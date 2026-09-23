"""Lecture déterministe (en Python, sans LLM) de la sortie de diagnostic_task.

diagnostic_task (voir tasksquestion.yaml) rédige, pour chaque fichier, un titre
"### Fichier : <chemin>" suivi d'un bloc de code contenant son contenu COMPLET. Jusqu'ici,
developer_agent devait recopier ce contenu à la main dans ses appels d'outils — la principale
source de troncature silencieuse du pipeline. Ce module extrait ces fichiers une fois pour
toutes, pour que :
- le guardrail de diagnostic_task rejette une sortie incomplète AVANT qu'elle n'arrive au
  Développeur (voir find_placeholders) ;
- le Développeur committe le contenu extrait tel quel, sans le recopier (voir
  github_commit_analyst_files, crewquestion.py) ;
- la QA compare le contenu RÉELLEMENT présent sur la branche à celui rédigé par l'Analyste,
  sans devoir le faire "à l'œil" (voir build_delivery_report).
"""
import difflib
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePosixPath
from typing import Callable

from tools import check_syntax_content

# Tolère les variantes de titre qu'un LLM produit en pratique : "### Fichier : x",
# "**Fichier : `x`**", "### 1. Fichier : x", "### 📄 Fichier : x", "Fichier : x (modifié)".
# LOOSE_FILE_HEADING sert seulement à détecter qu'un titre de fichier était VISÉ, même si son
# bloc n'a pas pu être extrait.
_HEADING_PREFIX = r"(?:#{1,4}\s*)?[^\w\s`]{0,4}\s*(?:\d{1,2}[.)]\s*)?\**\s*"
FILE_HEADING = re.compile(r"^" + _HEADING_PREFIX + r"Fichier\s*:\s*(.+?)\s*$", re.IGNORECASE)
LOOSE_FILE_HEADING = re.compile(r"^\W{0,8}(?:\d{1,2}[.)]\s*)?\W{0,4}Fichier\s*:", re.IGNORECASE | re.MULTILINE)
FENCE_LINE = re.compile(r"^(`{3,}|~{3,})\s*([^`\s]*)")

# Seules les lignes de COMMENTAIRE sont inspectées : un "..." peut apparaître légitimement
# dans du code (spread JS `...props`, texte d'interface), alors qu'un commentaire
# "// ... reste du code inchangé" ne l'est jamais dans un fichier complet. "#" n'est un
# commentaire que pour certaines extensions (ailleurs, c'est un titre Markdown, un sélecteur
# CSS d'id...) et les fichiers de texte libre ne sont pas inspectés du tout.
COMMENT_MARKER = re.compile(r"^\s*(//|/\*+|\*|\{/\*|<!--|#)\s*")
HASH_COMMENT_EXTENSIONS = {"py", "yaml", "yml", "sh", "toml", "rb"}
PROSE_EXTENSIONS = {"md", "mdx", "txt", "rst"}
# Deux formes de raccourci, testées sur le TEXTE du commentaire (marqueur retiré) :
# 1. une ellipse en tête, seule ou suivie d'un mot de raccourci : "// ...", "// ... reste",
#    "# ... code existant" — mais pas "// ...args are forwarded" (commentaire sur un spread) ;
# 2. un commentaire qui n'est QUE la formule de raccourci : "// reste du code inchangé",
#    "/* code existant */" — mais pas "// Code inchangé si l'utilisateur n'est pas connecté",
#    une vraie phrase qui continue après la formule.
SHORTCUT_WORDS = (
    r"(reste|rest|code|existing|existant|inchang[ée]e?s?|unchanged|autres?|others?|same|"
    r"m[êe]me|previous|pr[ée]c[ée]dente?s?|etc|remaining|suite)(?![\w])"
)
LEADING_ELLIPSIS = re.compile(r"^(\.{3}|…)\s*($|[*/}>-]|" + SHORTCUT_WORDS + r")", re.IGNORECASE)
SHORTCUT_ONLY = re.compile(
    r"^(\.{3}|…)?\s*(le\s+|the\s+)?"
    r"(reste du (code|fichier|composant)|code (existant|inchang[ée])|"
    r"rest of (the )?(code|file|component)|(existing|unchanged) code)"
    r"(\s+(inchang[ée]|existant|identique|ici|here|unchanged|as before|comme avant))*"
    r"\s*(\.{3}|…)?\s*[.:;*/}>-]*\s*$",
    re.IGNORECASE,
)

# Marqueur que diagnostic_task utilise pour signaler un fichier volontairement NON fourni (voir
# tasksquestion.yaml) : une sortie sans aucun bloc de fichier mais qui l'emploie est un choix
# assumé et documenté, pas un oubli de format.
NOT_DELIVERED_MARKER = re.compile(r"non\s+r[ée]alis[ée]", re.IGNORECASE)

# La section "Auto-revue" (voir diagnostic_task) vient APRÈS les fichiers et peut citer des
# extraits sous un titre "Fichier : <chemin>" : on arrête l'extraction à ce titre pour qu'un
# extrait ne soit jamais pris pour le contenu du fichier.
SELF_REVIEW_HEADING = re.compile(
    r"^(?:#{1,4}\s*)?[^\w\s]{0,4}\s*\**\s*auto[- ]?revue\b.{0,30}$", re.IGNORECASE
)
ANY_FENCE = re.compile(r"^\s*(`{3,}|~{3,})", re.MULTILINE)

# Préfixe de l'erreur renvoyée par un fetch (voir build_delivery_report) pour un fichier qui
# EXISTE mais dont le contenu n'a pas pu être lu (binaire, encodage) : à ne pas confondre avec
# un fichier absent.
PRESENT_UNREADABLE = "PRÉSENT_ILLISIBLE"
# Préfixe réservé à une absence CONFIRMÉE (404, fichier introuvable) : toute autre erreur
# (authentification, rate limit, panne réseau) rend le fichier NON VÉRIFIABLE, jamais ABSENT
# — sinon une panne GitHub passerait pour une preuve outillée de livraison manquante.
FILE_ABSENT = "ABSENT"

MAX_DIFF_LINES_PER_FILE = 40
MAX_PARALLEL_FETCHES = 8


def normalize_path(raw: str) -> str | None:
    """Chemin relatif propre tiré d'un titre, ou None s'il est inutilisable (vide, hors dépôt).

    Retire la mise en forme Markdown (``, **), une note finale entre parenthèses
    ("src/App.tsx (modifié)") et un préfixe "./" ou "/" : un chemin commençant par "/" est
    refusé par l'API GitHub, et un chemin décoré créerait un fichier au nom fantaisiste.
    """
    path = raw.strip().strip("*`'\" ")
    path = re.sub(r"\s+\([^)]*\)$", "", path).strip("*`'\" ")
    path = path.lstrip("/")
    while path.startswith("./"):
        path = path[2:]
    if not path or " " in path or ".." in PurePosixPath(path).parts:
        return None
    return path


def _extension(path: str) -> str:
    return path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""


def _find_closing_fence(lines: list[str], start: int, end: int, fence_str: str, prose: bool) -> int | None:
    """Index de la ligne qui ferme le bloc ouvert juste avant `start`, cherchée avant `end` (le
    titre de fichier suivant ou la fin), ou None si le bloc n'est jamais fermé.

    Pour un fichier de texte libre (Markdown...), c'est la DERNIÈRE clôture de la section : un
    README encadré par ``` contient souvent ses propres blocs ``` (avec ou sans langage), qu'on
    ne peut pas distinguer ligne à ligne d'une clôture. Pour du code, la première clôture au
    niveau 0, en suivant les sous-blocs ouverts avec un langage (```bash).
    """
    def is_closing(line: str) -> bool:
        stripped = line.strip()
        return bool(stripped) and set(stripped) == {fence_str[0]} and len(stripped) >= len(fence_str)

    if prose:
        candidates = [k for k in range(start, end) if is_closing(lines[k])]
        return candidates[-1] if candidates else None
    depth = 0
    for k in range(start, end):
        inner = FENCE_LINE.match(lines[k].strip())
        if not inner or inner.group(1)[0] != fence_str[0]:
            continue
        if is_closing(lines[k]):
            if depth == 0:
                return k
            depth -= 1
        elif inner.group(2):
            depth += 1
    return None


def parse_file_sections(text: str) -> tuple[list[dict], list[str]]:
    """(fichiers extraits, chemins annoncés dont le bloc est inexploitable).

    Chaque section va d'un titre "Fichier : <chemin>" au titre suivant (ou à la section
    "Auto-revue", qui arrête l'extraction : ses extraits ne sont jamais des fichiers). Un bloc
    jamais fermé (réponse coupée par la limite de tokens) est signalé, jamais committé à moitié.
    En cas de chemin dupliqué, la DERNIÈRE version gagne : l'Analyste donne sa correction après
    avoir éventuellement cité le code d'origine (voir la consigne de diagnostic_task, qui réserve
    ce titre au contenu final).
    """
    if not text:
        return [], []
    lines = text.splitlines()
    stop = next((n for n, line in enumerate(lines) if SELF_REVIEW_HEADING.match(line.strip())), len(lines))
    headings = [n for n in range(stop) if FILE_HEADING.match(lines[n].strip())]
    files: dict[str, str] = {}
    broken: list[str] = []
    for index, i in enumerate(headings):
        section_end = headings[index + 1] if index + 1 < len(headings) else stop
        raw_path = FILE_HEADING.match(lines[i].strip()).group(1)
        path = normalize_path(raw_path)
        j = i + 1
        while j < section_end and not lines[j].strip():
            j += 1
        fence = FENCE_LINE.match(lines[j].strip()) if j < section_end else None
        if not fence:
            # Un titre sans bloc annoncé lui-même "NON réalisé" est un choix documenté.
            if path and not NOT_DELIVERED_MARKER.search(lines[i]):
                broken.append(f"{path} (titre sans bloc de code juste en dessous)")
            continue
        if not path:
            broken.append(f"{raw_path.strip()} (chemin invalide)")
            continue
        closing = _find_closing_fence(
            lines, j + 1, section_end, fence.group(1), _extension(path) in PROSE_EXTENSIONS
        )
        if closing is None:
            broken.append(f"{path} (bloc de code jamais fermé : contenu probablement tronqué)")
            continue
        content = "\n".join(lines[j + 1:closing])
        files[path] = content + "\n" if content and not content.endswith("\n") else content
    broken = [b for b in broken if b.split(" ", 1)[0] not in files]
    return [{"path": p, "content": c} for p, c in files.items()], broken


def parse_file_blocks(text: str) -> list[dict]:
    return parse_file_sections(text)[0]


def find_placeholders(files: list[dict]) -> list[tuple[str, int, str]]:
    """(chemin, numéro de ligne, ligne) pour chaque commentaire trahissant un fichier incomplet."""
    issues = []
    for f in files:
        ext = _extension(f["path"])
        if ext in PROSE_EXTENSIONS:
            continue
        hash_is_comment = ext in HASH_COMMENT_EXTENSIONS
        for n, line in enumerate(f["content"].splitlines(), start=1):
            marker = COMMENT_MARKER.match(line)
            if not marker or (marker.group(1) == "#" and not hash_is_comment):
                continue
            body = line[marker.end():].strip()
            if LEADING_ELLIPSIS.match(body) or SHORTCUT_ONLY.match(body):
                issues.append((f["path"], n, line.strip()[:120]))
    return issues


def review_diagnostic_output(text: str) -> tuple[list[dict], str | None, set[str]]:
    """(fichiers extraits, problème à renvoyer à l'Analyste, chemins des fichiers fautifs).

    Les chemins fautifs sont ceux qui ne doivent JAMAIS être committés tels quels, même si
    l'Analyste ne corrige pas sa réponse (voir _diagnostic_guardrail, crewquestion.py).
    """
    files, broken = parse_file_sections(text)
    problems: list[str] = []
    if broken:
        problems.append(
            "Fichiers annoncés mais inexploitables (ils ne seront PAS committés) :\n"
            + "\n".join(f"- {b}" for b in broken)
        )
    if not files and not broken:
        # "non réalisé" ne dispense du signalement que si la réponse ne contient AUCUN code :
        # des blocs de code présents mais non extraits sont un problème de format, pas un choix.
        has_code = bool(ANY_FENCE.search(text or ""))
        declared_nothing = NOT_DELIVERED_MARKER.search(text or "") and not has_code
        if LOOSE_FILE_HEADING.search(text or "") or not declared_nothing:
            problems.append(
                "Aucun fichier exploitable trouvé : chaque fichier doit être introduit par une ligne "
                "'### Fichier : <chemin>' suivie IMMÉDIATEMENT d'un bloc de code (```) contenant son "
                "contenu COMPLET. Si tu ne peux livrer aucun fichier, dis-le explicitement en le "
                "marquant 'NON réalisé' avec la raison."
            )
    placeholders = find_placeholders(files)
    if placeholders:
        listing = "\n".join(f"- {p} ligne {n} : {line}" for p, n, line in placeholders[:10])
        problems.append(
            "Contenu incomplet détecté (commentaires de remplacement qui seraient committés tels "
            f"quels) :\n{listing}\nRéécris CES fichiers EN ENTIER, sans aucun raccourci, ou "
            "retire leur bloc et liste-les comme 'NON réalisé' dans ton plan."
        )
    if not problems:
        return files, None, set()
    return files, "\n\n".join(problems), {p for p, _, _ in placeholders}


def format_manifest(files: list[dict]) -> str:
    return "\n".join(
        f"- {f['path']} ({len(f['content'].splitlines())} lignes)" for f in files
    ) or "- (aucun fichier)"


def build_delivery_report(
    files: list[dict],
    fetch: Callable[[str], tuple[str | None, str | None]],
) -> str:
    """Rapport par fichier : présence réelle, identité avec la version de l'Analyste, syntaxe.

    fetch(path) -> (contenu, erreur) : contenu None en cas d'échec, avec l'erreur préfixée par
    FILE_ABSENT (absence confirmée) ou PRESENT_UNREADABLE (présent mais illisible) ; toute autre
    erreur rend le fichier NON VÉRIFIABLE. Les résultats sont étiquetés [vérifié outil] : ils proviennent
    d'une comparaison exacte en Python et de check_syntax_content, jamais d'une lecture LLM.
    """
    if not files:
        return (
            "INFO : l'Analyste n'a fourni aucun fichier exploitable pour cette exécution : rien "
            "à comparer. Vérifie le code avec github_read_file/check_syntax si nécessaire."
        )
    # Lectures en parallèle (une par fichier) : sur un gros lot, des allers-retours GitHub
    # séquentiels ajouteraient des dizaines de secondes à un seul appel d'outil de la QA.
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_FETCHES) as pool:
        fetched = list(pool.map(lambda f: fetch(f["path"]), files))
    rows = []
    for f, (actual, error) in zip(files, fetched):
        path, expected = f["path"], f["content"]
        if actual is None and (error or "").startswith(PRESENT_UNREADABLE):
            rows.append(
                f"### {path}\n- Présence : PRÉSENT [vérifié outil]\n"
                f"- Contenu : NON VÉRIFIABLE, fichier illisible par l'outil — {error}"
            )
            continue
        if actual is None and (error or "").startswith(FILE_ABSENT):
            rows.append(f"### {path}\n- Présence : ABSENT [vérifié outil] — {error}")
            continue
        if actual is None:
            rows.append(
                f"### {path}\n- Présence : NON VÉRIFIABLE (erreur de l'outil, pas une preuve "
                f"d'absence) — {error or 'erreur inconnue'}"
            )
            continue
        if actual.rstrip("\n") == expected.rstrip("\n"):
            match_line = "- Contenu : IDENTIQUE à la version de l'Analyste [vérifié outil]"
        else:
            diff = list(difflib.unified_diff(
                expected.splitlines(), actual.splitlines(),
                fromfile="analyste", tofile="branche", lineterm="", n=1,
            ))
            shown = diff[:MAX_DIFF_LINES_PER_FILE]
            more = f"\n... ({len(diff) - len(shown)} lignes de diff supplémentaires)" if len(diff) > len(shown) else ""
            match_line = (
                "- Contenu : DIVERGENT de la version de l'Analyste [vérifié outil]\n"
                "```diff\n" + "\n".join(shown) + more + "\n```"
            )
        syntax = check_syntax_content(actual, path).strip()
        rows.append(
            f"### {path}\n- Présence : PRÉSENT [vérifié outil]\n{match_line}\n"
            f"- check_syntax : {syntax} [vérifié outil]"
        )
    return "\n\n".join(rows)
