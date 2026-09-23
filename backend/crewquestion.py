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
from crewai.tools import tool
from tools import check_syntax
from github_tools import (
    github_read_file,
    github_list_directory,
    github_create_branch,
    github_write_file,
    github_write_files,
    github_open_pull_request,
    _reject_invalid_syntax,
    make_file_fetcher,
    track_edit_failures,
    write_files_to_branch,
)
from analyst_output import (
    FILE_ABSENT,
    FILE_BLOCKS,
    NOT_DELIVERED_MARKER,
    PRESENT_UNREADABLE,
    build_delivery_report,
    format_manifest,
    normalize_path,
    review_diagnostic_output,
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
RequestType = Literal["ANALYSE_ONLY", "BUGFIX", "FEATURE", "DESIGN_AND_DEV"]

class AnalysisReport(BaseModel):
    # Ordre des champs volontaire : le modèle remplit le JSON dans cet ordre, donc il rédige sa
    # justification (reasoning) et envisage une alternative AVANT de trancher request_type, puis
    # évalue sa confiance APRÈS — plutôt que de choisir d'abord et de rationaliser ensuite.
    summary: str = Field(description="Résumé en 2-3 phrases de ce que l'agent a compris de la demande.")
    reasoning: str = Field(
        default="",
        description="Indices relevés dans la demande (et le contexte) et règle de la grille de décision appliquée.",
    )
    alternative_type: Optional[RequestType] = Field(
        default=None, description="Deuxième catégorie la plus plausible, ou null si aucune."
    )
    request_type: RequestType = Field(description="Type de workflow à déclencher.")
    # Obligatoire (pas de valeur par défaut) : un défaut à 1.0 ferait passer pour certaine une
    # réponse qui omet ce champ, sans jamais déclencher le seuil de clarification.
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confiance dans request_type, de 0 à 1 (sous 0.6 : la demande doit être clarifiée).",
    )
    is_clear: bool = Field(description="Vrai si la demande est claire, Faux si des ambiguïtés existent.")
    questions: List[str] = Field(default_factory=list, description="Liste de 2 à 4 questions si la demande est floue.")

class QualificationResult(AnalysisReport):
    """Réponse de /api/qualify : AnalysisReport + un indicateur EXPLICITE de repli. Séparé du
    modèle demandé au LLM (output_pydantic) pour que celui-ci ne puisse jamais le remplir."""
    fallback: bool = Field(
        default=False,
        description="Vrai si la qualification automatique a échoué : request_type n'est alors qu'un défaut.",
    )

# Sous ce seuil, la qualification est jugée trop incertaine pour lancer un workflow coûteux
# (jusqu'à 5 agents) sur une catégorie peut-être fausse : on pose plutôt des questions.
QUALIFICATION_CONFIDENCE_THRESHOLD = 0.6

_WORKFLOW_LABELS = {
    "ANALYSE_ONLY": "une analyse/des spécifications sans code",
    "BUGFIX": "la correction d'un bug",
    "FEATURE": "l'ajout d'une fonctionnalité à un projet existant",
    "DESIGN_AND_DEV": "la conception et le développement complets d'un nouveau produit",
}

def _enforce_confidence_threshold(report: AnalysisReport) -> AnalysisReport:
    """Force une clarification quand le modèle n'est pas assez sûr de sa catégorie, et garantit
    qu'une demande jugée floue s'accompagne toujours d'au moins une question à poser."""
    if report.confidence < QUALIFICATION_CONFIDENCE_THRESHOLD:
        report.is_clear = False
    if not report.is_clear and not report.questions:
        alternative = report.alternative_type if report.alternative_type != report.request_type else None
        if alternative:
            report.questions = [
                f"Attends-tu plutôt {_WORKFLOW_LABELS[report.request_type]} ou "
                f"{_WORKFLOW_LABELS[alternative]} ?"
            ]
        else:
            report.questions = [
                "Peux-tu préciser le résultat attendu : " + ", ".join(_WORKFLOW_LABELS.values()) + " ?"
            ]
    return report

_REQUEST_TYPES = set(RequestType.__args__)

def _as_bool(value: Any) -> bool:
    """bool() tel quel ferait de la chaîne "false" (fréquente dans un JSON extrait à la main) un True."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "vrai", "oui", "yes", "1")
    return bool(value)

def _coerce_analysis_report(data: Any) -> Optional[AnalysisReport]:
    """Reconstruit un AnalysisReport champ par champ depuis un JSON extrait à la main, en
    corrigeant les écarts courants du modèle (confiance en pourcentage, alternative hors liste)
    au lieu de tout rejeter pour un seul champ. None si request_type lui-même est inexploitable.
    """
    if not isinstance(data, dict):
        return None
    request_type = str(data.get("request_type", "")).strip().upper()
    if request_type not in _REQUEST_TYPES:
        return None
    alternative = str(data.get("alternative_type") or "").strip().upper()
    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        # Absente ou illisible = incertaine (0.5), pas 1.0 : un JSON récupéré à la main
        # depuis une sortie mal formée ne mérite pas d'être cru sur parole.
        confidence = 0.5
    # Le prompt demande 0 à 1 ; les écarts courants sont une note sur 10 ou un pourcentage.
    # Au-delà, la valeur n'a pas de sens : traitée comme inconnue (0.5).
    if 1 < confidence <= 10:
        confidence /= 10
    elif 10 < confidence <= 100:
        confidence /= 100
    elif confidence > 100:
        confidence = 0.5
    questions = data.get("questions") or []
    return AnalysisReport(
        summary=str(data.get("summary") or "Analyse effectuée."),
        reasoning=str(data.get("reasoning") or ""),
        alternative_type=alternative if alternative in _REQUEST_TYPES else None,
        request_type=request_type,
        confidence=min(max(confidence, 0.0), 1.0),
        is_clear=_as_bool(data.get("is_clear", False)),
        questions=[str(q) for q in questions] if isinstance(questions, list) else [],
    )

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini/gemini-3.5-flash-lite")

def _make_llm(temperature: float) -> LLM:
    return LLM(model=MODEL_NAME, api_key=GEMINI_API_KEY, temperature=temperature, request_timeout=120)

# Une température par nature de travail, au lieu d'un 0.7 unique : classer, recopier ou
# vérifier demande de la constance ; seule la conception fonctionnelle gagne à rester créative.
qualification_llm = _make_llm(0.1)
designer_llm = _make_llm(0.5)
architect_llm = _make_llm(0.3)
diagnostic_llm = _make_llm(0.2)
developer_llm = _make_llm(0.0)
qa_llm = _make_llm(0.2)

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

def build_conversation_context(prior_entries, total_count: Optional[int] = None) -> str:
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
    # total_count : nombre réel de tours quand l'appelant n'a chargé que les plus récents.
    total = max(total_count or 0, len(prior_entries))
    status_labels = {"success": "réussi", "failed": "échoué", "running": "en cours (probablement interrompu)"}
    lines = []
    if total > len(recent_entries):
        lines.append(f"[{total - len(recent_entries)} tour(s) plus ancien(s) omis pour rester concis]")
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

# Dossier DÉDIÉ aux fichiers livrés en mode local (sans repository cible) : jamais le dossier
# de travail du serveur, où un fichier livré nommé "main.py" ou ".env" écraserait le backend en
# cours d'exécution. Chaque conversation a son propre sous-dossier (dérivé de son id, stable d'un
# tour à l'autre) : deux conversations ne s'écrasent jamais, et un tour de suivi ("corrige ça")
# relit bien ce que le tour précédent a livré.
BACKEND_DIR = Path(__file__).resolve().parent
LOCAL_WORKSPACE_DIR = Path(os.getenv("LOCAL_WORKSPACE_DIR") or BACKEND_DIR / "workspace").resolve()

def _conversation_workspace(conversation_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", conversation_id or "").strip(".-")
    return LOCAL_WORKSPACE_DIR / (f"conversation-{safe}" if safe else "sans-conversation")

def _local_target(workspace: Path, path: str) -> Path | None:
    """Chemin absolu dans `workspace`, ou None s'il en sortirait (".."). Un préfixe "./" ou "/"
    (fréquent sous la plume d'un LLM) est normalisé plutôt que refusé."""
    normalized = normalize_path(path)
    if normalized is None:
        return None
    root = workspace.resolve()
    target = (root / normalized).resolve()
    return target if root in target.parents else None

def _write_files_locally(workspace: Path, files: list[dict], rejected_sink: dict[str, str]) -> str:
    """Pendant disque local de write_files_to_branch (mode sans repository cible), confiné à
    `workspace`. Les fichiers NON écrits sont ajoutés à rejected_sink ({chemin: raison})."""
    written = []
    for f in files:
        path, content = f["path"], f["content"]
        target = _local_target(workspace, path)
        if target is None:
            rejected_sink[path] = "chemin hors de l'espace de travail local refusé"
            continue
        syntax_issue = _reject_invalid_syntax(path, content)
        if syntax_issue:
            rejected_sink[path] = syntax_issue
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(path)
        except Exception as e:
            rejected_sink[path] = f"{type(e).__name__}: {e}"
    # Comme write_files_to_branch : aucun fichier écrit est une ERREUR, jamais un "OK : 0" que
    # le Développeur lirait comme un succès (règle "OK : passe à l'étape suivante").
    message = (
        f"OK : {len(written)} fichier(s) écrit(s) dans l'espace de travail local."
        if written or not files
        else f"ERREUR : aucun fichier écrit dans l'espace de travail local ({len(files)} rejeté(s))."
    )
    refused = {p: r for p, r in rejected_sink.items() if p in {f["path"] for f in files}}
    if refused:
        message += f"\nREJETÉS ({len(refused)}) — non écrits :\n" + "\n".join(
            f"- '{p}' : {reason}" for p, reason in refused.items()
        )
    return message

def _read_local_file(workspace: Path, path: str) -> tuple[str | None, str | None]:
    target = _local_target(workspace, path)
    if target is None:
        return None, "chemin hors de l'espace de travail local"
    try:
        return target.read_text(encoding="utf-8"), None
    except FileNotFoundError:
        return None, f"{FILE_ABSENT} : '{path}' n'existe pas dans l'espace de travail local"
    except IsADirectoryError:
        return None, f"{FILE_ABSENT} : '{path}' est un dossier, pas un fichier"
    except UnicodeDecodeError:
        return None, f"{PRESENT_UNREADABLE} : '{path}' existe mais n'est pas du texte UTF-8"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

MAX_RETRY_CONTEXT_CHARS = 4000

def _never_cache(_args: Any = None, _result: Any = None) -> bool:
    return False

def _cache_success_only(_args: Any = None, result: Any = None) -> bool:
    return str(result or "").startswith("OK")

# CrewAI refuse d'exécuter deux fois de suite un appel d'outil IDENTIQUE (mode ReAct), cache ou
# non : pour réessayer après une erreur transitoire, les arguments doivent changer.
_RETRY_HINT = (
    "\nPour réessayer après une erreur transitoire, rappelle cet outil avec un commit_message "
    "légèrement différent (ex: ajoute « (2e essai) ») : un appel identique serait refusé."
)

_NEGATION_BEFORE = re.compile(r"\b(rien|aucun|pas|nothing|no)\s+(de\s+|d'\s*)?$", re.IGNORECASE)
# Mention POSITIVE ("src/a.ts réalisé, src/b.ts NON réalisé") : ce qui la précède appartient à
# une autre proposition. Plus fiable qu'une ponctuation (",", "|" d'un tableau Markdown...).
_DONE_POSITIVE = re.compile(r"(?<!non\s)(?<!non\s\s)\br[ée]alis[ée]e?s?(?:\(e?s\))?(?!\w)", re.IGNORECASE)

def _withdrawn_paths(text: str, candidates) -> set[str]:
    """Chemins de `candidates` que `text` déclare "NON réalisé" (retrait explicite), en une
    seule passe. Un chemin est retiré s'il figure, délimité exactement (pas "src/App.tsx.bak"
    pour "src/App.tsx", "./" ou "/" initial toléré), sur la même ligne avant la mention et après
    la dernière mention positive ("réalisé") : tous les fichiers de "src/a.ts, src/b.ts — NON
    réalisés" ou d'une ligne de tableau "| src/a.ts | NON réalisé |" sont retirés, pas "src/a.ts"
    dans "src/a.ts réalisé, src/b.ts NON réalisé". Une mention niée ("rien de NON réalisé") et
    le contenu des fichiers livrés (entre balises) sont ignorés."""
    patterns = {
        path: re.compile(r"(?<![\w./-])(?:\./|/)?" + re.escape(path) + r"(?![\w/-]|\.\w)")
        for path in candidates
    }
    withdrawn: set[str] = set()
    for line in FILE_BLOCKS.sub("", text).splitlines():
        for match in NOT_DELIVERED_MARKER.finditer(line):
            before = line[:match.start()]
            if _NEGATION_BEFORE.search(before):
                continue
            positives = list(_DONE_POSITIVE.finditer(before))
            clause = before[positives[-1].end():] if positives else before
            withdrawn.update(path for path, pattern in patterns.items() if pattern.search(clause))
    return withdrawn

# Tolère "Verdict final (après revue complète) : GO", "**Verdict** : NO GO", "Verdict — GO",
# "Verdict : ✅ GO", "Verdict : GO avec réserves", "Verdict : NON GO", ou "## Verdict" en
# titre suivi de "**GO**" sur une ligne suivante.
# Pour écarter les mentions fortuites ("verdict: No go-live possible", "... :\nGo figure"), une
# valeur qui n'est pas en MAJUSCULES ne doit être suivie ni d'un tiret collé ni d'un mot en
# minuscules ; en MAJUSCULES (la forme demandée à la QA), tout est accepté ("NO_GO car ...").
_VERDICT_VALUES = r"GO[ _]AVEC[ _]R[ÉE]SERVES|NON?[ _-]?GO|GO"
QA_VERDICT = re.compile(
    r"(?i:verdict)[^:\n—–=-]{0,60}(?:[:—–=-]|[ \t*]*\r?\n)\s*\W{0,8}"
    r"(?:(?P<eol>(?i:" + _VERDICT_VALUES + r"))(?![\w-])(?![ \t]+[a-zà-ÿ])"
    r"|(?P<upper>" + _VERDICT_VALUES + r")(?![\w-]))",
)

def _qa_verdict_guardrail(task_output):
    """Garantit un verdict QA lisible sans relancer l'agent (une relance QA coûterait jusqu'à
    10 appels d'outils) : sans verdict explicite, on l'ajoute comme NON FOURNI, à traiter en
    NO_GO, plutôt que de laisser l'utilisateur deviner la conclusion."""
    raw = getattr(task_output, "raw", "") or ""
    if QA_VERDICT.search(raw):
        return True, task_output
    return True, (
        f"{raw}\n\n**Verdict : NON FOURNI** — la QA n'a pas conclu explicitement : à considérer "
        "comme NO_GO tant qu'une vérification humaine n'a pas eu lieu."
    )

# --- CREW BASE ---
@CrewBase
class AppDevelopmentCrew():
    agents_config = 'agentsquestion.yaml'
    tasks_config = 'tasksquestion.yaml'

    @agent
    def qualification_agent(self) -> Agent:
        return Agent(config=self.agents_config['qualification_agent'], tools=[], llm=qualification_llm, max_iter=2, verbose=True)

    @agent
    def product_designer_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['product_designer_agent'],
            # Sans file_write_tool : son livrable est le texte de sa réponse (sauvegardé via
            # output_file), un outil d'écriture ne faisait que le distraire de son raisonnement.
            tools=[self._build_local_read_tool(), github_read_file, github_list_directory],
            llm=designer_llm, max_iter=3, verbose=True,
        )

    @agent
    def architect_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['architect_agent'],
            tools=[self._build_local_read_tool(), github_read_file, github_list_directory],
            llm=architect_llm, max_iter=3, verbose=True,
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
            llm=diagnostic_llm, max_iter=5, verbose=True,
            # Planification interne (hypothèses, lectures à faire) avant d'agir : l'agent le plus
            # critique du pipeline, dont tout le code livré dépend. Une seule passe de plan
            # (max_reasoning_attempts=1) pour rester raisonnable face au quota Gemini.
            reasoning=True, max_reasoning_attempts=1,
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
            # d'erreur (voir github_tools.py, _record_edit_failure) instruit l'agent appelant de
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
                # de travail dédié (LOCAL_WORKSPACE_DIR) ; file_write_tool écrirait n'importe où,
                # y compris sur le code du serveur.
                self._build_commit_analyst_files_tool(),
                check_syntax,
                github_create_branch, github_write_file, github_write_files,
                github_open_pull_request,
            ],
            # 6 (pas 8) : depuis la séparation avec diagnostic_agent (voir ci-dessus), cette tâche
            # n'a plus AUCUN diagnostic à faire, seulement à committer un code déjà rédigé — le
            # flux GitHub complet pour un BUGFIX (create_branch, write_file(s), check_syntax,
            # open_pull_request) tient en 4 appels ; 6 laisse une marge raisonnable sans jamais
            # retomber au niveau d'avant cette séparation, qui devait aussi couvrir un diagnostic
            # entier dans le même budget.
            llm=developer_llm, max_iter=6, verbose=True,
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
            llm=qa_llm, max_iter=10, verbose=True,
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
        return Task(
            config=self.tasks_config['diagnostic_task'], agent=self.diagnostic_agent(),
            output_file='docs/diagnostic_plan.md',
            guardrail=self._diagnostic_guardrail, guardrail_max_retries=1,
        )

    @task
    def development_task(self) -> Task:
        return Task(config=self.tasks_config['development_task'], agent=self.developer_agent(), output_file='docs/implementation_log.md')

    @task
    def qa_task(self) -> Task:
        return Task(
            config=self.tasks_config['qa_task'], agent=self.qa_agent(),
            output_file='tests/reports/qa_report.md',
            guardrail=_qa_verdict_guardrail,
        )

    # --- Outils et guardrails propres à UNE exécution ---
    # Ils lisent self._analyst_files, rempli par _diagnostic_guardrail et remis à zéro au début
    # de chaque run_dynamic_crew. Portée par instance (et non un global) : main.py crée un
    # AppDevelopmentCrew() dédié à chaque exécution, donc deux conversations concurrentes ne
    # partagent jamais ces fichiers. Pas de ContextVar non plus : CrewAI peut exécuter les
    # outils dans un pool de threads qui ne recopie pas le contexte courant.

    def _reset_execution_state(self, inputs: Optional[dict] = None) -> None:
        inputs = inputs or {}
        owner, repo = inputs.get("repo_owner") or "", inputs.get("repo_name") or ""
        # Cible FIXÉE par l'exécution (jamais par les arguments que le LLM passe aux outils) :
        # un owner vide passé par erreur ne doit jamais détourner un run GitHub vers le disque.
        self._work_branch = inputs.get("work_branch") or ""
        self._repo_target = (owner, repo) if owner and repo else None
        # Par conversation (et non par branche : en mode local, work_branch est toujours vide).
        self._workspace = _conversation_workspace(str(inputs.get("conversation_id") or ""))
        # Fichiers committables, fusionnés au fil des tentatives de l'Analyste (voir le guardrail).
        self._analyst_files = []
        # {chemin: raison} des fichiers annoncés par l'Analyste mais jamais committables
        # (raccourci "// ... reste du code", balise de fin manquante, chemin invalide).
        self._not_extracted = {}
        # {chemin: raison} des fichiers refusés à l'écriture lors du DERNIER commit qui les
        # concernait : un commit ultérieur réussi efface leur entrée.
        self._write_rejections = {}
        self._diagnostic_guardrail_failures = 0

    def _diagnostic_retry_context(self) -> str:
        """Specs/architecture reçues par diagnostic_task : CrewAI ne les repasse PAS à l'agent
        quand un guardrail le fait recommencer (seuls l'erreur et sa réponse précédente le sont),
        alors qu'une réécriture complète doit rester alignée dessus."""
        parts = []
        context = self.diagnostic_task().context
        # Hors run_dynamic_crew, CrewAI laisse ici une sentinelle "non spécifié", non itérable.
        for context_task in context if isinstance(context, list) else []:
            raw = getattr(getattr(context_task, "output", None), "raw", "") or ""
            if raw:
                parts.append(raw[:MAX_RETRY_CONTEXT_CHARS])
        return ("\n\nRappel du contexte reçu (specs/architecture) :\n" + "\n---\n".join(parts)) if parts else ""

    def _diagnostic_guardrail(self, task_output):
        """Refuse UNE fois une sortie de l'Analyste inexploitable (aucun fichier, balise de fin
        manquante, commentaires de type "// ... reste du code") pour qu'il la corrige ; à la 2e
        tentative, accepte en signalant le problème plutôt que de faire échouer tout le crew
        (CrewAI lève une exception quand un guardrail échoue au-delà de guardrail_max_retries).

        Les fichiers sains sont FUSIONNÉS d'une tentative à l'autre : une réponse corrigée qui ne
        reprend que les fichiers fautifs ne fait pas perdre les fichiers sains de la première —
        sauf ceux qu'elle retire explicitement (chemin suivi de "NON réalisé").
        """
        raw = getattr(task_output, "raw", "") or ""
        files, issue, faulty_paths, broken = review_diagnostic_output(raw)
        merged = {f["path"]: f for f in getattr(self, "_analyst_files", [])}
        not_extracted = dict(getattr(self, "_not_extracted", {}))
        delivered_now = {f["path"] for f in files}
        withdrawn = _withdrawn_paths(raw, list(merged) + sorted(delivered_now))
        # Fichier d'une tentative PRÉCÉDENTE déclaré "NON réalisé" : retrait explicite.
        for path in withdrawn - delivered_now:
            merged.pop(path, None)
            not_extracted[path] = "retiré par l'Analyste (NON réalisé)"
        # Fichier livré DANS cette réponse ET déclaré "NON réalisé" : ambigu ("NON réalisé" peut
        # décrire autre chose que le fichier). On ne tranche pas en silence : l'Analyste est
        # invité à clarifier ; s'il ne le fait pas, le fichier est EXCLU (et signalé) — committer
        # une version peut-être partielle par-dessus le vrai fichier est pire que ne rien committer.
        ambiguous = sorted(withdrawn & delivered_now)
        if ambiguous:
            clarification = (
                "Fichier(s) à la fois livré(s) entre balises ET déclaré(s) 'NON réalisé' : "
                + ", ".join(ambiguous)
                + ". Retire leurs balises s'ils ne sont vraiment pas réalisés, ou reformule la ligne "
                "du plan qui les mentionne."
            )
            issue = f"{issue}\n\n{clarification}" if issue else clarification
        for f in files:
            if f["path"] not in faulty_paths:
                merged[f["path"]] = f
                not_extracted.pop(f["path"], None)
        # Jamais de fichier à raccourci ou tronqué dans ce que le Développeur committera : il
        # écraserait le vrai fichier par une version incomplète. Une version SAINE d'une tentative
        # précédente reste en revanche committable.
        for path, reason in [(p, "contenu incomplet (commentaire de raccourci)") for p in faulty_paths] + list(broken.items()):
            if path not in merged:
                not_extracted[path] = reason
        self._analyst_files = list(merged.values())
        self._not_extracted = not_extracted
        if issue is None:
            return True, task_output
        self._diagnostic_guardrail_failures = getattr(self, "_diagnostic_guardrail_failures", 0) + 1
        if self._diagnostic_guardrail_failures <= 1:
            return False, (
                f"{issue}\n\nRenvoie ta réponse COMPLÈTE, avec TOUS les fichiers entre balises "
                f"(y compris ceux qui étaient déjà corrects).{self._diagnostic_retry_context()}"
            )
        for path in ambiguous:
            merged.pop(path, None)
            not_extracted[path] = "livré mais déclaré NON réalisé, ambiguïté non levée"
        self._analyst_files = list(merged.values())
        self._not_extracted = not_extracted
        excluded = "".join(f"\n- {p} : NON réalisé ({reason}, exclu du commit)" for p, reason in sorted(not_extracted.items()))
        return True, f"{raw}\n\n> ⚠️ Contrôle automatique (non corrigé par l'Analyste) : {issue}{excluded}"

    def _build_commit_analyst_files_tool(self):
        crew_self = self

        @tool("github_commit_analyst_files")
        def github_commit_analyst_files(commit_message: str) -> str:
            """
            Committe EN UN SEUL APPEL tous les fichiers rédigés par l'Analyste Diagnostic Technique,
            extraits automatiquement de sa réponse (balises <<<FICHIER: ...>>>) : tu n'as
            PAS à recopier leur contenu. À utiliser EN PRIORITÉ, après github_create_branch.
            Le repository, la branche de travail (ou, sans repository cible, l'espace de travail
            local) sont ceux de l'exécution en cours : tu n'as pas à les fournir.
            Mêmes garde-fous que github_write_files (branche principale refusée, fichiers
            Python/JSON/YAML invalides rejetés et listés dans une section REJETÉS).
            Arguments:
                commit_message (str): message de commit.
            """
            files = list(getattr(crew_self, "_analyst_files", []) or [])
            not_extracted = dict(getattr(crew_self, "_not_extracted", {}) or {})
            excluded_note = (
                "\nEXCLUS (non committables) : "
                + "; ".join(f"{p} ({reason})" for p, reason in sorted(not_extracted.items()))
                + ". Ne les committe JAMAIS, avec aucun outil : liste-les comme non livrés."
            ) if not_extracted else ""
            if not files and not_extracted:
                return f"INFO : aucun fichier committable.{excluded_note}"
            if not files:
                if getattr(crew_self, "_repo_target", None) is None:
                    # En local, cet outil est le SEUL moyen d'écrire : aucun repli possible.
                    return (
                        "INFO : aucun fichier n'a pu être extrait automatiquement de la réponse de "
                        "l'Analyste, rien n'a été écrit dans l'espace de travail local. Indique-le "
                        "dans ton rapport (aucun fichier livré)."
                    )
                return (
                    "INFO : aucun fichier n'a pu être extrait automatiquement de la réponse de "
                    "l'Analyste. Si elle contient quand même du code, committe-le avec "
                    "github_write_files ; sinon, indique dans ton rapport qu'il n'y avait rien à committer."
                )
            manifest = format_manifest(files)
            rejections: dict[str, str] = {}
            target = getattr(crew_self, "_repo_target", None)
            if target is None:
                result = _write_files_locally(crew_self._workspace, files, rejections)
            else:
                owner, repo = target
                result = write_files_to_branch(owner, repo, crew_self._work_branch, commit_message, files, rejections)
                if not result.startswith("OK"):
                    # Commit entier refusé (branche protégée, collision de dossier, erreur GitHub).
                    for f in files:
                        rejections.setdefault(f["path"], f"commit refusé : {result[:200]}")
            # Cet appel fait foi pour SES fichiers : un échec précédent réparé par ce commit est
            # oublié, un nouveau refus est retenu (voir qa_verify_delivered_files).
            for f in files:
                crew_self._write_rejections.pop(f["path"], None)
            crew_self._write_rejections.update(rejections)
            retry_hint = "" if result.startswith("OK") else _RETRY_HINT
            return f"{result}\nFichiers extraits de la réponse de l'Analyste :\n{manifest}{excluded_note}{retry_hint}"

        # Seul un SUCCÈS est mis en cache : un 2e appel après un "OK" ne réécrit pas tous les
        # fichiers, mais une erreur n'est jamais resservie (voir aussi _RETRY_HINT).
        github_commit_analyst_files.cache_function = _cache_success_only
        return github_commit_analyst_files

    def _build_qa_verify_tool(self):
        crew_self = self

        @tool("qa_verify_delivered_files")
        def qa_verify_delivered_files() -> str:
            """
            Vérifie EN UN SEUL APPEL chaque fichier rédigé par l'Analyste : présence réelle sur la
            branche de travail (ou dans l'espace de travail local), comparaison EXACTE (diff) avec
            la version de l'Analyste, et check_syntax sur le contenu réellement présent — ainsi que
            les fichiers annoncés mais jamais committables. Chaque résultat est une preuve outillée
            [vérifié outil]. Aucun argument : la cible est celle de l'exécution en cours.
            """
            files = list(getattr(crew_self, "_analyst_files", []) or [])
            target = getattr(crew_self, "_repo_target", None)
            if target is None:
                workspace = crew_self._workspace
                fetch = lambda path: _read_local_file(workspace, path)  # noqa: E731
            else:
                fetch = make_file_fetcher(target[0], target[1], crew_self._work_branch)
            return build_delivery_report(
                files, fetch,
                write_rejections=dict(getattr(crew_self, "_write_rejections", {}) or {}),
                not_extracted=dict(getattr(crew_self, "_not_extracted", {}) or {}),
            )

        # Sans argument, un 2e appel (après une correction) resservirait sinon l'ancien rapport.
        qa_verify_delivered_files.cache_function = _never_cache
        return qa_verify_delivered_files

    def _build_local_read_tool(self):
        crew_self = self

        @tool("read_a_files_content")
        def read_a_files_content(file_path: str) -> str:
            """
            Lit le contenu d'un fichier de l'espace de travail LOCAL de cette conversation (mode
            sans repository GitHub cible). Avec un repository cible, utilise github_read_file.
            Arguments:
                file_path (str): chemin relatif du fichier (ex: 'src/App.tsx' ou 'index.html').
            """
            if getattr(crew_self, "_repo_target", None) is not None:
                return (
                    "INFO : un repository GitHub cible est défini pour cette exécution : lis ses "
                    "fichiers avec github_read_file, pas sur le disque local."
                )
            content, error = _read_local_file(crew_self._workspace, file_path)
            if content is None:
                return (
                    f"ERREUR_FICHIER_INEXISTANT : {error}. Inutile de réessayer la lecture de ce "
                    "fichier exact : note cette absence dans ton rapport et poursuis ton analyse."
                )
            return content if content.strip() else f"INFO : Le fichier '{file_path}' est vide."

        return read_a_files_content

    @retry_on_rate_limit_async(max_retries=5, base_delay=12.0)
    async def analyze_user_request(self, user_prompt: str, conversation_context: str = "") -> QualificationResult:
        qualif_agent = self.qualification_agent()
        task_prompt = f"""
        Tu es le Spécialiste en Qualification / Senior Product Owner.
        Voici la demande actuelle : "{user_prompt}"

        Tours précédents de cette conversation (pour interpréter un message de suivi comme
        "corrige ça" ou "ajoute aussi Y" ; ne décide JAMAIS sur ce seul contexte si la demande
        actuelle dit autre chose) :
        {conversation_context or "Aucun échange précédent."}

        Applique la grille de décision de ta fiche, dans cet ordre :
        1. summary : ce que tu as compris.
        2. reasoning : les indices précis relevés dans la demande et la règle appliquée.
        3. alternative_type : la 2e catégorie la plus plausible (ou null).
        4. request_type : ta décision.
        5. confidence : de 0 à 1. Sous {QUALIFICATION_CONFIDENCE_THRESHOLD}, is_clear doit être false.
        6. is_clear, puis questions (2 à 4, fermées si possible) si is_clear est false.
        """
        analysis_task = Task(description=task_prompt, expected_output="Schéma JSON AnalysisReport.", agent=qualif_agent, output_pydantic=AnalysisReport)
        analysis_crew = Crew(agents=[qualif_agent], tasks=[analysis_task], process=Process.sequential, verbose=False)
        
        # Exécution asynchrone pour éviter l'erreur d'event loop
        result = await analysis_crew.kickoff_async()
        quota_mgr.last_execution_time = time.time()

        if hasattr(result, 'pydantic') and result.pydantic is not None:
            return QualificationResult(**_enforce_confidence_threshold(result.pydantic).model_dump())

        raw_output = str(result.raw) if hasattr(result, 'raw') else str(result)
        try:
            match = re.search(r'\{.*\}', raw_output, re.DOTALL)
            if match:
                report = _coerce_analysis_report(json.loads(match.group(0)))
                if report is not None:
                    return QualificationResult(**_enforce_confidence_threshold(report).model_dump())
        except Exception:
            pass

        # Qualification impossible : on demande plutôt que de lancer au hasard le workflow le
        # plus long (DESIGN_AND_DEV, 5 agents) sur une catégorie que rien ne justifie.
        return QualificationResult(fallback=True, **_enforce_confidence_threshold(AnalysisReport(
            summary=f"Analyse : {user_prompt}",
            reasoning="La qualification automatique n'a pas produit de résultat exploitable.",
            request_type="DESIGN_AND_DEV",
            confidence=0.0,
            is_clear=False,
        )).model_dump())

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

        # Contexte EXPLICITE par tâche au lieu du défaut CrewAI (toutes les sorties précédentes) :
        # chaque agent reçoit ce dont il a besoin pour raisonner, et pas plus. Le Développeur ne
        # reçoit que le code de l'Analyste (specs/architecture ne feraient que diluer ce qu'il doit
        # committer) ; la QA reçoit les critères d'acceptation du Designer en plus du code et du
        # rapport de commit, pour juger la conformité fonctionnelle.
        tasks_by_key = dict(selected)
        context_plan = {
            'architecture': ['design'],
            'diagnostic': ['design', 'architecture'],
            'development': ['diagnostic'],
            'qa': ['design', 'diagnostic', 'development'],
        }
        for key, task_obj in selected:
            wanted = [tasks_by_key[k] for k in context_plan.get(key, []) if k in tasks_by_key]
            task_obj.context = wanted if wanted else None
        self._reset_execution_state(inputs)

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
            # 13 (pas 3) : partagé par TOUS les agents de ce crew en séquence (design,
            # architecture, diagnostic, development, qa) — qa_task étant dernier et ayant le
            # max_iter le plus élevé (10, voir qa_agent), c'est lui qui hérite le plus de la
            # latence cumulée d'un plafond trop bas. Relevé sur demande explicite pour réduire
            # cette latence ; retry_on_rate_limit_async (plus haut) absorbe toujours les 429
            # transitoires si cette valeur s'avère trop optimiste face au quota Gemini réel.
            max_rpm=13,
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
