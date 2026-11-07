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


def _covers(delivered: str, planned: str) -> bool:
    return delivered == planned or delivered.endswith("/" + planned) or planned.endswith("/" + delivered)


def _covered_files(planned: set[str], delivered: set[str]) -> set[str]:
    """Fichiers du plan couverts par la livraison, UN fichier livré n'en couvrant qu'UN (égalité exacte d'abord, puis fin de chemin) :
    un `index.ts` livré ne couvre pas quatre `…/index.ts` prévus."""
    free, covered = set(delivered), set[str]()
    for exact in (True, False):
        for p in sorted(planned - covered):
            match = next((d for d in sorted(free) if (d == p if exact else _covers(d, p))), None)
            if match is not None:
                free.discard(match)
                covered.add(p)
    return covered


def plan_mismatch_issue(planned: set[str], delivered: set[str], withdrawn: frozenset[str] | set[str] = frozenset()) -> Optional[str]:
    """Message si l'Analyste s'écarte du plan de l'Architecte, sinon None : AUCUN fichier livré n'y figure (même chemin ou même fin de
    chemin), ou MOINS DE LA MOITIÉ des fichiers du plan sont livrés (exécution 138 : 1 fichier sur 6). Un fichier déclaré « NON réalisé »
    (`withdrawn`) compte comme traité : l'Analyste peut renoncer, pas se taire."""
    if not planned or not delivered:
        return None
    done = _covered_files(planned, delivered)
    if not done:
        return ("Aucun fichier livré ne figure dans le plan de l'Architecte. Fichiers attendus : " + ", ".join(sorted(planned))
                + ". Reprends ce plan (repository cible), sans ajouter d'autre amélioration.")
    handled = done | (set(withdrawn) & planned)
    if len(handled) * 2 >= len(planned):
        return None
    return (f"{len(done)} fichier(s) du plan de l'Architecte sur {len(planned)} seulement. Manquants : " + ", ".join(sorted(planned - handled))
            + ". Livre-les aussi, ou écris « NON réalisé » pour ceux que tu abandonnes.")
