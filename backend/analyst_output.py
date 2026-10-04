"""Lecture déterministe (en Python, sans LLM) de la sortie de diagnostic_task.

diagnostic_task (voir tasksquestion.yaml) encadre chaque fichier livré par deux balises seules
sur leur ligne : "<<<FICHIER: chemin/du/fichier>>>" puis "<<<FIN_FICHIER>>>", avec le contenu COMPLET
entre les deux. Des balises explicites plutôt que des titres Markdown : un titre "### Fichier :"
se confond avec un commentaire, une citation de code ou un bloc ``` imbriqué, alors que ces
balises n'apparaissent jamais par hasard. Jusqu'ici, developer_agent devait recopier ce contenu
à la main dans ses appels d'outils — la principale source de troncature silencieuse du
pipeline. Ce module extrait ces fichiers une fois pour toutes, pour que :
- le guardrail de diagnostic_task rejette une sortie incomplète AVANT qu'elle n'arrive au
  Développeur (voir review_diagnostic_output) ;
- le Développeur committe le contenu extrait tel quel, sans le recopier (voir
  github_commit_analyst_files, crew_tools.py) ;
- la QA compare le contenu RÉELLEMENT présent sur la branche à celui rédigé par l'Analyste,
  sans devoir le faire "à l'œil" (voir build_delivery_report).
"""
import difflib
import posixpath
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import analyst_blocks
from tools import check_syntax_content


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

# Seules les lignes de COMMENTAIRE sont inspectées : un "..." peut apparaître légitimement
# dans du code (spread JS `...props`, texte d'interface), alors qu'un commentaire
# "// ... reste du code inchangé" ne l'est jamais dans un fichier complet. "#" n'est un
# commentaire que pour certaines extensions (ailleurs, c'est un titre Markdown, un sélecteur
# CSS d'id...) et les fichiers de texte libre ne sont pas inspectés du tout.
COMMENT_MARKER = re.compile(r"^\s*(//|/\*+|\*|\{/\*|<!--|#)\s*")
HASH_COMMENT_EXTENSIONS = {"py", "yaml", "yml", "sh", "toml", "rb"}
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
# Jusqu'à deux articles/déterminants sont tolérés avant le mot : "// ... le reste du fichier",
# "// ... the rest", "# ... les autres fonctions".
_FILLER_WORDS = r"((le|la|les|the|all|tout|toute|toutes|tous|de|du|des)\s+|l['’]\s*){0,2}"
LEADING_ELLIPSIS = re.compile(
    r"^(\.{3}|…)(\s*$|\s*[*/}>-]|\s+" + _FILLER_WORDS + SHORTCUT_WORDS + r")", re.IGNORECASE
)
SHORTCUT_ONLY = re.compile(
    r"^(\.{3}|…)?\s*(le\s+|the\s+)?"
    r"(reste du (code|fichier|composant)|code (existant|inchang[ée])|"
    r"rest of (the )?(code|file|component)|(existing|unchanged) code)"
    # "... est inchangé", "... remains unchanged", "... restent identiques" : un verbe d'état
    # suivi de la formule reste un raccourci, pas une vraie phrase.
    r"(\s+(est|sont|reste|restent|remains?|stays?|is|are))?"
    r"(\s+(inchang[ée]e?s?|existante?s?|identiques?|ici|here|unchanged|the same|as before|comme avant))*"
    r"\s*(\.{3}|…)?\s*[.:;*/}>-]*\s*$",
    re.IGNORECASE,
)

# Marqueur que diagnostic_task utilise pour signaler un fichier volontairement NON fourni (voir
# tasksquestion.yaml) : une sortie sans aucun bloc de fichier mais qui l'emploie est un choix
# assumé et documenté, pas un oubli de format.
# "NON réalisé" suivi d'une raison ("— NON réalisé : trop volumineux") marque un fichier non
# livré ; suivi de ": aucun" ("Fichiers NON réalisé(s) : aucune"), c'est l'inverse. Partagé avec
# le repérage des fichiers retirés (crew_guardrails._withdrawn_paths) pour que les deux concordent.
# "(?!\w)" avant l'exclusion : sans lui, "réalisés : aucun" se rabattrait sur "réalisé" + "s".
NOT_DELIVERED_MARKER = re.compile(
    r"non\s+r[ée]alis[ée]e?s?(?:\(e?s\))?(?!\w)"
    r"(?!\s*(?:\(e?s\))?\s*:\s*(aucune?s?|n[ée]ant|none|rien)\b)",
    re.IGNORECASE,
)

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


# --- Cohérence des imports entre fichiers livrés -------------------------------------------------
CODE_EXTENSIONS = {"ts", "tsx", "js", "jsx"}
_IMPORT_FROM = re.compile(
    r"""import\s+(?:type\s+)?([\w$]+)?\s*,?\s*(?:\{([^}]*)\})?\s*(?:\*\s+as\s+[\w$]+)?\s*from\s*['"](\.{1,2}/[^'"]+)['"]""",
    re.DOTALL,
)
_IMPORT_SIDE_EFFECT = re.compile(r"""^\s*import\s*['"](\.{1,2}/[^'"]+)['"]""", re.MULTILINE)
_EXPORT_DECL = re.compile(
    r"export\s+(?:declare\s+)?(?:async\s+)?(?:const|let|var|function\*?|class|abstract\s+class|type|interface|enum)\s+([\w$]+)"
)
_EXPORT_LIST = re.compile(r"export\s*(?:type\s*)?\{([^}]*)\}")
_EXPORT_DESTRUCTURED = re.compile(r"export\s+(?:const|let|var)\s*[{\[]([^}\]]*)[}\]]")
_RESOLVE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".json", ".css")
_ASSET_EXTENSIONS = {"css", "scss", "json", "svg", "png", "jpg", "jpeg", "gif", "webp", "ico"}


def _exports_of(content: str) -> tuple[set[str], bool, bool]:
    """(noms exportés, export par défaut ?, `export *` présent ?)."""
    names = set(_EXPORT_DECL.findall(content))
    for group in _EXPORT_DESTRUCTURED.findall(content):
        for item in group.split(","):
            # "a", "a: alias", "a = defaut", "...reste" : le nom exporté est celui de la dernière position.
            name = item.split("=")[0].split(":")[-1].strip().lstrip(".").strip()
            if name.isidentifier():
                names.add(name)
    has_default = bool(re.search(r"export\s+default\b", content))
    for group in _EXPORT_LIST.findall(content):
        for item in group.split(","):
            parts = item.replace("type ", "", 1).strip().split(" as ")
            exported = parts[-1].strip()
            if exported == "default":
                has_default = True
            elif exported:
                names.add(exported)
    return names, has_default, bool(re.search(r"export\s*\*", content))


def _import_candidates(importer: str, spec: str) -> list[str]:
    target = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
    if target.startswith(".."):
        return []
    extension = target.rsplit(".", 1)[-1].lower() if "." in posixpath.basename(target) else ""
    if extension in _ASSET_EXTENSIONS:
        return [target]
    if extension in ("js", "jsx"):
        stem = target.rsplit(".", 1)[0]
        return [target, stem + ".ts", stem + ".tsx"]
    return (
        [target + suffix for suffix in _RESOLVE_SUFFIXES]
        + [f"{target}/index{suffix}" for suffix in _RESOLVE_SUFFIXES[:4]]
    )


def find_import_problems(
    files: list[dict],
    list_dir: Callable[[str], set[str] | None] | None = None,
    context_files: list[dict] | None = None,
    import_scope: dict[str, str] | None = None,
) -> list[str]:
    """Incohérences entre fichiers : import relatif qui ne mène ni à un fichier livré ni à un
    fichier existant, et import nommé (ou par défaut) absent des exports d'un fichier LIVRÉ.

    list_dir(dossier) -> noms des entrées, ou None si inconnu (erreur réseau, dossier absent) :
    dans le doute, un import n'est jamais signalé (pas de faux positif sur une panne GitHub).
    context_files : fichiers livrés à une tentative PRÉCÉDENTE : ils servent à résoudre les imports
    de `files` mais ne sont pas contrôlés eux-mêmes.
    import_scope : {chemin: texte} — pour ces fichiers, seuls les imports de CE texte sont contrôlés
    (le texte ajouté par une modification ciblée), pas ceux déjà présents dans le fichier d'origine."""
    delivered = {f["path"]: f["content"] for f in (context_files or [])}
    delivered.update({f["path"]: f["content"] for f in files})
    dir_cache: dict[str, set[str] | None] = {}

    def entries(directory: str) -> set[str] | None:
        if list_dir is None:
            return None
        if directory not in dir_cache:
            dir_cache[directory] = list_dir(directory)
        return dir_cache[directory]

    problems: list[str] = []
    scanned = {f["path"]: f["content"] for f in files}
    scanned.update({p: t for p, t in (import_scope or {}).items() if p in scanned})
    for path, content in scanned.items():
        if analyst_blocks._extension(path) not in CODE_EXTENSIONS:
            continue
        imports = [(m.group(1), m.group(2), m.group(3)) for m in _IMPORT_FROM.finditer(content)]
        imports += [(None, None, m.group(1)) for m in _IMPORT_SIDE_EFFECT.finditer(content)]
        for default_name, named, spec in imports:
            candidates = _import_candidates(path, spec)
            resolved = next((c for c in candidates if c in delivered), None)
            if resolved is None:
                known_absent = bool(candidates)
                for candidate in candidates:
                    names = entries(posixpath.dirname(candidate))
                    if names is None:
                        known_absent = False
                        break
                    if posixpath.basename(candidate) in names:
                        known_absent = False
                        break
                if known_absent and list_dir is not None:
                    problems.append(
                        f"{path} : l'import '{spec}' ne correspond à aucun fichier livré ni existant "
                        "(livre ce fichier, ou corrige le chemin)."
                    )
                continue
            if analyst_blocks._extension(resolved) not in CODE_EXTENSIONS:
                continue
            exported, has_default, star = _exports_of(delivered[resolved])
            if star:
                continue
            if default_name and not has_default:
                problems.append(f"{path} : import par défaut depuis '{spec}', mais {resolved} n'a pas d'export par défaut.")
            for item in (named or "").split(","):
                name = item.replace("type ", "", 1).strip().split(" as ")[0].strip()
                if name and name not in exported:
                    problems.append(f"{path} : '{name}' est importé depuis '{spec}', mais {resolved} ne l'exporte pas.")
    return list(dict.fromkeys(problems))


def find_placeholders(files: list[dict]) -> list[tuple[str, int, str]]:
    """(chemin, numéro de ligne, ligne) pour chaque commentaire trahissant un fichier incomplet."""
    issues = []
    for f in files:
        ext = analyst_blocks._extension(f["path"])
        if ext in analyst_blocks.PROSE_EXTENSIONS:
            continue
        # Sans extension du tout (".env", ".gitignore", "Dockerfile", "Makefile"...) : ces
        # fichiers utilisent quasi toujours "#" comme commentaire, et aucun n'est dans
        # analyst_blocks.PROSE_EXTENSIONS (déjà exclu ci-dessus) — les en priver de ce contrôle les laisserait
        # committer tronqués sans jamais être détectés.
        hash_is_comment = ext in HASH_COMMENT_EXTENSIONS or not ext
        for n, line in enumerate(f["content"].splitlines(), start=1):
            marker = COMMENT_MARKER.match(line)
            if not marker or (marker.group(1) == "#" and not hash_is_comment):
                continue
            # "(reste du code inchangé)", "[...]" : les parenthèses et crochets autour de la
            # formule ne changent rien à sa nature.
            body = re.sub(r"\s+", " ", re.sub(r"[()\[\]]", " ", line[marker.end():])).strip()
            if LEADING_ELLIPSIS.match(body) or SHORTCUT_ONLY.match(body):
                issues.append((f["path"], n, line.strip()[:120]))
    return issues


def review_diagnostic_output(
    text: str,
    read_base: Callable[[str], tuple[str | None, str | None]] | None = None,
    list_dir: Callable[[str], set[str] | None] | None = None,
    context_files: list[dict] | None = None,
) -> tuple[list[dict], str | None, set[str], dict[str, str]]:
    """(fichiers extraits, problème à renvoyer à l'Analyste, chemins des fichiers à raccourci,
    {chemin: raison} des fichiers annoncés mais inexploitables).

    Les fichiers à raccourci sont extraits mais ne doivent JAMAIS être committés tels quels,
    même si l'Analyste ne corrige pas sa réponse (voir _diagnostic_guardrail, crew_checks.py).

    read_base(chemin) -> (contenu, erreur) lit le fichier d'ORIGINE : il sert à résoudre les blocs
    <<<MODIFICATION: ...>>> en fichiers complets (sans lui, une modification est inexploitable).
    list_dir sert à vérifier les imports relatifs (voir find_import_problems).
    """
    files, broken = analyst_blocks.parse_file_sections(text)
    edits, edit_broken = parse_edit_sections(text)
    broken.update(edit_broken)
    edited_paths: set[str] = set()
    replaced_text: list[dict] = []
    for path, blocks in edits.items():
        if any(f["path"] == path for f in files):
            files = [f for f in files if f["path"] != path]
            broken[path] = "livré à la fois en <<<FICHIER>>> et en <<<MODIFICATION>>> : choisis un seul des deux"
            continue
        if read_base is None:
            broken[path] = "modification impossible : lecture du fichier d'origine indisponible"
            continue
        base, error = read_base(path)
        if base is None:
            broken[path] = (
                f"modification impossible : fichier d'origine illisible ({(error or 'erreur inconnue')[:120]}) "
                "— pour créer un nouveau fichier, utilise <<<FICHIER>>>"
            )
            continue
        content, error = apply_edits(base, blocks)
        if content is None:
            broken[path] = error or "modification inapplicable"
            continue
        files.append({"path": path, "content": content})
        edited_paths.add(path)
        # Seul le texte AJOUTÉ est inspecté : un commentaire déjà présent dans le fichier d'origine
        # n'est pas un raccourci de l'Analyste.
        replaced_text.append({"path": path, "content": "\n".join(replace for _, replace in blocks)})
    problems: list[str] = []
    if broken:
        problems.append(
            "Fichiers annoncés mais inexploitables (ils ne seront PAS committés) :\n"
            + "\n".join(f"- {p} ({reason})" for p, reason in broken.items())
        )
    if not files and not broken:
        # "non réalisé" ne dispense du signalement que si la réponse ne contient AUCUN code :
        # des blocs de code hors balises sont un problème de format, pas un choix assumé.
        has_code = bool(analyst_blocks.ANY_FENCE.search(text or ""))
        if has_code or not NOT_DELIVERED_MARKER.search(text or ""):
            problems.append(
                "Aucun fichier exploitable trouvé : encadre CHAQUE fichier livré par une ligne "
                "'<<<FICHIER: chemin/relatif/du/fichier>>>' et une ligne '<<<FIN_FICHIER>>>', avec son "
                "contenu COMPLET entre les deux (ou, pour modifier un fichier existant, un bloc "
                "'<<<MODIFICATION: chemin>>>'). Si tu ne peux livrer aucun fichier, dis-le "
                "explicitement en le marquant 'NON réalisé' avec la raison."
            )
    placeholders = find_placeholders([f for f in files if f["path"] not in edited_paths])
    placeholders += find_placeholders(replaced_text)
    if placeholders:
        listing = "\n".join(f"- {p} ligne {n} : {line}" for p, n, line in placeholders[:10])
        problems.append(
            "Contenu incomplet détecté (commentaires de remplacement qui seraient committés tels "
            f"quels) :\n{listing}\nRéécris CES fichiers EN ENTIER, sans aucun raccourci, ou "
            "retire leurs balises et liste-les comme 'NON réalisé' dans ton plan."
        )
    import_problems = find_import_problems(
        files, list_dir, context_files,
        import_scope={path: text["content"] for path, text in ((r["path"], r) for r in replaced_text)},
    )
    if import_problems:
        problems.append(
            "Incohérences entre fichiers (imports) :\n"
            + "\n".join(f"- {issue}" for issue in import_problems[:10])
            + "\nCorrige-les (les fichiers concernés restent à fournir EN ENTIER ou par modification)."
        )
    faulty = {p for p, _, _ in placeholders}
    return files, ("\n\n".join(problems) if problems else None), faulty, broken


def format_manifest(files: list[dict]) -> str:
    return "\n".join(
        f"- {f['path']} ({len(f['content'].splitlines())} lignes)" for f in files
    ) or "- (aucun fichier)"


def build_delivery_report(
    files: list[dict],
    fetch: Callable[[str], tuple[str | None, str | None]],
    write_rejections: dict[str, str] | None = None,
    not_extracted: dict[str, str] | None = None,
    scope_notes: list[str] | None = None,
    import_notes: list[str] | None = None,
) -> str:
    """Rapport par fichier : présence réelle, identité avec la version de l'Analyste, syntaxe.

    fetch(path) -> (contenu, erreur) : contenu None en cas d'échec, avec l'erreur préfixée par
    FILE_ABSENT (absence confirmée) ou PRESENT_UNREADABLE (présent mais illisible) ; toute autre
    erreur rend le fichier NON VÉRIFIABLE. write_rejections : {chemin: raison} des fichiers
    refusés au dernier commit — relus quand même (un autre outil a pu les écrire depuis), mais
    signalés NON LIVRÉS s'ils ne sont pas identiques à la version de l'Analyste. not_extracted :
    {chemin: raison} des fichiers annoncés par l'Analyste mais jamais committables.
    scope_notes : écarts entre les fichiers livrés et le plan de l'Architecte ; import_notes :
    incohérences d'imports entre fichiers livrés (voir find_import_problems) — deux sections ajoutées
    à la fin, sans quoi ces contrôles n'apparaîtraient pas dans le rapport de la QA.
    Les résultats sont étiquetés [vérifié outil] : ils proviennent d'une comparaison exacte en
    Python et de check_syntax_content, jamais d'une lecture LLM.
    """
    write_rejections = write_rejections or {}
    rows = [
        f"### {path}\n- Présence : NON LIVRÉ, jamais committable [vérifié outil] — {reason}"
        for path, reason in sorted((not_extracted or {}).items())
    ]
    if not files and not rows:
        return (
            "INFO : l'Analyste n'a fourni aucun fichier exploitable pour cette exécution : rien "
            "à comparer. Vérifie le code avec github_read_file/check_syntax si nécessaire."
        )
    # Lectures en parallèle (une par fichier) : sur un gros lot, des allers-retours GitHub
    # séquentiels ajouteraient des dizaines de secondes à un seul appel d'outil de la QA.
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_FETCHES) as pool:
        fetched = list(pool.map(lambda f: fetch(f["path"]), files))
    for f, (actual, error) in zip(files, fetched):
        path, expected = f["path"], f["content"]
        identical = actual is not None and actual.rstrip("\n") == expected.rstrip("\n")
        if path in write_rejections and not identical:
            rows.append(
                f"### {path}\n- Présence : NON LIVRÉ, écriture refusée au commit [vérifié outil] — "
                f"{write_rejections[path]}"
            )
            continue
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
        if identical:
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
    if scope_notes is not None:
        rows.append("### Périmètre (plan de l'Architecte) [vérifié outil]\n" + (
            "\n".join(f"- {note}" for note in scope_notes) or "- Conforme : aucun fichier hors plan, aucun fichier prévu manquant."
        ))
    if import_notes is not None:
        rows.append("### Cohérence des imports entre fichiers livrés [vérifié outil]\n" + (
            "\n".join(f"- {note}" for note in import_notes) or "- Aucune incohérence détectée (imports relatifs et exports nommés)."
        ))
    return "\n\n".join(rows)
