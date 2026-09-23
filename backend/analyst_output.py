"""Lecture déterministe (en Python, sans LLM) de la sortie de diagnostic_task.

diagnostic_task (voir tasksquestion.yaml) encadre chaque fichier livré par deux balises seules
sur leur ligne : "<<<FICHIER: <chemin>>>>" puis "<<<FIN_FICHIER>>>", avec le contenu COMPLET
entre les deux. Des balises explicites plutôt que des titres Markdown : un titre "### Fichier :"
se confond avec un commentaire, une citation de code ou un bloc ``` imbriqué, alors que ces
balises n'apparaissent jamais par hasard. Jusqu'ici, developer_agent devait recopier ce contenu
à la main dans ses appels d'outils — la principale source de troncature silencieuse du
pipeline. Ce module extrait ces fichiers une fois pour toutes, pour que :
- le guardrail de diagnostic_task rejette une sortie incomplète AVANT qu'elle n'arrive au
  Développeur (voir review_diagnostic_output) ;
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

FILE_START = re.compile(r"^<<<\s*FICHIER\s*:\s*(.*?)\s*>>>$", re.IGNORECASE)
# FIN_FICHIER avec un "_" : "<FIN FICHIER>" serait lu comme une balise HTML par le rendu Markdown
# de l'interface (et masqué). La variante avec espace reste acceptée si le modèle l'écrit.
FILE_END = re.compile(r"^<<<\s*FIN[\s_]+FICHIER\s*>>>$", re.IGNORECASE)
FENCE_LINE = re.compile(r"^(`{3,}|~{3,})")
ANY_FENCE = re.compile(r"^\s*(`{3,}|~{3,})", re.MULTILINE)

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
# Un espace est exigé entre l'ellipse et le mot : "// ...rest is forwarded" décrit un spread.
LEADING_ELLIPSIS = re.compile(r"^(\.{3}|…)(\s*$|\s*[*/}>-]|\s+" + SHORTCUT_WORDS + r")", re.IGNORECASE)
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
    """Chemin relatif propre tiré d'une balise, ou None s'il est inutilisable.

    Retire seulement une mise en forme Markdown parasite (``, **) et un préfixe "./" ou "/"
    (refusé par l'API GitHub). Tout le reste — espace, note "(extrait)", ".." — rend le chemin
    invalide : mieux vaut refuser un fichier que l'écrire sous un nom fantaisiste.
    """
    path = raw.strip().strip("*`'\" ")
    path = path.lstrip("/")
    while path.startswith("./"):
        path = path[2:]
    if not path or any(c.isspace() for c in path) or ".." in PurePosixPath(path).parts:
        return None
    return path


def _extension(path: str) -> str:
    return path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""


def _strip_outer_fence(body: list[str], prose: bool) -> list[str]:
    """Retire le bloc ``` qui encadre le contenu (utile à l'affichage Markdown du rapport), s'il
    l'encadre ENTIÈREMENT : ouverture en première ligne, clôture en dernière. Les blocs ```
    intérieurs (README, template literal) font partie du fichier et restent intacts."""
    first = next((n for n, line in enumerate(body) if line.strip()), None)
    last = next((n for n in range(len(body) - 1, -1, -1) if body[n].strip()), None)
    if first is None or first == last:
        return body
    opening = FENCE_LINE.match(body[first].strip())
    closing = body[last].strip()
    if not (opening and set(closing) == {opening.group(1)[0]} and len(closing) >= len(opening.group(1))):
        return body
    inner = body[first + 1:last]
    # Un fichier de code ne commence jamais par une ligne ``` : c'est forcément l'enveloppe, même
    # si le code contient lui-même un ``` isolé (template literal). Un fichier de texte (README),
    # si : l'enveloppe n'est retirée que si l'intérieur reste une suite de blocs bien formée —
    # un README qui commence par ```bash et finit par ``` n'est PAS encadré.
    return inner if not prose or _fences_are_balanced(inner) else body


def _fences_are_balanced(lines: list[str]) -> bool:
    """Vrai si chaque bloc ouvert dans `lines` y est refermé (règles CommonMark : une ligne de
    clôture est nue et au moins aussi longue que l'ouverture ; à l'intérieur d'un bloc, une
    ligne ```lang n'est que du contenu)."""
    open_fence: str | None = None
    for line in lines:
        fence = FENCE_LINE.match(line.strip())
        if not fence:
            continue
        if open_fence is None:
            open_fence = fence.group(1)
        elif set(line.strip()) == {open_fence[0]} and len(line.strip()) >= len(open_fence):
            open_fence = None
    return open_fence is None


def parse_file_sections(text: str) -> tuple[list[dict], list[str]]:
    """(fichiers extraits, fichiers annoncés mais inexploitables, avec la raison).

    Seules les balises comptent : un titre, un commentaire ou un extrait cité hors balises
    n'est jamais pris pour un fichier. Une balise d'ouverture sans fermeture (réponse coupée
    par la limite de tokens, ou nouvelle ouverture avant la fermeture) rend le fichier
    inexploitable, jamais committé à moitié. En cas de chemin dupliqué, la DERNIÈRE version
    fait foi — y compris si elle est inexploitable : une version antérieure (souvent une
    citation du code d'origine) n'est alors jamais committée à sa place.
    """
    files: dict[str, str] = {}
    broken: dict[str, str] = {}
    current_path: str | None = None
    current_raw = ""
    body: list[str] = []

    def mark_broken(path: str | None, raw: str, reason: str) -> None:
        key = path or raw.strip() or "(chemin vide)"
        files.pop(key, None)
        broken[key] = reason

    for line in (text or "").splitlines():
        stripped = line.strip()
        start = FILE_START.match(stripped)
        if start:
            if current_raw or current_path:
                mark_broken(current_path, current_raw, "balise <<<FIN_FICHIER>>> manquante : contenu probablement tronqué")
            current_raw = start.group(1) or " "
            current_path = normalize_path(start.group(1))
            body = []
            continue
        if FILE_END.match(stripped):
            if current_path:
                content = "\n".join(_strip_outer_fence(body, _extension(current_path) in PROSE_EXTENSIONS))
                files[current_path] = content + "\n" if content and not content.endswith("\n") else content
                broken.pop(current_path, None)
            elif current_raw:
                mark_broken(None, current_raw, "chemin invalide")
            current_path, current_raw, body = None, "", []
            continue
        if current_raw or current_path:
            body.append(line)
    if current_raw or current_path:
        mark_broken(current_path, current_raw, "balise <<<FIN_FICHIER>>> manquante : contenu probablement tronqué")
    return (
        [{"path": p, "content": c} for p, c in files.items()],
        [f"{p} ({reason})" for p, reason in broken.items()],
    )


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
        # des blocs de code hors balises sont un problème de format, pas un choix assumé.
        has_code = bool(ANY_FENCE.search(text or ""))
        if has_code or not NOT_DELIVERED_MARKER.search(text or ""):
            problems.append(
                "Aucun fichier exploitable trouvé : encadre CHAQUE fichier livré par une ligne "
                "'<<<FICHIER: <chemin relatif>>>>' et une ligne '<<<FIN_FICHIER>>>', avec son "
                "contenu COMPLET entre les deux. Si tu ne peux livrer aucun fichier, dis-le "
                "explicitement en le marquant 'NON réalisé' avec la raison."
            )
    placeholders = find_placeholders(files)
    if placeholders:
        listing = "\n".join(f"- {p} ligne {n} : {line}" for p, n, line in placeholders[:10])
        problems.append(
            "Contenu incomplet détecté (commentaires de remplacement qui seraient committés tels "
            f"quels) :\n{listing}\nRéécris CES fichiers EN ENTIER, sans aucun raccourci, ou "
            "retire leurs balises et liste-les comme 'NON réalisé' dans ton plan."
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
    undelivered: dict[str, str] | None = None,
) -> str:
    """Rapport par fichier : présence réelle, identité avec la version de l'Analyste, syntaxe.

    fetch(path) -> (contenu, erreur) : contenu None en cas d'échec, avec l'erreur préfixée par
    FILE_ABSENT (absence confirmée) ou PRESENT_UNREADABLE (présent mais illisible) ; toute autre
    erreur rend le fichier NON VÉRIFIABLE. undelivered : {chemin: raison} des fichiers dont
    l'écriture a été refusée — ils ne sont pas relus (on lirait l'ancien fichier à leur place). Les résultats sont étiquetés [vérifié outil] : ils proviennent
    d'une comparaison exacte en Python et de check_syntax_content, jamais d'une lecture LLM.
    """
    if not files:
        return (
            "INFO : l'Analyste n'a fourni aucun fichier exploitable pour cette exécution : rien "
            "à comparer. Vérifie le code avec github_read_file/check_syntax si nécessaire."
        )
    # Lectures en parallèle (une par fichier) : sur un gros lot, des allers-retours GitHub
    # séquentiels ajouteraient des dizaines de secondes à un seul appel d'outil de la QA.
    undelivered = undelivered or {}
    to_fetch = [f for f in files if f["path"] not in undelivered]
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_FETCHES) as pool:
        fetched = dict(zip((f["path"] for f in to_fetch), pool.map(lambda f: fetch(f["path"]), to_fetch)))
    rows = []
    for f in files:
        path, expected = f["path"], f["content"]
        if path in undelivered:
            rows.append(f"### {path}\n- Présence : NON LIVRÉ, écriture refusée [vérifié outil] — {undelivered[path]}")
            continue
        actual, error = fetched[path]
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
