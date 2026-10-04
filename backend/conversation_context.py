"""Rappel des tours précédents d'une conversation, donné aux agents."""
import re
from typing import Optional

import crew_summary

MAX_PRIOR_TURN_SUMMARY_CHARS = 800

MAX_PRIOR_TURN_RESULT_CHARS = 500

def _extract_prior_turn_summary(result_text: str) -> str:
    """Réduit le résultat d'un tour précédent à un texte court utilisable comme contexte.

    Réutilise le "## Résumé" déjà généré pour ce tour (via crew_summary.SUMMARY_SENTINEL) quand il
    existe : c'est déjà une synthèse pensée pour être lue, pas le rapport complet de
    chaque agent. À défaut (résumé absent, ex: génération échouée), on retombe sur un
    simple tronquage du résultat brut plutôt que de ne rien montrer.
    """
    if not result_text:
        return ""

    idx = result_text.rfind(crew_summary.SUMMARY_SENTINEL)
    if idx != -1:
        tail = result_text[idx + len(crew_summary.SUMMARY_SENTINEL):].strip()
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
    (crew_summary.MAX_SUMMARY_INPUT_CHARS, troncature par tâche).
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
