"""Résultat final d'une exécution : sorties de toutes les tentatives réunies, mise en forme, résumé."""
import time
from typing import Any

import crew_retry
import crew_summary
import crew_workflow


class CombinedCrewResult:
    """Résultat final reconstruit depuis les sorties accumulées par on_task_complete au fil de TOUTES les tentatives
    (et pas depuis le CrewOutput de la seule DERNIÈRE, qui ne couvrirait que les tâches de ce sous-crew en cas de
    reprise), dans l'ordre ORIGINAL des étapes : crew_workflow._format_crew_result et crew_summary._generate_summary
    ne lisent que .tasks_output et .raw."""

    def __init__(self, tasks_output):
        self.tasks_output = tasks_output
        self.raw = str(getattr(tasks_output[-1], "raw", "") or "") if tasks_output else ""


async def build_final_result(
    inputs: dict, request_type: str, scope, step_keys: list[str], completed_outputs: dict[str, Any], selected_tasks: list,
) -> str:
    result = CombinedCrewResult([completed_outputs[k] for k in step_keys if k in completed_outputs])

    # execution_duration reste valide pour chaque Task déjà exécutée, quelle que soit la
    # tentative qui l'a réellement exécutée (start_time/end_time sont posés sur l'objet Task
    # lui-même). Ignore les agents sans durée connue (tâche jamais exécutée après un échec
    # définitif en cours de route) plutôt que d'y mettre None, pour que
    # crew_workflow._format_crew_result n'ait qu'un seul test.
    task_durations = {
        t.agent.role.strip(): t.execution_duration
        for t in selected_tasks
        if t.execution_duration is not None
    }
    formatted = crew_workflow._format_crew_result(result, task_durations)

    # last_execution_time n'est délibérément pas remis à jour avant cet appel :
    # on_task_complete() l'a déjà fait à la fin de la dernière tâche. Le remettre à
    # `time.time()` ici ferait toujours mesurer un écart quasi nul à adaptive_pause() dans
    # crew_summary._generate_summary, forçant une pause maximale systématique au lieu d'une
    # pause proportionnée au temps déjà écoulé depuis le dernier appel Gemini réel.
    summary = await crew_summary._generate_summary(inputs.get('user_request', ''), result)
    summary_body = crew_summary._compose_summary_body(
        list(crew_workflow._iter_task_sections(result)), request_type, scope, summary
    )
    if summary_body:
        formatted = f"{formatted}\n\n{crew_summary.SUMMARY_SENTINEL}\n\n## Résumé\n\n{summary_body}"

    crew_retry.quota_mgr.last_execution_time = time.time()
    return formatted
