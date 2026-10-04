import os
import time
import json
import re
from typing import Any, Callable, Optional

import crew_llms  # noqa: F401  (charge .env et règle LiteLLM AVANT l'import de crewai)
from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, task
from logs import get_logger
import crew_checks
import crew_guardrails
import crew_retry
import crew_run
import crew_tools
import qualification
import llm_limiter
from tools import check_syntax

log = get_logger("crew")
from github_write import github_create_branch, github_write_file, github_write_files
from github_read import github_read_file, github_list_directory
from agent_metrics import (  # noqa: F401
    ExecutionMetrics,
    _current_metrics,
    register_event_listeners,
    track_execution_metrics,
)

# --- MÉTRIQUES & PAUSES ---
# ExecutionMetrics, _current_metrics et track_execution_metrics vivent dans agent_metrics.py (mesure
# par agent à partir des événements CrewAI) ; réexportés ici car execution.py les importe d'ici.
register_event_listeners()


# Sous TOUS les appels au modèle (agents, planificateur/observateur de CrewAI, qualification, résumé) : plafond commun
# de requêtes par minute et pause commune après un 429 (voir llm_limiter.py). Le max_rpm d'un crew, lui, ne couvre ni
# les autres exécutions en parallèle ni les appels internes de CrewAI.
llm_limiter.install(on_wait=crew_retry._record_limiter_wait)


# --- CREW BASE ---
@CrewBase
class AppDevelopmentCrew(crew_checks.CrewChecksMixin, crew_tools.CrewToolsMixin):
    # str au chargement, remplacé par CrewBase par le dict lu dans le YAML.
    agents_config: Any = 'agentsquestion.yaml'
    tasks_config: Any = 'tasksquestion.yaml'

    @agent
    def qualification_agent(self) -> Agent:
        return Agent(config=self.agents_config['qualification_agent'], tools=[], llm=crew_llms.qualification_llm, max_iter=2, verbose=True)

    @agent
    def product_designer_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['product_designer_agent'],
            # Sans file_write_tool : son livrable est le texte de sa réponse (sauvegardé via
            # output_file), un outil d'écriture ne faisait que le distraire de son raisonnement.
            tools=[self._build_local_read_tool(), github_read_file, github_list_directory],
            llm=crew_llms.designer_llm, max_iter=3, verbose=True,
        )

    @agent
    def architect_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['architect_agent'],
            tools=[self._build_local_read_tool(), github_read_file, github_list_directory],
            llm=crew_llms.architect_llm, max_iter=5, verbose=True,
        )

    @agent
    def diagnostic_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['diagnostic_agent'],
            # Lecture SEULE, volontairement : aucun outil d'écriture (ni GitHub ni disque local),
            # pour qu'il soit STRUCTURELLEMENT impossible que cet agent committe quoi que ce soit,
            # même halluciné — contrairement à une simple consigne de prompt, un outil absent ne
            # peut pas être "oublié" par le LLM. Voir developer_agent plus bas : la séparation
            # entre lecture/diagnostic (ici) et écriture (là-bas) donne à CHACUNE un budget
            # d'itérations qui lui est propre, qu'aucune des deux ne peut faire empiéter sur
            # l'autre (auparavant une seule tâche/agent partageait un budget unique entre les
            # deux, et un diagnostic un peu long pouvait épuiser tout le budget avant même le
            # premier commit — voir l'historique de max_iter sur developer_agent ci-dessous).
            tools=[self._build_local_read_tool(), github_read_file, github_list_directory],
            llm=crew_llms.diagnostic_llm, max_iter=7, verbose=True,
            # Planification interne (hypothèses, lectures à faire) avant d'agir : l'agent le plus critique du pipeline,
            # dont tout le code livré dépend. Un seul appel de plan ; en effort « low », l'observation de chaque étape
            # se fait par heuristique, SANS appel LLM. L'ancien `reasoning=True` (effort « medium ») ajoutait un appel
            # d'observation par étape, un replan complet sur échec (jusqu'à 3) et jusqu'à 15 itérations par étape, hors
            # `max_rpm` (d'après le code de CrewAI) : une source importante des 429 Gemini. Contrepartie : une étape en
            # échec n'est plus replanifiée (l'heuristique la marque terminée) ; les contrôles de qualité et la seconde
            # tentative automatique restent le filet. Réglable sans déploiement : DIAGNOSTIC_REASONING_EFFORT et
            # crew_llms.DIAGNOSTIC_STEP_MAX_ITERATIONS.
            planning_config=crew_llms._diagnostic_planning_config(),
        )

    @agent
    def developer_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['developer_agent'],
            # Écriture SEULE, volontairement : aucun outil de lecture (ni github_read_file ni
            # read_a_files_content), pour qu'il soit STRUCTURELLEMENT impossible que cet agent
            # "redécouvre" un diagnostic ou relise indéfiniment au lieu de committer — le
            # diagnostic et le code complet lui arrivent déjà tout prêts via le contexte de
            # diagnostic_task (process séquentiel CrewAI). Voir diagnostic_agent ci-dessus pour
            # le raisonnement complet de cette séparation.
            # Pas de github_edit_file ici, volontairement : cet outil remplace un extrait exact
            # (old_string/new_string) et, sur échec (occurrences 0 ou >1), son propre message
            # d'erreur (voir github_edit_failures.py, _record_edit_failure) instruit l'agent appelant de
            # RELIRE le fichier avec github_read_file avant de retenter — un outil que cet agent
            # n'a justement plus (voir plus haut). diagnostic_task ne produit d'ailleurs jamais de
            # old_string/new_string, seulement le contenu complet de chaque fichier : github_write_file/
            # github_write_files (qui n'ont besoin d'aucune lecture préalable) couvrent donc tous les
            # cas réels de cette tâche.
            # github_commit_analyst_files en premier : il committe les fichiers déjà extraits
            # en Python de la sortie de l'Analyste (voir _build_commit_analyst_files_tool), sans
            # que le LLM ait à recopier leur contenu — github_write_file(s) restent le repli.
            tools=[
                # Sans file_write_tool : en local, github_commit_analyst_files écrit dans l'espace
                # de travail dédié (crew_workspace.LOCAL_WORKSPACE_DIR) ; file_write_tool écrirait n'importe où,
                # y compris sur le code du serveur.
                self._build_commit_analyst_files_tool(),
                check_syntax,
                github_create_branch, github_write_file, github_write_files,
                self._build_open_pull_request_tool(),
            ],
            # 6 (pas 8) : depuis la séparation avec diagnostic_agent (voir ci-dessus), cette tâche
            # n'a plus AUCUN diagnostic à faire, seulement à committer un code déjà rédigé — le
            # flux GitHub complet pour un BUGFIX (create_branch, write_file(s), check_syntax,
            # github_open_delivery_pull_request) tient en 4 appels ; 6 laisse une marge raisonnable sans jamais
            # retomber au niveau d'avant cette séparation, qui devait aussi couvrir un diagnostic
            # entier dans le même budget.
            llm=crew_llms.developer_llm, max_iter=6, verbose=True,
        )

    @agent
    def qa_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['qa_agent'],
            # Sans file_write_tool : la QA vérifie, elle n'écrit jamais. qa_verify_delivered_files
            # compare en un seul appel chaque fichier de l'Analyste à la branche (présence, diff
            # exact, check_syntax), ce qui libère le budget pour la conformité fonctionnelle.
            tools=[
                self._build_qa_verify_tool(),
                self._build_local_read_tool(), check_syntax, github_read_file, github_list_directory,
            ],
            # 10 et non 5 : lire un fichier PUIS le vérifier avec check_syntax est déjà 2 appels
            # par fichier modifié, avant même le rapport final — 5 ne couvrait donc que ~2
            # fichiers. Depuis github_write_files (voir developer_agent), development_task peut
            # committer des lots bien plus larges en un seul appel ; qa_task reste volontairement
            # instruite à ne PAS exiger une vérification exhaustive de chaque fichier d'un gros lot
            # (elle doit alors signaler explicitement lesquels restent non vérifiés, voir
            # tasksquestion.yaml), donc ce budget n'a pas besoin de suivre la taille du lot — juste
            # d'en couvrir davantage qu'avant sans pour autant viser l'exhaustivité.
            llm=crew_llms.qa_llm, max_iter=10, verbose=True,
        )

    @task
    def design_task(self) -> Task:
        return Task(config=self.tasks_config['design_task'], agent=self.product_designer_agent(), output_file='docs/specs_design.md',
                    guardrail=crew_guardrails._design_spec_guardrail, guardrail_max_retries=0)

    @task
    def architecture_task(self) -> Task:
        return Task(config=self.tasks_config['architecture_task'], agent=self.architect_agent(), output_file='docs/architecture_spec.md',
                    guardrail=crew_guardrails._architecture_guardrail, guardrail_max_retries=0)

    @task
    def diagnostic_task(self) -> Task:
        return Task(
            config=self.tasks_config['diagnostic_task'], agent=self.diagnostic_agent(),
            output_file='docs/diagnostic_plan.md',
            guardrail=self._diagnostic_guardrail, guardrail_max_retries=1,
        )

    @task
    def development_task(self) -> Task:
        return Task(config=self.tasks_config['development_task'], agent=self.developer_agent(), output_file='docs/implementation_log.md',
                    guardrail=self._development_report_guardrail, guardrail_max_retries=0)

    @task
    def qa_task(self) -> Task:
        return Task(
            config=self.tasks_config['qa_task'], agent=self.qa_agent(),
            output_file='tests/reports/qa_report.md',
            guardrail=self._qa_report_guardrail, guardrail_max_retries=0,
        )

    # --- Outils et guardrails propres à UNE exécution ---
    # Ils lisent self._analyst_files, rempli par _diagnostic_guardrail et remis à zéro au début
    # de chaque run_dynamic_crew. Portée par instance (et non un global) : execution.py crée un
    # AppDevelopmentCrew() dédié à chaque exécution, donc deux conversations concurrentes ne
    # partagent jamais ces fichiers. Pas de ContextVar non plus : CrewAI peut exécuter les
    # outils dans un pool de threads qui ne recopie pas le contexte courant.


    @crew_retry.retry_on_rate_limit_async(max_retries=5, base_delay=12.0)
    async def analyze_user_request(self, user_prompt: str, conversation_context: str = "", has_repo_target: bool = False) -> qualification.QualificationResult:
        qualif_agent = self.qualification_agent()
        task_prompt = qualification._build_qualification_prompt(user_prompt, conversation_context, has_repo_target)
        analysis_task = Task(description=task_prompt, expected_output="Schéma JSON AnalysisReport.", agent=qualif_agent, output_pydantic=qualification.AnalysisReport)
        analysis_crew = Crew(agents=[qualif_agent], tasks=[analysis_task], process=Process.sequential, verbose=False)
        
        # Exécution asynchrone pour éviter l'erreur d'event loop
        result = await analysis_crew.kickoff_async()
        crew_retry.quota_mgr.last_execution_time = time.time()

        if hasattr(result, 'pydantic') and result.pydantic is not None:
            return qualification.QualificationResult(**qualification._enforce_confidence_threshold(result.pydantic).model_dump())

        raw_output = str(result.raw) if hasattr(result, 'raw') else str(result)
        try:
            match = re.search(r'\{.*\}', raw_output, re.DOTALL)
            if match:
                report = qualification._coerce_analysis_report(json.loads(match.group(0)))
                if report is not None:
                    return qualification.QualificationResult(**qualification._enforce_confidence_threshold(report).model_dump())
        except Exception:
            pass

        # Qualification impossible : on demande plutôt que de lancer au hasard le workflow le
        # plus long (DESIGN_AND_DEV, 5 agents) sur une catégorie que rien ne justifie.
        return qualification.QualificationResult(fallback=True, **qualification._enforce_confidence_threshold(qualification.AnalysisReport(
            summary=f"Analyse : {user_prompt}",
            reasoning="La qualification automatique n'a pas produit de résultat exploitable.",
            request_type="DESIGN_AND_DEV",
            confidence=0.0,
            is_clear=False,
        )).model_dump())

    def save_analysis_report(self, report: qualification.AnalysisReport, user_prompt: str, filepath: str = "docs/qualification_report.md"):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        q_text = "\n".join([f"- {q}" for q in report.questions]) if report.questions else "_Aucune question._"
        md_content = f"# Rapport\n\n**Demande:** {user_prompt}\n\n### Synthèse\n{report.summary}\n\n### Workflow\n- Clear: {report.is_clear}\n- Type: {report.request_type}\n\n### Questions\n{q_text}"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md_content)

    async def run_dynamic_crew(self, inputs: dict, request_type: str, on_step_change: Optional[Callable[[Optional[str]], None]] = None, on_task_output_complete: Optional[Callable[[str, str, Optional[float]], None]] = None, resume_outputs: Optional[dict[str, str]] = None, scope: Optional[str] = None):
        """Exécute les étapes du workflow `request_type` (voir crew_run.CrewRun) et renvoie le résultat formaté."""
        run = crew_run.CrewRun(self, inputs, request_type, on_step_change, on_task_output_complete, resume_outputs, scope)
        return await run.execute()
