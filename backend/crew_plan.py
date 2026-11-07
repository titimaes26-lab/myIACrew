"""Plan de l'Architecte transmis à l'Analyste : rappel déterministe dans sa description (avant sa planification) et contrôle de recoupement."""
from typing import Optional

from crew_guardrails import _ARCH_FILE_LINE, _markdown_section

PLAN_BRIEFING_TITLE = "PLAN DE L'ARCHITECTE À SUIVRE"


def plan_briefing(architecture_raw: str) -> str:
    """Rappel DÉTERMINISTE des fichiers du plan de l'Architecte, ajouté à la description de la tâche de l'Analyste : la planification
    interne de CrewAI ne lit que cette description, jamais le contexte des tâches précédentes (exécution 137 : l'Analyste, sans le
    plan dans SA planification, a inventé d'autres améliorations sur un autre projet). Vide sans section « Fichiers à créer ou modifier »."""
    section = _markdown_section((architecture_raw or "").replace("\u2019", "'"), "Fichiers à créer ou modifier")
    lines = [m.group(0).strip() for m in _ARCH_FILE_LINE.finditer(section or "")]
    if not lines:
        return ""
    body = "\n".join(lines).replace("{", "(").replace("}", ")")      # la description peut être ré-interpolée : jamais d'accolades
    return (f"{PLAN_BRIEFING_TITLE} (non négociable : produis CES fichiers, sur le repository cible, sans ajouter d'autre amélioration) :\n{body}")


def plan_mismatch_issue(planned: set[str], delivered: set[str]) -> Optional[str]:
    """Message si AUCUN fichier livré ne figure dans le plan de l'Architecte (même chemin, ou même fin de chemin), sinon None."""
    if not planned or not delivered:
        return None
    if any(d == p or d.endswith("/" + p) or p.endswith("/" + d) for d in delivered for p in planned):
        return None
    return ("Aucun fichier livré ne figure dans le plan de l'Architecte. Fichiers attendus : " + ", ".join(sorted(planned))
            + ". Reprends ce plan (repository cible), sans ajouter d'autre amélioration.")
