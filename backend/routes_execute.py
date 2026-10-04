"""Points d'accès de lancement : qualification du besoin (étape 1) et exécution du workflow (étape 2)."""
import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

import execution
import execution_context
import execution_resume
import execution_state
import memory_monitor
from auth import get_current_user
from crewquestion import AppDevelopmentCrew
from qualification import QualificationResult
from database import Conversation, ExecutionHistory, get_session
from errors import AppError, ErrorCode, classify_exception, http_status_for
from github_access import GitHubAccessProblem, check_github_access
from github_guards import WORK_BRANCH_PREFIX
from limits import check_qualify_rate, check_user_execution_quota, record_execution_launch
from logs import get_logger
from orphans import sweep_stale_executions
from schemas import UserRequestInput, WorkflowExecutionInput

log = get_logger("routes_execute")

router = APIRouter()

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

@router.post("/api/qualify", response_model=QualificationResult)
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

@router.post("/api/execute")
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
    # crew_run.CrewRun) : jamais de branche/commit/PR à vérifier pour ce
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
    resume_outputs = execution_resume.resumable_outputs(session, data, user.get("id"), conversation.id)

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
