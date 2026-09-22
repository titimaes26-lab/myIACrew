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
from typing import List, Literal, Callable, Any, Optional
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
from crewai.project.utils import cache as _crewai_memoize_cache
from crewai_tools import FileWriterTool
from tools import read_a_files_content, check_syntax
from github_tools import (
    github_read_file,
    github_list_directory,
    github_create_branch,
    github_write_file,
    github_write_files,
    github_edit_file,
    github_open_pull_request,
    track_edit_failures,
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
# 25 et non 20 : le résumé demande désormais 4-6 phrases (au lieu de 3-5) plus, le cas
# échéant, la justification des choix (voir _build_summary_prompt), une génération
# légèrement plus longue qui reprenait la marge de cette valeur sans que celle-ci ait
# été ajustée en conséquence.
summary_llm = LLM(model=MODEL_NAME, api_key=GEMINI_API_KEY, temperature=0.5, request_timeout=25)

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

    Pour chaque tâche, garde le DÉBUT et la FIN de son rapport plutôt qu'un simple préfixe :
    un agent conclut typiquement son rapport par sa synthèse/justification ("pourquoi tel
    choix"), qu'un pur `raw[:budget]` couperait systématiquement en tout premier sur un
    rapport dépassant le budget, alors que le résumé demandé à summary_llm cherche justement
    ce genre de rationale (voir _build_summary_prompt).
    """
    sections = list(_iter_task_sections(result))
    if not sections:
        raw = getattr(result, "raw", None)
        text = raw if raw is not None else str(result)
        return text[:MAX_SUMMARY_INPUT_CHARS]

    per_task_budget = max(MAX_SUMMARY_INPUT_CHARS // len(sections), 500)
    parts = []
    for agent_name, raw in sections:
        if len(raw) <= per_task_budget:
            truncated = raw
        else:
            head_budget = per_task_budget * 2 // 3
            tail_budget = per_task_budget - head_budget
            truncated = f"{raw[:head_budget]} [...tronqué...] {raw[-tail_budget:]}"
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
        "Rédige, en français, un résumé de 4 à 6 phrases clair et concret de ce qui a été "
        "livré (décisions clés, ce qui a été produit) ET, quand cette justification est "
        "présente dans le résultat ci-dessus, du POURQUOI des choix importants qui ont été "
        "faits (ex: pourquoi tel découpage de composants, pourquoi telle approche plutôt "
        "qu'une autre). N'invente rien qui ne soit pas déjà présent dans le résultat "
        "ci-dessus : ni un fait (ex: un fichier livré qui ne l'a pas été), ni une raison "
        "absente — si le résultat ne justifie pas un choix, décris-le sans inventer de "
        "justification."
    )

# Borne le temps d'attente total (au-delà du request_timeout de summary_llm lui-même),
# car LITELLM_NUM_RETRIES=7 (défini plus haut, process-wide) s'applique aussi à cet
# appel : sans ce filet, une erreur transitoire pourrait déclencher jusqu'à 7 tentatives
# internes avant que summary_llm.call() ne lève enfin, contredisant l'objectif même
# d'un résumé qui ne doit jamais faire attendre longtemps une réponse déjà acquise.
# 30 et non 25 : doit rester strictement supérieur au request_timeout de summary_llm
# (25 désormais, voir plus haut) pour continuer à lui laisser le temps de lever sa
# propre erreur de timeout plutôt que d'être coupé par celui-ci en premier.
SUMMARY_WALL_CLOCK_TIMEOUT = 30

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

def _evict_memoized_cache_entries(crew_instance: Any) -> None:
    """Purge, best-effort, les entrées de crewai.project.utils.cache (voir son import plus haut)
    associées à `crew_instance`, une fois son exécution terminée.

    Les méthodes décorées @task/@agent de AppDevelopmentCrew sont mémoïsées par CrewAI dans ce
    dict module-level, clé par (nom de méthode, id(self)) — voir le commentaire sur
    crew_for_this_execution dans main.py, qui explique pourquoi chaque exécution instancie un
    AppDevelopmentCrew() DÉDIÉ (donc un id(self) distinct) plutôt que de réutiliser un singleton.
    Ce cache n'offre aucune API d'éviction publique et n'est JAMAIS purgé de lui-même : sans cet
    appel, chaque exécution y laisse une poignée d'entrées orphelines pour toujours, même une fois
    `crew_instance` elle-même devenue inaccessible et éligible au garbage collection — une fuite
    mémoire lente mais réelle, jusqu'ici seulement "acceptée" (voir ce même commentaire dans
    main.py, qui suggérait un redémarrage périodique du service comme seul filet de sécurité).
    Significatif sur un service à mémoire limitée (ex: plan gratuit Render, souvent 512 Mo)
    recevant de nombreuses exécutions sans redémarrage entretemps.

    Reste dans les entrailles PRIVÉES de crewai (cache._cache, un PrivateAttr Pydantic, structure
    non garantie stable d'une version à l'autre — requirements.txt n'épingle pas crewai) faute de
    mieux : best-effort et strictement défensif, une évolution future de cette structure interne
    ne doit jamais faire échouer une exécution par ailleurs saine, juste laisser le cache grossir
    comme avant ce correctif (dégradation silencieuse, pas une régression).

    Passe par cache._lock (le même RWLock que CacheHandler.add()/read() utilisent pour CE MÊME
    dict _cache) plutôt que d'y toucher directement : deux conversations DIFFÉRENTES peuvent
    s'exécuter concurremment (voir main.py, contrôle de concurrence limité à UNE conversation à la
    fois), donc la construction d'un AppDevelopmentCrew() pour l'une (qui appelle cache.add() sous
    ce verrou) peut survenir pendant que cette fonction itère ici sur _cache pour une autre — sans
    le même verrou, ce serait un dict modifié pendant son itération (RuntimeError), rattrapé par
    le except ci-dessous mais qui ferait échouer silencieusement la purge de cette exécution-là.
    """
    try:
        with _crewai_memoize_cache._lock.w_locked():
            internal_cache = _crewai_memoize_cache._cache
            # Repère la clé "__instance__" que _make_hashable (crewai/project/utils.py) produit
            # pour `self` : `("__instance__", id(self))`, dont str() rend exactement ce fragment —
            # présent tel quel dans la clé finale de cache quelle que soit la méthode mémoïsée.
            marker = f"'__instance__', {id(crew_instance)})"
            stale_keys = [k for k in internal_cache if marker in k]
            for k in stale_keys:
                del internal_cache[k]
    except Exception as e:
        # flush=True : sys.stdout est bufferisé par bloc une fois redirigé vers les logs Render
        # (pas un terminal) — voir main.py, _log_memory, qui applique la même garde partout pour
        # ne pas perdre le dernier diagnostic si le process se termine brutalement juste après.
        print(f"AVERTISSEMENT : échec du nettoyage du cache de mémoïsation CrewAI (best-effort, sans impact) : {type(e).__name__}: {e}", flush=True)

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
            tools=[read_a_files_content, github_read_file, github_list_directory],
            llm=gemini_llm, max_iter=5, verbose=True,
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
            tools=[
                file_write_tool, check_syntax,
                github_create_branch, github_write_file, github_write_files,
                github_edit_file, github_open_pull_request,
            ],
            # 6 (pas 8) : depuis la séparation avec diagnostic_agent (voir ci-dessus), cette tâche
            # n'a plus AUCUN diagnostic à faire, seulement à committer un code déjà rédigé — le
            # flux GitHub complet pour un BUGFIX (create_branch, write_file(s)/edit_file,
            # check_syntax, open_pull_request) tient en 4 appels, 6 laisse de la marge pour un
            # repli sur échec répété de github_edit_file (voir github_tools.py,
            # _record_edit_failure) sans jamais retomber au niveau d'avant cette séparation, qui
            # devait aussi couvrir un diagnostic entier dans le même budget.
            llm=gemini_llm, max_iter=6, verbose=True,
        )

    @agent
    def qa_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['qa_agent'],
            tools=[read_a_files_content, file_write_tool, check_syntax, github_read_file, github_list_directory],
            # 10 et non 5 : lire un fichier PUIS le vérifier avec check_syntax est déjà 2 appels
            # par fichier modifié, avant même le rapport final — 5 ne couvrait donc que ~2
            # fichiers. Depuis github_write_files (voir developer_agent), development_task peut
            # committer des lots bien plus larges en un seul appel ; qa_task reste volontairement
            # instruite à ne PAS exiger une vérification exhaustive de chaque fichier d'un gros lot
            # (elle doit alors signaler explicitement lesquels restent non vérifiés, voir
            # tasksquestion.yaml), donc ce budget n'a pas besoin de suivre la taille du lot — juste
            # d'en couvrir davantage qu'avant sans pour autant viser l'exhaustivité.
            llm=gemini_llm, max_iter=10, verbose=True,
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
    def diagnostic_task(self) -> Task:
        return Task(config=self.tasks_config['diagnostic_task'], agent=self.diagnostic_agent(), output_file='docs/diagnostic_plan.md')

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
    async def run_dynamic_crew(self, inputs: dict, request_type: str, on_step_change: Optional[Callable[[Optional[str]], None]] = None):
        # Clés alignées sur WORKFLOW_STEPS (frontend/src/constants/workflowSteps.ts) : c'est
        # ce que on_step_change transmet à main.py pour persister l'étape en cours (voir
        # ExecutionHistory.current_step), et le frontend s'attend exactement à ces 5 valeurs
        # pour faire correspondre la progression réelle à l'étape affichée dans StepIndicator.
        # 'diagnostic' précède toujours 'development' (jamais l'inverse) : diagnostic_task
        # (lecture seule, voir diagnostic_agent) produit le code complet que development_task
        # (écriture seule, voir developer_agent) committe ensuite tel quel — process séquentiel
        # CrewAI, donc development_task reçoit automatiquement la sortie de diagnostic_task en
        # contexte sans câblage explicite supplémentaire ici.
        if request_type == "ANALYSE_ONLY":
            selected = [('design', self.design_task()), ('architecture', self.architecture_task())]
        elif request_type == "BUGFIX":
            selected = [('diagnostic', self.diagnostic_task()), ('development', self.development_task()), ('qa', self.qa_task())]
        elif request_type == "FEATURE":
            selected = [('architecture', self.architecture_task()), ('diagnostic', self.diagnostic_task()), ('development', self.development_task()), ('qa', self.qa_task())]
        else:
            selected = [('design', self.design_task()), ('architecture', self.architecture_task()), ('diagnostic', self.diagnostic_task()), ('development', self.development_task()), ('qa', self.qa_task())]

        step_keys = [key for key, _ in selected]
        selected_tasks = [task for _, task in selected]
        selected_agents = list({task.agent for task in selected_tasks})

        completed_count = 0

        def on_task_complete(task_output):
            nonlocal completed_count
            completed_count += 1
            quota_mgr.adaptive_pause(task_output)
            # Annonce la tâche SUIVANTE qui démarre (pas celle qui vient de finir) : rien à
            # annoncer après la dernière (le résultat est ensuite juste agrégé/résumé, sans
            # étape agent supplémentaire pour l'utilisateur).
            if on_step_change is not None and completed_count < len(selected_tasks):
                on_step_change(step_keys[completed_count])

        if on_step_change is not None:
            # Rejoué à l'identique par retry_on_rate_limit_async (décorateur sur cette méthode)
            # si une erreur de quota/rate-limit survient plus loin : selected_tasks est
            # entièrement reconstruit à zéro à chaque nouvelle tentative (self.architecture_task()
            # etc. recréent des Task neufs), donc les tâches déjà terminées lors d'une tentative
            # précédente sont réellement refaites depuis le début, pas juste réaffichées. La
            # progression annoncée ici reflète donc fidèlement ce qui se passe réellement (retour
            # à la première étape), même si ça peut surprendre après avoir vu 'qa' s'afficher.
            #
            # await asyncio.to_thread(...) et non un appel direct : contrairement aux appels
            # suivants (déclenchés par task_callback depuis le thread d'arrière-plan de
            # kickoff_async, voir on_task_complete ci-dessus), CE point du code s'exécute encore
            # sur le thread de la boucle asyncio elle-même (avant le premier `await
            # kickoff_async`). on_step_change effectue une écriture DB synchrone bloquante (voir
            # _persist_current_step, main.py) : l'appeler ici directement bloquerait la boucle
            # asyncio, et donc TOUTES les autres requêtes concurrentes servies par ce même
            # worker, le temps de l'aller-retour réseau vers la base — à chaque exécution ET à
            # chaque nouvelle tentative sur rate-limit.
            await asyncio.to_thread(on_step_change, step_keys[0])

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
            # track_edit_failures() : isole le suivi des échecs répétés de github_edit_file
            # (voir github_tools.py) à CETTE exécution, pour qu'il ne se souvienne pas à tort
            # d'échecs d'un tour précédent sur le même work_branch réutilisé (voir la docstring
            # de _edit_failure_counts dans github_tools.py pour le raisonnement complet).
            with track_edit_failures():
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
            if on_step_change is not None:
                # Plus aucune étape n'est réellement en cours à cet instant, que cette erreur
                # finisse par être définitive OU rejouée par retry_on_rate_limit_async (dans ce
                # dernier cas, ce même appel repartira du tout début — voir plus haut) : entre
                # les deux, l'exécution est simplement à l'arrêt, potentiellement pendant
                # plusieurs minutes d'attente (retry_on_rate_limit_async attend jusqu'à 2^4 fois
                # base_delay avant de rejouer). Sans ce nettoyage, ExecutionHistory.current_step
                # resterait affiché comme "suivi en direct" (StepIndicator.tsx) sur la dernière
                # étape connue alors que rien n'est concrètement en train de s'exécuter — un
                # message "Échec à l'étape X/Y" bien plus précis (step_index/agent_role
                # ci-dessus) existe déjà pour indiquer où ça s'est arrêté (voir CrewStepError,
                # parseFailureDetail côté frontend), current_step n'a donc pas besoin de faire
                # doublon comme trace diagnostique.
                await asyncio.to_thread(on_step_change, None)
            raise CrewStepError(step_index, len(selected_tasks), agent_role, e) from e
        finally:
            # _evict_memoized_cache_entries(self) : self.design_task()/architecture_task()/
            # development_task()/qa_task() sont décorées @task par crewai, qui les MÉMOÏSE par
            # (nom de méthode, id(self)) dans un cache module-level SANS éviction native — voir
            # crewai/project/utils.py et la docstring de cette fonction. Purge ici, à la fin de
            # CETTE exécution, les entrées qu'elle y a laissées : sans ça, ce cache grossit sans
            # limite sur la durée de vie du process (une poignée d'entrées orphelines par
            # exécution, jamais nettoyées), un risque réel de mémoire sur un service à ressources
            # limitées (ex: plan gratuit Render) qui enchaîne de nombreuses exécutions.
            _evict_memoized_cache_entries(self)

            # t.callback = None : filet de sécurité résiduel, plus le mécanisme principal
            # maintenant que le cache est activement purgé ci-dessus. main.py instancie un
            # AppDevelopmentCrew() dédié à CHAQUE exécution (`crew_for_this_execution`, jamais le
            # singleton crew_instance) précisément pour que `self` ait un id() distinct à chaque
            # fois, donc des objets Task neufs — ce qui évite, dans l'immense majorité des cas,
            # qu'un rôle de tâche partagé entre deux exécutions (y compris deux conversations
            # DIFFÉRENTES en parallèle) ne partage aussi le même objet Task, et donc sa
            # `.callback`/`.output`. Ne couvre plus qu'un cas résiduel très improbable (éviction
            # ci-dessus qui aurait elle-même échoué ET CPython qui réutiliserait ensuite l'id()
            # d'une instance déjà garbage-collectée) : sans lui, une future exécution qui
            # obtiendrait par coïncidence CE MÊME id() récupérerait alors via le cache ces mêmes
            # objets Task, `.callback` non nettoyée incluse.
            for t in selected_tasks:
                t.callback = None

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
