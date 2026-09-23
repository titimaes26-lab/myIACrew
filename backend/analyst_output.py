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
# "**Fichier : `x`**", "Fichier : x (modifié)". LOOSE_FILE_HEADING sert seulement à
# détecter qu'un titre de fichier était VISÉ, même si son bloc n'a pas pu être extrait.
FILE_HEADING = re.compile(r"^(?:#{2,4}\s*)?\**\s*Fichier\s*:\s*(.+?)\s*$", re.IGNORECASE)
LOOSE_FILE_HEADING = re.compile(r"^\W{0,6}Fichier\s*:", re.IGNORECASE | re.MULTILINE)
FENCE_LINE = re.compile(r"^(`{3,}|~{3,})\s*([^`\s]*)")

# Seules les lignes de COMMENTAIRE sont inspectées : un "..." peut apparaître légitimement
# dans du code (spread JS `...props`, texte d'interface), alors qu'un commentaire
# "// ... reste du code inchangé" ne l'est jamais dans un fichier complet. "#" n'est un
# commentaire que pour certaines extensions (ailleurs, c'est un titre Markdown, un sélecteur
# CSS d'id...) et les fichiers de texte libre ne sont pas inspectés du tout.
SLASH_COMMENT = re.compile(r"^\s*(//|/\*|\*|\{/\*|<!--)")
HASH_COMMENT = re.compile(r"^\s*#")
HASH_COMMENT_EXTENSIONS = {"py", "yaml", "yml", "sh", "toml", "rb"}
PROSE_EXTENSIONS = {"md", "mdx", "txt", "rst"}
# Une ellipse seule ("// ...") ou suivie d'un mot de raccourci ("// ... reste", "# ... code
# existant") trahit un fichier incomplet ; "// ...args are forwarded" (commentaire légitime
# sur un spread) ou un "TODO : à compléter" dans un fichier par ailleurs complet, non.
SHORTCUT_WORDS = (
    r"(reste|rest|code|existing|existant|inchang[ée]e?s?|unchanged|autres?|others?|same|"
    r"m[êe]me|previous|pr[ée]c[ée]dente?s?|etc|remaining|suite)(?![\w])"
)
PLACEHOLDER_IN_COMMENT = re.compile(
    r"^\s*(//|#|/\*|\*|\{/\*|<!--)\s*(\.{3}|…)\s*($|\*/|\*/\}|-->|" + SHORTCUT_WORDS + r")"
    r"|reste du (code|fichier|composant)"
    r"|code inchang[ée]"
    r"|rest of (the )?(code|file|component)"
    r"|(existing|unchanged) code\s*(\.{3}|…)"
    r"|(\.{3}|…)\s*(existing|unchanged) code",
    re.IGNORECASE,
)

# Marqueur que diagnostic_task utilise pour signaler un fichier volontairement NON fourni (voir
# tasksquestion.yaml) : une sortie sans aucun bloc de fichier mais qui l'emploie est un choix
# assumé et documenté, pas un oubli de format.
NOT_DELIVERED_MARKER = re.compile(r"non\s+r[ée]alis[ée]", re.IGNORECASE)

# La section "Auto-revue" (voir diagnostic_task) vient APRÈS les fichiers et peut citer des
# extraits sous un titre "Fichier : <chemin>" : on arrête l'extraction à ce titre pour qu'un
# extrait ne soit jamais pris pour le contenu du fichier.
SELF_REVIEW_HEADING = re.compile(r"^#{1,4}\s*\**\s*auto[- ]?revue", re.IGNORECASE)
ANY_FENCE = re.compile(r"^\s*(`{3,}|~{3,})", re.MULTILINE)

# Préfixe de l'erreur renvoyée par un fetch (voir build_delivery_report) pour un fichier qui
# EXISTE mais dont le contenu n'a pas pu être lu (binaire, encodage) : à ne pas confondre avec
# un fichier absent.
PRESENT_UNREADABLE = "PRÉSENT_ILLISIBLE"

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


def parse_file_sections(text: str) -> tuple[list[dict], list[str]]:
    """(fichiers extraits, chemins annoncés dont le bloc est inexploitable).

    Un bloc ouvert par ``` se ferme sur une ligne composée uniquement du même caractère, au
    moins aussi longue. Les blocs IMBRIQUÉS sont suivis : une ligne ```bash (avec un langage)
    à l'intérieur ouvre un sous-bloc, fermé par le ``` suivant — un README encadré par ```
    au lieu de ```` n'est donc pas coupé à son premier exemple de commande. Un bloc jamais
    fermé (réponse coupée par la limite de tokens) est signalé, jamais committé à moitié.
    En cas de chemin dupliqué, la version la plus LONGUE gagne : un doublon est presque
    toujours un extrait cité plus bas, jamais une réécriture plus courte du fichier entier.
    """
    if not text:
        return [], []
    lines = text.splitlines()
    files: dict[str, str] = {}
    broken: list[str] = []
    i = 0
    while i < len(lines):
        if SELF_REVIEW_HEADING.match(lines[i].strip()):
            break
        heading = FILE_HEADING.match(lines[i].strip())
        if not heading:
            i += 1
            continue
        path = normalize_path(heading.group(1))
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        fence = FENCE_LINE.match(lines[j].strip()) if j < len(lines) else None
        if not fence:
            # Un titre sans bloc annoncé lui-même "NON réalisé" est un choix documenté.
            if path and not NOT_DELIVERED_MARKER.search(lines[i]):
                broken.append(f"{path} (titre sans bloc de code juste en dessous)")
            i += 1
            continue
        fence_str = fence.group(1)
        body: list[str] = []
        depth = 0
        k = j + 1
        closed = False
        while k < len(lines):
            stripped = lines[k].strip()
            inner = FENCE_LINE.match(stripped)
            if inner and inner.group(1)[0] == fence_str[0]:
                is_bare = set(stripped) == {fence_str[0]}
                if is_bare and depth == 0 and len(stripped) >= len(fence_str):
                    closed = True
                    break
                if is_bare:
                    depth = max(depth - 1, 0)
                elif inner.group(2):
                    depth += 1
            body.append(lines[k])
            k += 1
        if not path:
            broken.append(f"{heading.group(1).strip()} (chemin invalide)")
        elif not closed:
            broken.append(f"{path} (bloc de code jamais fermé : contenu probablement tronqué)")
        else:
            content = "\n".join(body)
            content = content + "\n" if content and not content.endswith("\n") else content
            if len(content) > len(files.get(path, "")):
                files[path] = content
        i = k + 1
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
            is_comment = SLASH_COMMENT.match(line) or (hash_is_comment and HASH_COMMENT.match(line))
            if is_comment and PLACEHOLDER_IN_COMMENT.search(line):
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

    fetch(path) -> (contenu, erreur) : contenu None si le fichier est absent ou illisible, avec
    l'erreur correspondante (préfixée par PRESENT_UNREADABLE s'il existe mais n'a pas pu être lu). Les résultats sont étiquetés [vérifié outil] : ils proviennent
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
        if actual is None:
            rows.append(f"### {path}\n- Présence : ABSENT [vérifié outil] — {error or 'introuvable'}")
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
