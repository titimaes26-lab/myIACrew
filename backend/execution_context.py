"""Contexte d'une exécution : état d'une tentative, consignes et entrées du crew, aperçu du dépôt, plan et branche
des tours précédents, reprise d'une exécution en échec."""
import asyncio
import re
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, col, func, select

import database
from metrics_collect import ExecutionMetrics
from metrics_roles import step_for_role
from conversation_context import MAX_PRIOR_TURNS_IN_CONTEXT, build_conversation_context
from crew_workflow import AGENT_SECTION_REGEX_PATTERN, workflow_step_keys
from database import Conversation, ExecutionHistory
from github_snapshot import GitHubVerificationUnavailable, build_repo_snapshot, get_branch_head_sha
from logs import get_logger
from schemas import WorkflowExecutionInput

log = get_logger("execution_context")

@dataclass
class RunState:
    """État d'UNE tentative d'exécution, lu par le chemin d'échec même quand le crew a planté en route :
    SHA de la branche de travail AVANT le crew (None tant que non capturé) et métriques de la tentative."""
    sha_before: Optional[str] = None
    metrics: Optional[ExecutionMetrics] = None

def repo_instructions(
    has_repo_target: bool, data: "WorkflowExecutionInput", work_branch: str,
    base_branch: Optional[str], branch_exists: bool,
) -> str:
    """Consignes de cible données aux agents (repository, branches, ou espace de travail local)."""
    if not has_repo_target:
        return (
            "Aucun repository GitHub cible fourni : travaille uniquement dans l'espace de travail local de "
            "cette conversation (chemins de fichiers relatifs, lus avec read_a_files_content). Cet espace est "
            "VIDE au premier tour d'une conversation : ne présume jamais qu'un fichier non livré par un tour "
            "précédent de cette même conversation existe déjà. N'utilise aucun outil github_* SAUF "
            "github_commit_analyst_files et qa_verify_delivered_files, qui agissent alors sur cet espace."
        )
    header = (
        f"Repository GitHub cible : {data.repo_owner}/{data.repo_name}\n"
        f"Branche de base : {base_branch}\n"
        f"Branche de travail à créer et utiliser pour toute écriture : {work_branch}\n"
    )
    # branch_exists vient d'un appel GitHub LIVE fait avant le crew, pas du statut d'un tour précédent (un
    # tour « failed » peut avoir réellement poussé des commits). « Inconnu » est traité comme « n'existe
    # pas » : lire la branche de base en attendant est le choix le moins risqué.
    if branch_exists:
        return header + (
            "Cette branche de travail EXISTE DÉJÀ sur GitHub (réutilisée d'un tour précédent de cette "
            f"conversation) : pour toute lecture, lis-la directement avec branch={work_branch}."
        )
    return header + (
        "Cette branche de travail N'EXISTE PAS ENCORE sur GitHub (sera créée par la tâche de commit qui "
        f"suit) : pour toute lecture, lis sur branch={base_branch} en attendant."
    )

def crew_inputs(
    data: "WorkflowExecutionInput", conversation_id: int, final_prompt: str, conversation_context: str,
    work_branch: str, base_branch: Optional[str], has_repo_target: bool, branch_exists: bool, repo_snapshot: str = "",
    previous_plan: str = "",
) -> dict:
    return {
        'user_request': final_prompt,
        'conversation_context': conversation_context,
        'repo_owner': data.repo_owner or '',
        'repo_name': data.repo_name or '',
        # `or 'main'` : base_branch est None sans repository cible, et les tâches interpolent toujours {base_branch}.
        'base_branch': base_branch or 'main',
        'work_branch': work_branch,
        # Isole l'espace de travail LOCAL de chaque conversation (mode sans repository cible).
        'conversation_id': str(conversation_id),
        'repo_instructions': repo_instructions(has_repo_target, data, work_branch, base_branch, branch_exists),
        # Aperçu du repo lu en Python (vide : l'agent lit lui-même avec ses outils).
        'repo_snapshot': repo_snapshot,
        # Plan d'architecture du tour précédent de la conversation (vide : l'Architecte part de zéro).
        'previous_plan': previous_plan,
    }

async def prefetch_repo_snapshot(
    data: "WorkflowExecutionInput", work_branch: str, base_branch: Optional[str], has_repo_target: bool, branch_exists: bool,
    resume_outputs: Optional[dict[str, str]] = None,
) -> str:
    """Aperçu du repo pour l'Architecte ET le Diagnostic (racine, résumé de package.json/tsconfig.json, src), lu en
    Python plutôt que par leurs appels d'outils : autant de tours de LLM en moins, et la même vue pour les deux.
    Seulement avec un repository cible et si l'une de ces deux étapes va réellement tourner (pas réutilisée par une
    reprise, pas sautée par une petite FEATURE).
    Best-effort : toute erreur renvoie "" et l'agent lit lui-même (même règle de branche que repo_instructions)."""
    if not has_repo_target:
        return ""
    steps = workflow_step_keys(data.target_workflow, data.scope)
    if not any(step in steps and step not in (resume_outputs or {}) for step in ("architecture", "diagnostic")):
        return ""
    branch = work_branch if branch_exists else (base_branch or "main")
    try:
        return await asyncio.to_thread(build_repo_snapshot, data.repo_owner, data.repo_name, branch)
    except Exception as e:
        log.warning(f"aperçu du repository non lu ({type(e).__name__}: {e}) : l'agent lira lui-même.")
        return ""

MAX_PREVIOUS_PLAN_CHARS = 6000

AGENT_DURATION_MARKER = re.compile(r"<!--agent-duration:[0-9.]+-->\s*")

def previous_architecture_plan(
    session: Session, data: "WorkflowExecutionInput", has_repo_target: bool, user_id, conversation_id: int, current_id: int,
) -> str:
    """Plan de l'Architecte du dernier tour RÉUSSI de cette conversation qui en a produit un (parmi les 3 derniers),
    sur le même repository cible, ou "". Sert de base à l'Architecte (il ne décrit alors que ce qui change) ; il peut
    être périmé, la consigne lui demande de le confronter à l'aperçu du repository."""
    target = (data.repo_owner, data.repo_name) if has_repo_target else (None, None)
    rows = session.exec(
        select(ExecutionHistory)
        .where(ExecutionHistory.conversation_id == conversation_id)
        .where(ExecutionHistory.user_id == user_id)
        .where(ExecutionHistory.id != current_id)
        .where(ExecutionHistory.status == "success")
        .order_by(col(ExecutionHistory.created_at).desc())
        .limit(3)
    ).all()
    for row in rows:
        if (row.repo_owner or None, row.repo_name or None) != target:
            continue
        if "architecture" not in workflow_step_keys(row.workflow, row.scope):
            continue
        for agent_name, content in parse_completed_agents(row.result or "").items():
            if step_for_role(agent_name) != "architecture":
                continue
            body = content.split("\n", 1)[1] if content.startswith("## ") and "\n" in content else content
            body = AGENT_DURATION_MARKER.sub("", body).strip()
            if body:
                cut = "\n[… tronqué]" if len(body) > MAX_PREVIOUS_PLAN_CHARS else ""
                return body[:MAX_PREVIOUS_PLAN_CHARS] + cut
    return ""

async def load_previous_plan(
    data: "WorkflowExecutionInput", has_repo_target: bool, user_id, conversation_id: int, current_id: int,
    resume_outputs: Optional[dict[str, str]],
) -> str:
    """Plan du tour précédent à donner à l'Architecte, ou "" : seulement si son étape va réellement tourner (ni
    sautée par une petite FEATURE, ni réutilisée par une reprise). Best-effort : une erreur donne ""."""
    if "architecture" not in workflow_step_keys(data.target_workflow, data.scope) or "architecture" in (resume_outputs or {}):
        return ""

    def read() -> str:
        with Session(database.engine) as plan_session:
            return previous_architecture_plan(plan_session, data, has_repo_target, user_id, conversation_id, current_id)

    try:
        return await asyncio.to_thread(read)
    except Exception as e:
        log.warning(f"plan du tour précédent non lu ({type(e).__name__}: {e}) : l'Architecte part de zéro.")
        return ""

async def capture_branch_sha(data: "WorkflowExecutionInput", work_branch: str, has_repo_target: bool) -> Optional[str]:
    """SHA de la branche de travail AVANT le crew : repère de verify_github_delivery pour distinguer « cette
    exécution a poussé un commit » de « une branche/PR d'un tour précédent existe toujours ». None = branche
    absente OU vérification indisponible : traité pareil (best-effort), une panne réseau n'empêche pas le crew."""
    # Pour TOUT run avec repository cible (pas seulement ceux qui vérifient la livraison) : les consignes
    # données aux agents dépendent de l'existence de la branche, ANALYSE_ONLY compris.
    if not has_repo_target:
        return None
    try:
        return await asyncio.to_thread(get_branch_head_sha, data.repo_owner, data.repo_name, work_branch)
    except GitHubVerificationUnavailable:
        return None

def parse_completed_agents(result_text: str) -> dict[str, str]:
    """Découpe le résultat combiné du crew en sections par agent.

    Format attendu:
    - Sections séparées par la frontière littérale: '\n\n---\n\n## AgentName'
    - Chaque section: '## AgentName\n\n...contenu...'
    - Le résumé final (optionnel) est marqué par '<!--crew-summary-->' et n'est PAS retourné
    - Cette fonction ignore le résumé et les sections après le marqueur de résumé

    Exemple:
        Input: "## Agent1\n\n...content1...\n\n---\n\n## Agent2\n\n...content2...\n\n<!--crew-summary-->\n\n## Résumé\n\n..."
        Output: {"Agent1": "## Agent1\n\n...content1...", "Agent2": "## Agent2\n\n...content2..."}

    Args:
        result_text: String markdown du résultat du crew complet ou partiel

    Returns:
        dict[str, str]: {nom_agent: contenu_formaté_avec_heading}
    """
    if not result_text:
        return {}

    # Chercher le sentinel résumé : '<!--crew-summary-->' (marqueur du résumé final)
    agents = {}
    summary_marker = "<!--crew-summary-->"

    # Isoler la partie agents (avant le résumé)
    if summary_marker in result_text:
        agents_part = result_text[:result_text.index(summary_marker)]
    else:
        agents_part = result_text

    # Découper par frontière AGENT_SECTION_REGEX_PATTERN
    # Cette frontière contient '\n\n---\n\n## ' donc le split supprime ce texte entre sections
    sections = re.split(AGENT_SECTION_REGEX_PATTERN, agents_part)

    for section in sections:
        if not section.strip():
            continue

        lines = section.split('\n', 1)
        if len(lines) >= 2:
            agent_name = lines[0].strip()
            content = lines[1]
        else:
            agent_name = lines[0].strip()
            content = ""

        # Retirer les marqueurs ## du heading si présents (première section les conserve du split)
        if agent_name.startswith('##'):
            agent_name = agent_name[2:].strip()

        # Ne pas traiter comme agent si le heading ne ressemble pas à un rôle
        # (ex: un heading du contenu d'un agent, pas une vraie frontière)
        if agent_name and len(agent_name) > 2:
            agents[agent_name] = f"## {agent_name}\n\n{content}" if content else f"## {agent_name}"

    return agents

def prior_turns(session: Session, conversation_id: int) -> tuple[list[int], str]:
    """(identifiants des tours encore « running », rappel des derniers tours) d'une conversation. Seuls les
    MAX_PRIOR_TURNS_IN_CONTEXT derniers tours sont lus en entier (pour leur résumé) ; le nombre total sert à signaler
    ceux qui sont omis. Même contenu que lire toute la conversation, sans charger les résultats des tours anciens."""
    in_conversation = ExecutionHistory.conversation_id == conversation_id
    running_ids = [
        row_id for row_id in session.exec(
            select(ExecutionHistory.id).where(in_conversation).where(ExecutionHistory.status == "running")
        ).all() if row_id is not None
    ]
    total = session.exec(select(func.count()).select_from(ExecutionHistory).where(in_conversation)).one()
    recent = session.exec(
        select(ExecutionHistory).where(in_conversation)
        .order_by(col(ExecutionHistory.created_at).desc(), col(ExecutionHistory.id).desc())
        .limit(MAX_PRIOR_TURNS_IN_CONTEXT)
    ).all()
    return running_ids, build_conversation_context(list(reversed(recent)), total_count=total)

def previous_work_branch(
    session: Session, conversation_id: int, owner: Optional[str], repo: Optional[str], base_branch: Optional[str],
) -> str:
    """Branche de travail du dernier tour de la conversation sur le même repository et la même branche de base,
    quel que soit son statut (« failed » compris : « Relancer » continue sur la même branche et la même PR), ou ""."""
    found = session.exec(
        select(ExecutionHistory.work_branch)
        .where(ExecutionHistory.conversation_id == conversation_id)
        .where(col(ExecutionHistory.work_branch).is_not(None))
        .where(col(ExecutionHistory.work_branch) != "")
        .where(ExecutionHistory.repo_owner == owner)
        .where(ExecutionHistory.repo_name == repo)
        .where(ExecutionHistory.base_branch == base_branch)
        .order_by(col(ExecutionHistory.created_at).desc(), col(ExecutionHistory.id).desc())
        .limit(1)
    ).first()
    return found or ""

def load_qualification_context(conversation_id: int, user_id) -> str | None:
    """Rappel des tours précédents pour /api/qualify, ou None si la conversation est introuvable
    ou n'appartient pas à cet utilisateur. Synchrone (accès DB bloquant) : à appeler via
    asyncio.to_thread, avec sa propre Session, pour ne pas bloquer la boucle asyncio."""
    with Session(database.engine) as session:
        conversation = session.get(Conversation, conversation_id)
        if not conversation or conversation.user_id != user_id:
            return None
        # Seuls les MAX_PRIOR_TURNS_IN_CONTEXT derniers tours servent au contexte : inutile de
        # relire tous les résultats (souvent volumineux) d'une longue conversation à chaque
        # qualification — le nombre total suffit pour signaler les tours omis.
        total = session.exec(
            select(func.count()).select_from(ExecutionHistory)
            .where(ExecutionHistory.conversation_id == conversation.id)
        ).one()
        recent = session.exec(
            select(ExecutionHistory)
            .where(ExecutionHistory.conversation_id == conversation.id)
            .order_by(col(ExecutionHistory.created_at).desc())
            .limit(MAX_PRIOR_TURNS_IN_CONTEXT)
        ).all()
        return build_conversation_context(list(reversed(recent)), total_count=total)


