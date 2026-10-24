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

class _EditParser:
    """Automate ligne à ligne de parse_edit_sections : `state` vaut out | search | replace (à l'intérieur d'un bloc CHERCHER/REMPLACER)."""

    def __init__(self) -> None:
        self.edits: dict[str, list[tuple[str, str]]] = {}
        self.broken: dict[str, str] = {}
        self.path: str | None = None
        self.raw_path = ""
        self.blocks: list[tuple[str, str]] = []
        self.state = "out"
        self.search: list[str] = []
        self.replace: list[str] = []
        self.in_edit = False

    def fail(self, reason: str) -> None:
        key = self.path or self.raw_path.strip() or "(chemin vide)"
        self.edits.pop(key, None)
        self.broken[key] = reason

    def begin(self, raw: str) -> None:
        if self.in_edit:
            self.fail("balise <<<FIN_MODIFICATION>>> manquante")
        self.in_edit, self.raw_path, self.path, self.blocks = True, raw or " ", analyst_blocks.normalize_path(raw), []

    def end(self) -> None:
        if self.path is None:
            self.fail("chemin invalide")
        elif not self.blocks:
            self.fail("aucun bloc CHERCHER / REMPLACER")
        else:
            self.edits[self.path] = self.blocks
            self.broken.pop(self.path, None)
        self.in_edit, self.path, self.raw_path, self.blocks = False, None, "", []

    def block_line(self, line: str, stripped: str) -> None:
        if self.state == "out" and EDIT_SEARCH.match(stripped):
            self.state, self.search, self.replace = "search", [], []
        elif self.state == "search" and EDIT_SEPARATOR.match(stripped):
            self.state = "replace"
        elif self.state == "replace" and EDIT_REPLACE.match(stripped):
            self.blocks.append(("\n".join(self.search), "\n".join(self.replace)))
            self.state = "out"
        elif self.state == "search":
            self.search.append(line)
        elif self.state == "replace":
            self.replace.append(line)

    def feed(self, line: str) -> None:
        stripped = analyst_blocks.undecorate(line.strip())
        start = EDIT_START.match(stripped)
        if start and self.state == "out":
            self.begin(start.group(1))
        elif not self.in_edit:
            return
        elif EDIT_END.match(stripped) and self.state == "out":
            self.end()
        else:
            self.block_line(line, stripped)


def parse_edit_sections(text: str) -> tuple[dict[str, list[tuple[str, str]]], dict[str, str]]:
    """({chemin: [(texte à chercher, remplacement), ...]}, {chemin: raison} des blocs inexploitables).

    Même philosophie que analyst_blocks.parse_file_sections : un bloc incomplet (balise de fin ou séparateur
    manquant, réponse coupée) rend le fichier inexploitable, jamais appliqué à moitié. En cas de
    chemin dupliqué, la DERNIÈRE version fait foi."""
    parser = _EditParser()
    for line in (text or "").splitlines():
        parser.feed(line)
    if parser.in_edit:
        parser.fail("balise <<<FIN_MODIFICATION>>> manquante ou bloc incomplet : contenu probablement tronqué")
    return parser.edits, parser.broken

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
