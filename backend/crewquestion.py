import os
import sys
import time
import asyncio
import json
import re
import functools
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from pathlib import Path
from typing import List, Literal, Callable, Any
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# --- CHARGEMENT DES VARIABLES D'ENVIRONNEMENT ---
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Configuration LiteLLM & Quotas
os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY or ""
os.environ["LITELLM_NUM_RETRIES"] = "7"
os.environ["LITELLM_TIME_CONTINUOUS_BACKOFF"] = "2"

from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import FileWriterTool
from tools import read_a_files_content, check_syntax
from github_tools import (
    github_read_file,
    github_list_directory,
    github_create_branch,
    github_write_file,
    github_edit_file,
    github_open_pull_request,
)

# --- MÉTRIQUES & PAUSES ---
class ExecutionMetrics:
    def __init__(self):
        self.start_time = time.time()
        self.api_calls_count = 0
        self.rate_limit_hits = 0
        self.total_wait_time = 0.0

    def record_call(self):
        self.api_calls_count += 1

    def record_rate_limit(self, wait_seconds: float):
        self.rate_limit_hits += 1
        self.total_wait_time += wait_seconds

    def record_wait(self, wait_seconds: float):
        self.total_wait_time += wait_seconds

# Métriques de l'exécution actuellement suivie (voir track_execution_metrics), pour
# renvoyer à l'utilisateur le coût/la performance de SA requête plutôt qu'un compteur
# global cumulé depuis le démarrage du serveur et partagé entre tous les utilisateurs.
# contextvars (et non un simple global) car correctement isolé entre requêtes concurrentes,
# et propagé automatiquement dans un thread lancé via asyncio.to_thread (utilisé par
# kickoff_async). Attention : loop.run_in_executor() nu ne copie PAS ce contexte tout
# seul (cf. _generate_summary, qui doit le faire explicitement via copy_context().run(...)
# pour son pool dédié) — ne pas supposer que la propagation est automatique partout.
_current_metrics: ContextVar["ExecutionMetrics | None"] = ContextVar("current_metrics", default=None)

@contextmanager
def track_execution_metrics():
    """Active un ExecutionMetrics dédié le temps du bloc, à lire une fois celui-ci terminé."""
    m = ExecutionMetrics()
    token = _current_metrics.set(m)
    try:
        yield m
    finally:
        _current_metrics.reset(token)

def retry_on_rate_limit_async(max_retries: int = 5, base_delay: float = 10.0):
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            retries = 0
            while True:
                m = _current_metrics.get()
                if m is not None:
                    m.record_call()
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    err_msg = str(e).lower()
                    is_retryable = any(marker in err_msg for marker in (
                        "429", "resource_exhausted", "rate limit", "quota",
                        "503", "unavailable", "high demand", "overloaded",
                    ))
                    if is_retryable:
                        retries += 1
                        if retries > max_retries:
                            raise e
                        match = re.search(r'retry after (\d+(\.\d+)?)', err_msg)
                        wait_time = float(match.group(1)) + 2.0 if match else base_delay * (2 ** (retries - 1))
                        if m is not None:
                            m.record_rate_limit(wait_time)
                        await asyncio.sleep(wait_time)
                    else:
                        raise e
        return wrapper
    return decorator

class QuotaManager:
    def __init__(self):
        self.last_execution_time = 0.0
        self.min_interval_seconds = 5.0

    def adaptive_pause(self, task_output=None):
        elapsed = time.time() - self.last_execution_time
        if elapsed < self.min_interval_seconds:
            wait_time = self.min_interval_seconds - elapsed
            m = _current_metrics.get()
            if m is not None:
                m.record_wait(wait_time)
            time.sleep(wait_time)
        self.last_execution_time = time.time()

quota_mgr = QuotaManager()

class CrewStepError(Exception):
    """Erreur levée quand le crew échoue à une étape précise (voir run_dynamic_crew).

    Conserve le message de l'exception d'origine (str(e) identique) pour que
    retry_on_rate_limit_async continue de détecter les erreurs de quota/rate-limit
    normalement, tout en exposant l'étape et l'agent en cours au moment de l'échec.
    """
    def __init__(self, step_index: int, total_steps: int, agent_role: str, original: Exception):
        self.step_index = step_index
        self.total_steps = total_steps
        self.agent_role = agent_role.strip()
        super().__init__(str(original))

def _iter_task_sections(result):
    """Génère (agent_name, raw) pour chaque tâche exécutée.

    Logique de repli partagée entre _format_crew_result (affichage) et
    _build_summary_input (résumé), pour que les deux ne puissent pas diverger
    silencieusement en n'étant corrigés que d'un seul côté.
    """
    tasks_output = getattr(result, "tasks_output", None) or []
    for task_output in tasks_output:
        agent_name = (getattr(task_output, "agent", None) or "Agent").strip()
        raw = getattr(task_output, "raw", None)
        raw = raw if raw is not None else str(task_output)
        yield agent_name, raw

def _format_crew_result(result) -> str:
    """Combine les sorties de toutes les tâches exécutées, pas seulement la dernière.

    result.raw ne reflète que la sortie de la dernière tâche du crew. Pour un workflow
    à plusieurs tâches (ex: ANALYSE_ONLY = design_task puis architecture_task), le
    contenu produit par les tâches précédentes serait sinon silencieusement perdu et
    jamais renvoyé à l'utilisateur.
    """
    tasks_output = getattr(result, "tasks_output", None)
    if not tasks_output or len(tasks_output) <= 1:
        return str(result.raw) if hasattr(result, "raw") else str(result)

    sections = [f"## {agent_name}\n\n{raw}" for agent_name, raw in _iter_task_sections(result)]
    return "\n\n---\n\n".join(sections)

# --- PYDANTIC MODEL & LLM ---
class AnalysisReport(BaseModel):
    summary: str = Field(description="Résumé en 2-3 phrases de ce que l'agent a compris de la demande.")
    is_clear: bool = Field(description="Vrai si la demande est claire, Faux si des ambiguïtés existent.")
    request_type: Literal["ANALYSE_ONLY", "BUGFIX", "FEATURE", "DESIGN_AND_DEV"] = Field(
        description="Type de workflow à déclencher."
    )
    questions: List[str] = Field(default_factory=list, description="Liste de 2 à 4 questions si la demande est floue.")

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini/gemini-3.5-flash-lite")
gemini_llm = LLM(model=MODEL_NAME, api_key=GEMINI_API_KEY, temperature=0.7, request_timeout=120)
file_write_tool = FileWriterTool()

# Marqueur inséré avant la section de résumé, pour que le frontend puisse la séparer
# du reste sans ambiguïté (voir parseCrewResult.ts). Un simple titre "## Résumé" pourrait
# apparaître naturellement dans le rapport d'un agent (ex: sa propre sous-section de
# conclusion) ; ce commentaire HTML, lui, n'a aucune raison d'être produit par un agent.
SUMMARY_SENTINEL = "<!--crew-summary-->"

# Borne la taille du texte envoyé au modèle pour la synthèse : le résultat combiné peut
# contenir du code source complet (workflows FEATURE/DESIGN_AND_DEV), et ce résumé n'est
# qu'un ajout de confort qui ne justifie pas de peser significativement sur le quota
# Gemini déjà sous tension (cf. adaptive_pause/retry_on_rate_limit_async ci-dessus).
MAX_SUMMARY_INPUT_CHARS = 6000

# Timeout dédié, plus court que celui des agents (120s) : un résumé qui traîne ne doit
# pas ajouter jusqu'à 2 minutes à une réponse dont le vrai travail est déjà terminé.
summary_llm = LLM(model=MODEL_NAME, api_key=GEMINI_API_KEY, temperature=0.5, request_timeout=20)

MAX_SUMMARY_REQUEST_CHARS = 1500

# Pool dédié et volontairement petit : asyncio.wait_for peut abandonner l'attente d'un
# appel bloqué (ex: litellm qui enchaîne ses propres tentatives internes bien au-delà de
# SUMMARY_WALL_CLOCK_TIMEOUT) sans pouvoir arrêter le thread sous-jacent. En isolant ces
# threads orphelins potentiels dans un pool à part, une panne prolongée de Gemini ne peut
# jamais épuiser le pool par défaut dont dépend le reste de l'application. Ce pool dédié
# peut lui-même se retrouver saturé le temps que litellm abandonne ses propres tentatives
# (borné par LITELLM_NUM_RETRIES/le backoff, pas indéfini) : dans ce cas les résumés sont
# simplement absents pendant cette fenêtre, sans jamais affecter le résultat des agents.
_summary_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="crew-summary")

def _build_summary_input(result) -> str:
    """Texte borné donné en entrée au résumé, avec un budget de troncature réparti
    à parts égales entre les tâches plutôt qu'une simple troncature globale : sur un
    workflow à plusieurs tâches (ex: FEATURE), une troncature globale ne garderait que
    le début (architecture) et perdrait entièrement le code produit et l'avis QA, qui
    sont pourtant l'essentiel de ce qui a été livré.
    """
    sections = list(_iter_task_sections(result))
    if not sections:
        raw = getattr(result, "raw", None)
        text = raw if raw is not None else str(result)
        return text[:MAX_SUMMARY_INPUT_CHARS]

    per_task_budget = max(MAX_SUMMARY_INPUT_CHARS // len(sections), 500)
    parts = []
    for agent_name, raw in sections:
        truncated = raw[:per_task_budget]
        if len(raw) > per_task_budget:
            truncated += " [...tronqué...]"
        parts.append(f"## {agent_name}\n{truncated}")
    return "\n\n".join(parts)

def _build_summary_prompt(user_request: str, summary_input: str) -> str:
    # user_request vient du texte libre saisi par l'utilisateur (aucune limite de
    # longueur côté frontend) : le borner évite qu'une saisie très longue fasse, à elle
    # seule, dépasser le budget de taille que ce résumé est censé respecter.
    truncated_request = user_request[:MAX_SUMMARY_REQUEST_CHARS]
    return (
        "Voici le résultat produit par une équipe d'agents IA pour répondre à la "
        f"demande suivante :\n\n{truncated_request}\n\n"
        f"Résultat complet :\n---\n{summary_input}\n---\n\n"
        "Rédige, en français, un résumé de 3 à 5 phrases clair et concret de ce qui a "
        "été livré (décisions clés, ce qui a été produit). N'invente rien qui ne soit "
        "pas déjà présent dans le résultat ci-dessus."
    )

# Borne le temps d'attente total (au-delà du request_timeout de summary_llm lui-même),
# car LITELLM_NUM_RETRIES=7 (défini plus haut, process-wide) s'applique aussi à cet
# appel : sans ce filet, une erreur transitoire pourrait déclencher jusqu'à 7 tentatives
# internes avant que summary_llm.call() ne lève enfin, contredisant l'objectif même
# d'un résumé qui ne doit jamais faire attendre longtemps une réponse déjà acquise.
SUMMARY_WALL_CLOCK_TIMEOUT = 25

async def _generate_summary(user_request: str, result) -> str | None:
    """Résumé de synthèse ajouté en fin de résultat combiné.

    Best-effort : un échec ici (quota, timeout...) ne doit jamais faire échouer
    l'exécution, dont le résultat des agents est déjà acquis à ce stade. Volontairement
    sans retry applicatif : ce n'est qu'un ajout de confort, pas la livraison principale.
    """
    def _call() -> str:
        quota_mgr.adaptive_pause()
        return summary_llm.call(_build_summary_prompt(user_request, _build_summary_input(result)))

    try:
        loop = asyncio.get_running_loop()
        # loop.run_in_executor() ne copie PAS automatiquement le contexte courant dans le
        # thread (contrairement à asyncio.to_thread, qui le fait mais impose son propre
        # executor par défaut) : sans ce copy_context().run(...) explicite, l'appel à
        # quota_mgr.adaptive_pause() dans _call() perdrait de vue le _current_metrics de
        # CETTE requête (il verrait la valeur par défaut, None), et le temps d'attente
        # de cet appel ne serait jamais comptabilisé dans les métriques renvoyées.
        ctx = copy_context()
        text = await asyncio.wait_for(
            loop.run_in_executor(_summary_executor, ctx.run, _call), timeout=SUMMARY_WALL_CLOCK_TIMEOUT
        )
        return text.strip() or None
    except Exception as e:
        print(f"Génération du résumé ignorée : {type(e).__name__}: {e}")
        return None

MAX_PRIOR_TURN_SUMMARY_CHARS = 800
MAX_PRIOR_TURN_RESULT_CHARS = 500

def _extract_prior_turn_summary(result_text: str) -> str:
    """Réduit le résultat d'un tour précédent à un texte court utilisable comme contexte.

    Réutilise le "## Résumé" déjà généré pour ce tour (via SUMMARY_SENTINEL) quand il
    existe : c'est déjà une synthèse pensée pour être lue, pas le rapport complet de
    chaque agent. À défaut (résumé absent, ex: génération échouée), on retombe sur un
    simple tronquage du résultat brut plutôt que de ne rien montrer.
    """
    if not result_text:
        return ""

    idx = result_text.rfind(SUMMARY_SENTINEL)
    if idx != -1:
        tail = result_text[idx + len(SUMMARY_SENTINEL):].strip()
        heading_match = re.match(r"^##\s+.+?\s*\n+(.*)", tail, re.DOTALL)
        summary_text = heading_match.group(1) if heading_match else tail
        return summary_text.strip()[:MAX_PRIOR_TURN_SUMMARY_CHARS]

    return result_text.strip()[:MAX_PRIOR_TURN_RESULT_CHARS]

MAX_PRIOR_TURNS_IN_CONTEXT = 10

def build_conversation_context(prior_entries) -> str:
    """Rappel textuel des tours précédents de cette conversation, donné en entrée aux
    tâches (voir tasksquestion.yaml, placeholder {conversation_context}).

    Chaque appel à run_dynamic_crew part d'un crew neuf, sans aucune connaissance de ce
    qui a été demandé/livré aux tours précédents du même fil de discussion : sans ce
    rappel, un message de suivi ("ajoute aussi Y") ne peut pas être compris comme une
    continuation de ce qui précède. Ne garde que les MAX_PRIOR_TURNS_IN_CONTEXT derniers
    tours (les plus pertinents pour un message de suivi) : sans cette borne, une longue
    conversation ferait grossir sans limite le texte injecté dans chaque tâche, à
    l'inverse du soin apporté ailleurs dans ce fichier à borner la taille des prompts
    (MAX_SUMMARY_INPUT_CHARS, troncature par tâche).
    """
    if not prior_entries:
        return "Aucun échange précédent dans cette conversation."

    recent_entries = prior_entries[-MAX_PRIOR_TURNS_IN_CONTEXT:]
    status_labels = {"success": "réussi", "failed": "échoué", "running": "en cours (probablement interrompu)"}
    lines = []
    if len(prior_entries) > len(recent_entries):
        lines.append(f"[{len(prior_entries) - len(recent_entries)} tour(s) plus ancien(s) omis pour rester concis]")
    for entry in recent_entries:
        status_label = status_labels.get(entry.status, entry.status)
        lines.append(f'- Demande : "{entry.user_request.strip()[:200]}" ({entry.workflow}, {status_label})')
        if entry.status == "success" and entry.result:
            summary = _extract_prior_turn_summary(entry.result)
            if summary:
                lines.append(f"  Résultat : {summary}")
    return "\n".join(lines)

# --- CREW BASE ---
@CrewBase
class AppDevelopmentCrew():
    agents_config = 'agentsquestion.yaml'
    tasks_config = 'tasksquestion.yaml'

    @agent
    def qualification_agent(self) -> Agent:
        return Agent(config=self.agents_config['qualification_agent'], tools=[], llm=gemini_llm, max_iter=2, verbose=True)

    @agent
    def product_designer_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['product_designer_agent'],
            tools=[read_a_files_content, file_write_tool, github_read_file, github_list_directory],
            llm=gemini_llm, max_iter=3, verbose=True,
        )

    @agent
    def architect_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['architect_agent'],
            tools=[read_a_files_content, file_write_tool, github_read_file, github_list_directory],
            llm=gemini_llm, max_iter=3, verbose=True,
        )

    @agent
    def developer_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['developer_agent'],
            tools=[
                read_a_files_content, file_write_tool, check_syntax,
                github_read_file, github_list_directory,
                github_create_branch, github_write_file, github_edit_file, github_open_pull_request,
            ],
            # 5 et non 3 : le flux GitHub complet (create_branch, write/edit_file,
            # check_syntax, open_pull_request) dépasse déjà 3 appels d'outils pour un
            # seul fichier ; max_iter=3 coupait la tâche avant l'ouverture de la PR.
            llm=gemini_llm, max_iter=5, verbose=True,
        )

    @agent
    def qa_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['qa_agent'],
            tools=[read_a_files_content, file_write_tool, check_syntax, github_read_file, github_list_directory],
            # Idem developer_agent : lire un fichier PUIS le vérifier avec check_syntax
            # est déjà 2 appels par fichier modifié, avant même le rapport final.
            llm=gemini_llm, max_iter=5, verbose=True,
        )

    @task
    def qualification_task(self) -> Task:
        return Task(config=self.tasks_config['qualification_task'], agent=self.qualification_agent(), output_file='docs/qualification_report.md')

    @task
    def design_task(self) -> Task:
        return Task(config=self.tasks_config['design_task'], agent=self.product_designer_agent(), output_file='docs/specs_design.md')

    @task
    def architecture_task(self) -> Task:
        return Task(config=self.tasks_config['architecture_task'], agent=self.architect_agent(), output_file='docs/architecture_spec.md')

    @task
    def development_task(self) -> Task:
        return Task(config=self.tasks_config['development_task'], agent=self.developer_agent(), output_file='docs/implementation_log.md')

    @task
    def qa_task(self) -> Task:
        return Task(config=self.tasks_config['qa_task'], agent=self.qa_agent(), output_file='tests/reports/qa_report.md')

    @retry_on_rate_limit_async(max_retries=5, base_delay=12.0)
    async def analyze_user_request(self, user_prompt: str) -> AnalysisReport:
        qualif_agent = self.qualification_agent()
        task_prompt = f"""
        Tu es le Spécialiste en Qualification / Senior Product Owner.
        Voici la demande : "{user_prompt}"
        Remplis le rapport JSON structuré : summary, is_clear, request_type, questions.
        """
        analysis_task = Task(description=task_prompt, expected_output="Schéma JSON AnalysisReport.", agent=qualif_agent, output_pydantic=AnalysisReport)
        analysis_crew = Crew(agents=[qualif_agent], tasks=[analysis_task], process=Process.sequential, verbose=False)
        
        # Exécution asynchrone pour éviter l'erreur d'event loop
        result = await analysis_crew.kickoff_async()
        quota_mgr.last_execution_time = time.time()

        if hasattr(result, 'pydantic') and result.pydantic is not None:
            return result.pydantic

        raw_output = str(result.raw) if hasattr(result, 'raw') else str(result)
        try:
            match = re.search(r'\{.*\}', raw_output, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                return AnalysisReport(
                    summary=data.get("summary", "Analyse effectuée."),
                    is_clear=bool(data.get("is_clear", True)),
                    request_type=data.get("request_type", "DESIGN_AND_DEV"),
                    questions=data.get("questions", [])
                )
        except Exception:
            pass

        return AnalysisReport(summary=f"Analyse : {user_prompt}", is_clear=True, request_type="DESIGN_AND_DEV", questions=[])

    def save_analysis_report(self, report: AnalysisReport, user_prompt: str, filepath: str = "docs/qualification_report.md"):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        q_text = "\n".join([f"- {q}" for q in report.questions]) if report.questions else "_Aucune question._"
        md_content = f"# Rapport\n\n**Demande:** {user_prompt}\n\n### Synthèse\n{report.summary}\n\n### Workflow\n- Clear: {report.is_clear}\n- Type: {report.request_type}\n\n### Questions\n{q_text}"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md_content)

    @retry_on_rate_limit_async(max_retries=5, base_delay=15.0)
    async def run_dynamic_crew(self, inputs: dict, request_type: str):
        if request_type == "ANALYSE_ONLY":
            selected_tasks = [self.design_task(), self.architecture_task()]
        elif request_type == "BUGFIX":
            selected_tasks = [self.development_task(), self.qa_task()]
        elif request_type == "FEATURE":
            selected_tasks = [self.architecture_task(), self.development_task(), self.qa_task()]
        else:
            selected_tasks = [self.design_task(), self.architecture_task(), self.development_task(), self.qa_task()]

        selected_agents = list({task.agent for task in selected_tasks})

        completed_count = 0

        def on_task_complete(task_output):
            nonlocal completed_count
            completed_count += 1
            quota_mgr.adaptive_pause(task_output)

        dynamic_crew = Crew(
            agents=selected_agents,
            tasks=selected_tasks,
            process=Process.sequential,
            task_callback=on_task_complete,
            max_rpm=3,
            output_log_file='crew_execution.log',
            verbose=True
        )
        try:
            result = await dynamic_crew.kickoff_async(inputs=inputs)
        except Exception as e:
            # Identifie la tâche qui était en cours au moment de l'échec (celle juste
            # après la dernière complétée avec succès) pour que le frontend puisse
            # afficher "échec pendant X" plutôt qu'une erreur générique. Si toutes les
            # tâches ont déjà déclenché leur callback (échec après coup, ex: pendant
            # l'agrégation du résultat par crewai), on ne dépasse pas total_steps.
            if completed_count < len(selected_tasks):
                step_index = completed_count + 1
                agent_role = selected_tasks[completed_count].agent.role
            else:
                step_index = len(selected_tasks)
                agent_role = "finalisation du résultat"
            raise CrewStepError(step_index, len(selected_tasks), agent_role, e) from e

        formatted = _format_crew_result(result)

        # last_execution_time n'est délibérément pas remis à jour avant cet appel :
        # on_task_complete() (task_callback ci-dessus) l'a déjà fait à la fin de la
        # dernière tâche. Le remettre à `time.time()` ici ferait toujours mesurer un
        # écart quasi nul à adaptive_pause() dans _generate_summary, forçant une pause
        # maximale (5s) systématique au lieu d'une pause proportionnée au temps déjà
        # écoulé depuis le dernier appel Gemini réel.
        summary = await _generate_summary(inputs.get('user_request', ''), result)
        if summary:
            formatted = f"{formatted}\n\n{SUMMARY_SENTINEL}\n\n## Résumé\n\n{summary}"

        quota_mgr.last_execution_time = time.time()
        return formatted
