"""Parcours d'une exécution : étapes de chaque workflow, reprise, erreur d'étape et mise en forme du résultat des agents."""
from typing import Optional

# --- CONSTANTES DE FORMAT MARKDOWN POUR LES AGENTS (PARTAGÉES AVEC MAIN.PY) ---
AGENT_SECTION_SEPARATOR = "\n\n---\n\n"

AGENT_SECTION_REGEX_PATTERN = r'\n\n---\n\n## '

MAX_AGENT_OUTPUT_SIZE = 10_000_000  # 10MB par agent

MAX_AGENT_NAME_LENGTH = 200

# Étapes de chaque workflow, dans l'ordre (clés alignées sur WORKFLOW_STEPS côté frontend) : source
# unique, utilisée par run_dynamic_crew ET par main.py (reprise d'une exécution en échec).
WORKFLOW_STEP_KEYS = {
    "ANALYSE_ONLY": ["design", "architecture"],
    "BUGFIX": ["diagnostic", "development", "qa"],
    "FEATURE": ["architecture", "diagnostic", "development", "qa"],
    "DESIGN_AND_DEV": ["design", "architecture", "diagnostic", "development", "qa"],
}

# Seules ces étapes peuvent être reprises d'une exécution précédente : leur sortie est un TEXTE sans
# effet de bord. development écrit sur GitHub et qa en dépend : on les rejoue toujours.
RESUMABLE_STEPS = ("design", "architecture", "diagnostic")

# Rôle porté par CrewStepError quand l'échec survient APRÈS la dernière étape (agrégation du résultat).
FINALIZATION_ROLE = "finalisation du résultat"

def workflow_step_keys(request_type: str, scope: Optional[str] = None) -> list[str]:
    # Tout type inconnu retombe sur le workflow complet, comme run_dynamic_crew l'a toujours fait.
    keys = list(WORKFLOW_STEP_KEYS.get(request_type, WORKFLOW_STEP_KEYS["DESIGN_AND_DEV"]))
    # Une petite FEATURE saute l'architecture : le Diagnostic part directement de la demande et du repo.
    if request_type == "FEATURE" and scope == "PETIT":
        keys.remove("architecture")
    return keys

def resumable_prefix(step_keys: list[str], saved_outputs: dict[str, str]) -> list[str]:
    """Étapes réutilisables d'une exécution précédente : le PRÉFIXE continu des étapes du workflow qui
    sont reprenables ET sauvegardées. Jamais une étape isolée après un trou : chaque étape lit les
    précédentes en contexte, sauter l'une d'elles puis en réutiliser une suivante serait incohérent."""
    prefix: list[str] = []
    for key in step_keys:
        if key in RESUMABLE_STEPS and (saved_outputs.get(key) or "").strip():
            prefix.append(key)
        else:
            break
    return prefix

class CrewStepError(Exception):
    """Erreur levée quand le crew échoue à une étape précise (voir run_dynamic_crew).

    Conserve le message de l'exception d'origine (str(e) identique) pour que
    crew_retry.retry_on_rate_limit_async continue de détecter les erreurs de quota/rate-limit
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

def _format_crew_result(result, task_durations: Optional[dict[str, float]] = None) -> str:
    """Combine les sorties de toutes les tâches exécutées, pas seulement la dernière.

    result.raw ne reflète que la sortie de la dernière tâche du crew. Pour un workflow
    à plusieurs tâches (ex: ANALYSE_ONLY = design_task puis architecture_task), le
    contenu produit par les tâches précédentes serait sinon silencieusement perdu et
    jamais renvoyé à l'utilisateur.

    task_durations : mapping optionnel agent_name -> secondes d'exécution (voir
    run_dynamic_crew, construit depuis Task.execution_duration). Quand une durée est
    connue pour un agent, elle est encodée juste après son heading via le même marqueur
    HTML que celui écrit par _persist_completed_agent (main.py), pour que le frontend
    n'ait qu'une seule logique d'extraction à implémenter, que la section vienne du
    polling progressif ou de ce résultat final.

    Format final: sections séparées par AGENT_SECTION_SEPARATOR ("\n\n---\n\n")
    """
    tasks_output = getattr(result, "tasks_output", None)
    if not tasks_output or len(tasks_output) <= 1:
        return str(result.raw) if hasattr(result, "raw") else str(result)

    sections = []
    for agent_name, raw in _iter_task_sections(result):
        duration = (task_durations or {}).get(agent_name)
        if duration is not None:
            sections.append(f"## {agent_name}\n<!--agent-duration:{duration:.2f}-->\n\n{raw}")
        else:
            sections.append(f"## {agent_name}\n\n{raw}")
    return AGENT_SECTION_SEPARATOR.join(sections)
