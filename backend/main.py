import asyncio
import uuid

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import update as sql_update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from crewquestion import (
    AppDevelopmentCrew, QualificationResult,
)
import database
from database import (
    create_db_and_tables, get_session, Conversation, ExecutionHistory,
)
from auth import get_current_user, close_http_client
from orphans import sweep_stale_executions
from logs import get_logger
from qa_report import final_verdict
from limits import check_qualify_rate, check_user_execution_quota, record_execution_launch
from errors import (
    AppError, ErrorCode, classify_exception, http_status_for,
)
from github_tools import (
    check_github_access, GitHubAccessProblem, WORK_BRANCH_PREFIX,
)
import execution
import execution_context
import execution_state
import memory_monitor
import routes_conversations
import routes_history
from routes_metrics import router as metrics_router
from error_handlers import CORS_ALLOW_CREDENTIALS, CORS_ALLOW_ORIGINS, register_error_handlers
from schemas import UserRequestInput, WorkflowExecutionInput

log = get_logger("main")

# 1. INSTANCIATION DE FASTAPI (Obligatoire au tout début !)
app = FastAPI(title="CrewAI App Development API")


# Délai (best-effort, voir on_shutdown) accordé aux exécutions de crew encore en tâche de fond
# pour se terminer avant que le process ne s'arrête. Constante de module (comme
# execution_state.MAX_CONCURRENT_EXECUTIONS ci-dessus), pas locale à on_shutdown, pour rester visible/réutilisable
# sans dupliquer sa valeur (ex: un futur endpoint de santé qui voudrait l'exposer).
_SHUTDOWN_DRAIN_TIMEOUT_S = 20

_BACKFILL_BATCH = 200

def _backfill_qa_verdicts() -> None:
    """Rattrape `qa_verdict` des exécutions réussies d'avant la colonne, depuis le texte de leur résultat, pour que
    la qualité dans le temps couvre l'historique. Une exécution sans verdict (aucun QA) reste à NULL et est
    relue à chaque démarrage : le coût est borné par lots. Best-effort."""
    try:
        with Session(database.engine) as backfill:
            last_id = 0
            while True:
                rows = backfill.exec(
                    select(ExecutionHistory.id, ExecutionHistory.result)
                    .where(ExecutionHistory.status == "success")
                    .where(col(ExecutionHistory.qa_verdict).is_(None))
                    .where(ExecutionHistory.id > last_id)
                    .order_by(col(ExecutionHistory.id))
                    .limit(_BACKFILL_BATCH)
                ).all()
                if not rows:
                    break
                last_id = rows[-1][0] or last_id
                for execution_id, result in rows:
                    verdict = final_verdict(result or "")
                    if verdict:
                        backfill.exec(
                            sql_update(ExecutionHistory).where(ExecutionHistory.id == execution_id).values(qa_verdict=verdict)
                        )
                backfill.commit()
    except Exception as e:
        log.warning(f"rattrapage des verdicts QA ignoré : {type(e).__name__}: {e}")

# 2. ÉVÉNEMENT DE DÉMARRAGE (Création des tables BDD)
@app.on_event("startup")
def on_startup():
    create_db_and_tables()
    _backfill_qa_verdicts()
    # Imprimé une seule fois, au démarrage : rend le plafond mémoire du conteneur visible dans les
    # logs Render dès le boot, sans attendre qu'une exécution déclenche la première ligne [MEM]
    # (memory_monitor.log_memory) — utile pour juger d'emblée si le plan actuel a une marge suffisante pour ce
    # type de charge (CrewAI + plusieurs agents Gemini), avant même de lancer quoi que ce soit.
    memory_monitor.log_memory("démarrage du service")
    # Libère les conversations bloquées par une exécution morte avec l'ancien process (crash, OOM,
    # redéploiement). Best-effort : ne doit jamais empêcher le service de démarrer.
    try:
        with Session(database.engine) as session:
            swept = sweep_stale_executions(session, active_ids=execution_state.active_execution_ids)
        if swept:
            log.info(f"Démarrage : {len(swept)} exécution(s) orpheline(s) marquée(s) interrompue(s) : {swept}")
    except Exception as e:
        log.warning(f"balayage des exécutions orphelines impossible : {type(e).__name__}: {e}")

# Ferme proprement le client HTTP partagé de auth.py (voir sa docstring) plutôt que de
# laisser ses connexions ouvertes à l'arrêt du process.
@app.on_event("shutdown")
async def on_shutdown():
    # Best-effort, PAS une garantie : une exécution de crew tourne désormais en tâche de fond
    # asyncio (execution_state.background_tasks), invisible pour le mécanisme de "graceful shutdown" d'Uvicorn —
    # celui-ci ne suit et n'attend que les requêtes HTTP en vol, jamais des Task créées par
    # l'application elle-même. Sans cette attente explicite ici, un redéploiement Render (SIGTERM)
    # pendant une exécution en cours laisserait sa ligne bloquée sur "running" pour toujours (voir
    # la limite déjà documentée dans execute_workflow). _SHUTDOWN_DRAIN_TIMEOUT_S est un compromis
    # : le délai réel accordé par Render entre SIGTERM et un SIGKILL forcé n'est pas connu
    # précisément d'ici, et une exécution DESIGN_AND_DEV/FEATURE peut de toute façon durer
    # plusieurs minutes — largement au-delà de ce que quelque délai raisonnable que ce soit ici
    # pourrait couvrir. Chaque seconde accordée reste néanmoins strictement plus utile que zéro :
    # une exécution sur le point de se terminer a ainsi une vraie chance de persister son résultat
    # avant l'arrêt, plutôt qu'aucune.
    if execution_state.background_tasks:
        log.info(f"Arrêt du service : attente (best-effort, jusqu'à {_SHUTDOWN_DRAIN_TIMEOUT_S}s) de "
            f"{len(execution_state.background_tasks)} exécution(s) de crew encore en tâche de fond...")
        await asyncio.wait(list(execution_state.background_tasks), timeout=_SHUTDOWN_DRAIN_TIMEOUT_S)
    await close_http_client()


# CORS (politique : voir error_handlers.py) puis format d'erreur unique de l'API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_error_handlers(app)
app.include_router(metrics_router)
app.include_router(routes_history.router)
app.include_router(routes_conversations.router)


crew_instance = AppDevelopmentCrew()


# kind de GitHubAccessProblem -> (statut HTTP, code, réessayable)
_GITHUB_ACCESS_ERRORS = {
    "not_found": (404, ErrorCode.NOT_FOUND, False),
    "forbidden": (403, ErrorCode.FORBIDDEN, False),
    "invalid_token": (502, ErrorCode.GITHUB_UNAVAILABLE, False),
    "unavailable": (503, ErrorCode.GITHUB_UNAVAILABLE, True),
    "rate_limited": (503, ErrorCode.GITHUB_UNAVAILABLE, True),
    "missing_token": (500, ErrorCode.INTERNAL_ERROR, False),
}

_GITHUB_PRECHECK_TIMEOUT_S = 15


# 4. ENDPOINTS API
@app.get("/")
def read_root():
    return {"status": "API CrewAI opérationnelle"}


@app.post("/api/qualify", response_model=QualificationResult)
async def qualify_request(data: UserRequestInput, user: dict = Depends(get_current_user)):
    """Étape 1 : Qualification du besoin"""
    # Tours précédents de la conversation : sans eux, un message de suivi ("corrige ça",
    # "ajoute aussi Y") est qualifié hors contexte, souvent en DESIGN_AND_DEV par défaut.
    check_qualify_rate(user.get("id"))
    conversation_context = ""
    if data.conversation_id is not None:
        loaded = await asyncio.to_thread(execution_context.load_qualification_context, data.conversation_id, user.get("id"))
        if loaded is None:
            raise HTTPException(status_code=404, detail="Conversation introuvable.")
        conversation_context = loaded
    try:
        report = await crew_instance.analyze_user_request(data.user_request, conversation_context, data.has_repo_target)
        crew_instance.save_analysis_report(report, data.user_request)
        return report
    except Exception as e:
        log.error("erreur CrewAI pendant la qualification", exc_info=True)
        # Pas de str(e) dans la réponse : le détail réel reste dans les logs ci-dessus.
        info = classify_exception(e)
        raise AppError(
            http_status_for(info.code), info.code,
            info.message or "La qualification de la demande a échoué.", info.retryable,
        )

_CONVERSATION_BUSY_MESSAGE = (
    "Une exécution est déjà en cours pour cette conversation. Attends qu'elle se termine avant d'envoyer un nouveau message."
)


def _ensure_conversation_idle(session: Session, conversation_id: int) -> str:
    """Refuse (409) si une exécution est déjà en cours sur cette conversation ; renvoie sinon le rappel de ses tours.

    Tours précédents : donnent aux agents un rappel de ce qui a déjà été demandé/livré, et permettent de continuer sur
    la même branche de travail plutôt que d'en ouvrir une nouvelle déconnectée à chaque message. Trois requêtes ciblées
    (tours en cours, derniers tours, branche) plutôt que de relire toute la conversation avec le résultat de chaque tour.

    Empêche deux exécutions concurrentes sur la même conversation. Nécessaire depuis la réutilisation du work_branch
    entre tours : sans ce garde-fou, deux requêtes parallèles écriraient toutes les deux sur la même branche via
    github_write_file/github_edit_file, qui se basent sur le SHA du fichier pour détecter les conflits (optimistic
    concurrency) — l'une des deux échouerait avec un SHA obsolète au lieu d'une erreur claire. Un tour resté bloqué à
    "running" (crash serveur en cours d'exécution) est balayé (orphans.sweep_stale_executions) s'il n'a plus donné signe
    de vie : il ne doit pas bloquer la conversation pour toujours."""
    running_ids, conversation_context = execution_context.prior_turns(session, conversation_id)
    if running_ids and sweep_stale_executions(session, active_ids=execution_state.active_execution_ids, conversation_id=conversation_id):
        session.expire_all()
        running_ids, conversation_context = execution_context.prior_turns(session, conversation_id)
    if running_ids:
        raise HTTPException(status_code=409, detail=_CONVERSATION_BUSY_MESSAGE)
    return conversation_context


@app.post("/api/execute")
async def execute_workflow(
    data: WorkflowExecutionInput,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Étape 2 : Lancement dynamique des agents & enregistrement BDD"""
    memory_monitor.log_memory("début /api/execute")
    final_prompt = (
        f"Demande initiale : {data.user_request}\n"
        f"Type d'exécution : {data.target_workflow}\n"
        f"Précisions apportées : {data.clarifications if data.clarifications else 'Aucune.'}"
    )

    has_repo_target = bool(data.repo_owner and data.repo_name)
    # ANALYSE_ONLY ne comprend que design_task/architecture_task, en lecture seule (voir
    # run_dynamic_crew dans crewquestion.py) : jamais de branche/commit/PR à vérifier pour ce
    # workflow. Calculé une seule fois et réutilisé aux deux points qui en ont besoin plus bas
    # (capture du SHA de référence avant le crew, vérification après coup) plutôt que dupliqué,
    # pour qu'ils ne puissent pas diverger silencieusement si l'un est modifié sans l'autre.
    should_verify_github_delivery = has_repo_target and data.target_workflow != "ANALYSE_ONLY"

    conversation = None
    if data.conversation_id is not None:
        conversation = session.get(Conversation, data.conversation_id)
        if not conversation or conversation.user_id != user.get("id"):
            raise HTTPException(status_code=404, detail="Conversation introuvable.")

    # Contrôle préalable GitHub : une faute (repository, droits, branche de base) se découvre ICI, avant
    # toute ligne en base et tout appel LLM, au lieu de la fin d'une exécution de plusieurs minutes.
    if should_verify_github_delivery:
        try:
            # Borné dans le temps : PyGithub peut sinon attendre longtemps (délai par défaut et
            # nouvelles tentatives) pendant que la requête de lancement reste suspendue.
            await asyncio.wait_for(
                asyncio.to_thread(check_github_access, data.repo_owner, data.repo_name, data.base_branch or "main"),
                timeout=_GITHUB_PRECHECK_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            raise AppError(
                503, ErrorCode.GITHUB_UNAVAILABLE,
                "GitHub met trop de temps à répondre : réessayez dans quelques instants.", True,
            )
        except GitHubAccessProblem as problem:
            status_code, code, retryable = _GITHUB_ACCESS_ERRORS.get(
                problem.kind, (500, ErrorCode.INTERNAL_ERROR, False)
            )
            raise AppError(status_code, code, problem.message, retryable)

    # Conversation existante : son conflit (double clic, deux onglets) est testé AVANT les plafonds, pour garder le message
    # précis « déjà en cours pour cette conversation ». Rien n'est écrit à ce stade.
    conversation_context = _ensure_conversation_idle(session, conversation.id) if conversation is not None else ""

    # Plafonds par utilisateur (exécutions simultanées, exécutions par heure) : un compte ne sature pas les autres.
    # AVANT toute écriture : un refus ne laisse pas de conversation vide. Quelques lectures légères en base (balayage
    # compris), comme les autres accès à `session` de ce point d'accès.
    check_user_execution_quota(session, user.get("id"), execution_state.active_execution_ids)

    if conversation is None:
        conversation = Conversation(
            user_id=user.get("id"),
            title=data.user_request.strip()[:80] or "Nouvelle conversation",
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)

    # Reprise d'une exécution en échec (étapes déjà réussies réutilisées) ; {} si rien n'est reprenable.
    resume_outputs = execution_context.resumable_outputs(session, data, user.get("id"), conversation.id)

    # Normalisé une seule fois : utilisé à la fois pour comparer aux tours précédents et
    # pour ce qui est stocké sur ce tour, afin que les deux restent cohérents (sinon un
    # base_branch vide explicitement envoyé empêcherait à tort la réutilisation de
    # branche au tour suivant, qui compare toujours à la valeur normalisée).
    normalized_base_branch = (data.base_branch or "main") if has_repo_target else None

    work_branch = ""
    if has_repo_target:
        # Réutilise le NOM de branche d'un tour précédent quel que soit son statut (y compris
        # "failed") : c'est ce qui permet à un "Recommence l'implémentation" après un échec de
        # continuer sur la MÊME branche/PR plutôt que d'en ouvrir une nouvelle à chaque tentative.
        # Savoir si cette branche existe RÉELLEMENT sur GitHub à cet instant (utile à
        # diagnostic_task, voir tasksquestion.yaml) n'est PAS déduit ici du statut DB de ce tour
        # précédent (un tour "failed" peut avoir réellement poussé des commits, ex: si seule
        # l'ouverture de la PR a échoué — voir verify_github_delivery) : execution.run_crew_and_persist
        # interroge directement l'API GitHub (repo_branch_sha_before, live) pour ce signal, plus
        # fiable qu'une heuristique basée sur ce champ.
        work_branch = execution_context.previous_work_branch(session, conversation.id, data.repo_owner, data.repo_name, normalized_base_branch)
        if not work_branch:
            work_branch = f"{WORK_BRANCH_PREFIX}{data.target_workflow.lower()}-{uuid.uuid4().hex[:8]}"

    # Enregistrement immédiat (statut "running") pour garder une trace même en cas d'échec
    db_entry = ExecutionHistory(
        user_request=data.user_request,
        workflow=data.target_workflow,
        clarifications=data.clarifications,
        status="running",
        user_id=user.get("id"),
        conversation_id=conversation.id,
        repo_owner=data.repo_owner if has_repo_target else None,
        repo_name=data.repo_name if has_repo_target else None,
        base_branch=normalized_base_branch,
        work_branch=work_branch or None,
        scope=data.scope,
        attempts=1,
        reused_steps=len(resume_outputs),
    )
    session.add(db_entry)
    try:
        session.commit()
    except IntegrityError:
        # Index unique partiel (database.ONE_RUNNING_PER_CONVERSATION_INDEX) : une requête simultanée a pris la place
        # entre le contrôle ci-dessus et cet insert. Même réponse que le contrôle applicatif.
        session.rollback()
        raise HTTPException(status_code=409, detail=_CONVERSATION_BUSY_MESSAGE)
    session.refresh(db_entry)
    record_execution_launch(session, user.get("id"))

    # Compromis de mémoïsation CrewAI (cache module-level sans éviction native, purgé activement
    # par run_dynamic_crew) : voir la docstring de execution.execute_crew_and_persist, qui construit
    # désormais l'instance AppDevelopmentCrew dédiée à cette exécution.
    #
    # asyncio.create_task (pas `await`) : lance l'exécution réelle du crew en tâche de fond et
    # répond IMMÉDIATEMENT, plutôt que de faire attendre le client (potentiellement plusieurs
    # minutes) sur cette même connexion HTTP. Verrouiller l'écran du téléphone ou fermer l'onglet
    # pendant l'attente coupait cette connexion (comportement standard d'un navigateur mobile en
    # arrière-plan) — l'exécution continuait déjà côté serveur dans les deux cas (rien n'annule
    # cette tâche juste parce que le client se déconnecte), mais sans réponse à attendre, plus
    # aucune fenêtre où une telle coupure prive l'utilisateur de voir le résultat final : le
    # sondage de progression déjà existant côté frontend (useConversation.ts) prend le relais dès
    # que l'exécution quitte "running" en base, indépendamment de cette connexion d'origine.
    # execution_state.background_tasks (voir sa définition) retient une référence forte le temps de l'exécution,
    # pour ne pas risquer que le garbage collector ne l'interrompe en cours de route.
    task = asyncio.create_task(execution.execute_crew_and_persist(
        db_entry.id, conversation.id, data, has_repo_target, should_verify_github_delivery,
        work_branch, normalized_base_branch, final_prompt, conversation_context, resume_outputs,
    ))
    execution_state.track_execution_task(task, db_entry.id)

    return {
        "status": "running", "id": db_entry.id, "conversation_id": conversation.id,
        # Étapes réutilisées d'une exécution précédente (vide hors reprise) : l'interface l'indique.
        "resumed_steps": list(resume_outputs),
    }


