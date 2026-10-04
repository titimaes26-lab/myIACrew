"""Blocs de modification ciblée <<<MODIFICATION: …>>> (CHERCHER / REMPLACER) : lecture et application."""
import re
import analyst_blocks

# Modification ciblée d'un fichier EXISTANT, pour ne pas le réécrire en entier :
#   <<<MODIFICATION: chemin>>> puis un ou plusieurs blocs
#   "<<<<<<< CHERCHER" / texte exact / "=======" / texte de remplacement / ">>>>>>> REMPLACER",
#   puis <<<FIN_MODIFICATION>>>. Résolue EN PYTHON contre le fichier d'origine (voir
#   review_diagnostic_output) : en aval, le fichier modifié est un fichier complet comme les autres.
EDIT_START = re.compile(r"^<<<\s*MODIFICATION\s*:\s*(.*?)\s*>{3,}.*$", re.IGNORECASE)

EDIT_END = re.compile(r"^<<<\s*FIN[\s_]+MODIFICATION\s*>{3,}.*$", re.IGNORECASE)

EDIT_SEARCH = re.compile(r"^<{7}\s*CHERCHER\s*$", re.IGNORECASE)

EDIT_SEPARATOR = re.compile(r"^={7}\s*$")

EDIT_REPLACE = re.compile(r"^>{7}\s*REMPLACER\s*$", re.IGNORECASE)

def parse_edit_sections(text: str) -> tuple[dict[str, list[tuple[str, str]]], dict[str, str]]:
    """({chemin: [(texte à chercher, remplacement), ...]}, {chemin: raison} des blocs inexploitables).

    Même philosophie que analyst_blocks.parse_file_sections : un bloc incomplet (balise de fin ou séparateur
    manquant, réponse coupée) rend le fichier inexploitable, jamais appliqué à moitié. En cas de
    chemin dupliqué, la DERNIÈRE version fait foi."""
    edits: dict[str, list[tuple[str, str]]] = {}
    broken: dict[str, str] = {}
    path: str | None = None
    raw_path = ""
    blocks: list[tuple[str, str]] = []
    state = "out"  # out | search | replace
    search: list[str] = []
    replace: list[str] = []
    in_edit = False

    def fail(reason: str) -> None:
        key = path or raw_path.strip() or "(chemin vide)"
        edits.pop(key, None)
        broken[key] = reason

    for line in (text or "").splitlines():
        stripped = analyst_blocks._undecorate(line.strip())
        start = EDIT_START.match(stripped)
        if start and state == "out":
            if in_edit:
                fail("balise <<<FIN_MODIFICATION>>> manquante")
            in_edit, raw_path, path, blocks = True, start.group(1) or " ", analyst_blocks.normalize_path(start.group(1)), []
            continue
        if not in_edit:
            continue
        if EDIT_END.match(stripped) and state == "out":
            if path is None:
                fail("chemin invalide")
            elif not blocks:
                fail("aucun bloc CHERCHER / REMPLACER")
            else:
                edits[path] = blocks
                broken.pop(path, None)
            in_edit, path, raw_path, blocks = False, None, "", []
            continue
        if state == "out" and EDIT_SEARCH.match(stripped):
            state, search, replace = "search", [], []
        elif state == "search" and EDIT_SEPARATOR.match(stripped):
            state = "replace"
        elif state == "replace" and EDIT_REPLACE.match(stripped):
            blocks.append(("\n".join(search), "\n".join(replace)))
            state = "out"
        elif state == "search":
            search.append(line)
        elif state == "replace":
            replace.append(line)
    if in_edit:
        fail("balise <<<FIN_MODIFICATION>>> manquante ou bloc incomplet : contenu probablement tronqué")
    return edits, broken

def apply_edits(base: str, blocks: list[tuple[str, str]]) -> tuple[str | None, str | None]:
    """(contenu modifié, None) ou (None, raison). Chaque texte cherché doit apparaître EXACTEMENT
    une fois dans le fichier (au moment où son bloc est appliqué) : jamais de remplacement
    deviné au mauvais endroit."""
    crlf = "\r\n" in base
    content = base.replace("\r\n", "\n") if crlf else base
    for number, (search, replace) in enumerate(blocks, start=1):
        if not search.strip():
            return None, f"bloc {number} : texte à chercher vide"
        occurrences = content.count(search)
        if occurrences == 0:
            return None, (
                f"bloc {number} : texte à chercher introuvable dans le fichier d'origine "
                "(copie-le EXACTEMENT depuis ta lecture, indentation comprise)"
            )
        if occurrences > 1:
            return None, (
                f"bloc {number} : texte à chercher présent {occurrences} fois, ajoute des lignes "
                "de contexte pour le rendre unique"
            )
        content = content.replace(search, replace, 1)
    return (content.replace("\n", "\r\n") if crlf else content), None
