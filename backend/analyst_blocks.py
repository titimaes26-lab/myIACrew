"""Balises de fichiers de la sortie de diagnostic_task : <<<FICHIER: …>>> … <<<FIN_FICHIER>>>, découpage en sections."""
import re
import textwrap
from pathlib import PurePosixPath

# Un LLM entoure parfois la balise de mise en forme Markdown ("**<<<FICHIER: x>>>**",
# "`<<<FIN_FICHIER>>>`", ou même de façon ASYMÉTRIQUE — "**<<<FICHIER: x>>>" sans clôture) :
# seule l'OUVERTURE de la décoration est retirée avant de reconnaître la balise (voir
# undecorate) — jamais sa clôture, qu'il n'est pas nécessaire de chercher : FILE_START et
# FILE_END acceptent déjà n'importe quel texte après ">>>" (leur `.*$` final), qui absorbe donc
# de lui-même une clôture "**"/"_"/backtick éventuelle, symétrique ou non, avec ou sans texte
# après elle ("**<<<FICHIER: x>>>** (nouveau)"). Une ligne de contenu ordinaire qui commence par
# hasard par ces caractères (ex: une clôture ``` de bloc de code) n'est jamais affectée : si le
# retrait ne fait pas apparaître "<<<" en tête, undecorate revient au texte d'origine.
_LEADING_DECORATION = re.compile(r"^(\*{1,3}|_{1,3}|`{1,3})")

# Le texte éventuel après ">>>" ("(nouveau)") est ignoré plutôt que de faire rater la balise.
FILE_START = re.compile(r"^<<<\s*FICHIER\s*:\s*(.*?)\s*>{3,}.*$", re.IGNORECASE)

# Toute ligne qui RESSEMBLE à une balise sans être reconnue est signalée, jamais ignorée en silence.
MARKER_LIKE = re.compile(r"^<<<.*FICHIER", re.IGNORECASE)

# FIN_FICHIER avec un "_" : "<FIN FICHIER>" serait lu comme une balise HTML par le rendu Markdown
# de l'interface (et masqué). La variante avec espace reste acceptée si le modèle l'écrit.
# Variante tolérée : "<<<FIN_FICHIER: src/a.ts>>>" (le modèle répète parfois le chemin).
FILE_END = re.compile(r"^<<<\s*FIN[\s_]+FICHIER\s*(?::\s*([^>]*?)\s*)?>{3,}.*$", re.IGNORECASE)

FENCE_LINE = re.compile(r"^(`{3,}|~{3,})")

ANY_FENCE = re.compile(r"^\s*(`{3,}|~{3,})", re.MULTILINE)

# Les corps de fichiers entre balises ne sont jamais des déclarations de l'Analyste.
FILE_BLOCKS = re.compile(
    r"^[ \t]*<<<\s*FICHIER\s*:.*?^[ \t]*<<<\s*FIN[\s_]+FICHIER[^\n]*$",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)

PROSE_EXTENSIONS = {"md", "mdx", "txt", "rst"}

def normalize_path(raw: str) -> str | None:
    """Chemin relatif propre tiré d'une balise, ou None s'il est inutilisable.

    Retire seulement une mise en forme Markdown parasite (``, **) et un préfixe "./" ou "/"
    (refusé par l'API GitHub). Tout le reste — espace, note "(extrait)", ".." — rend le chemin
    invalide : mieux vaut refuser un fichier que l'écrire sous un nom fantaisiste.
    """
    path = raw.strip().strip("*`'\" <>")
    path = path.lstrip("/")
    while path.startswith("./"):
        path = path[2:]
    if not path or any(c.isspace() or c in '<>|"*?:\\' for c in path) or ".." in PurePosixPath(path).parts:
        return None
    return path

def extension(path: str) -> str:
    """Extension du fichier, en minuscules, ou "" s'il n'en a pas. Un nom de fichier qui
    commence lui-même par un point ("`.env`", "`.gitignore`", "`.eslintrc`") n'a PAS
    d'extension pour autant : seul un point situé APRÈS ce préfixe compte ("`.eslintrc.json`"
    a bien l'extension "json")."""
    basename = path.rsplit("/", 1)[-1]
    stem = basename.lstrip(".")
    return stem.rsplit(".", 1)[-1].lower() if "." in stem else ""

def undecorate(stripped: str) -> str:
    """Retire une mise en forme Markdown en tête de ligne SEULEMENT si le résultat révèle une
    balise ("**<<<FICHIER: x>>>**" ou même "**<<<FICHIER: x>>>" sans clôture deviennent
    "<<<FICHIER: x>>>[**]") : une clôture éventuelle, symétrique ou non, n'a jamais besoin d'être
    retirée séparément (voir le commentaire de _LEADING_DECORATION). Une ligne de contenu normale
    (dont le retrait ne fait pas apparaître "<<<" en tête) n'est jamais affectée."""
    s = _LEADING_DECORATION.sub("", stripped, count=1)
    return s if s.startswith("<<<") else stripped

def _strip_outer_fence(body: list[str], prose: bool) -> tuple[list[str], str | None]:
    """(contenu, problème éventuel) : retire le bloc ``` qui encadre le contenu (utile à
    l'affichage Markdown du rapport), s'il l'encadre ENTIÈREMENT : ouverture en première ligne,
    clôture en dernière. Les blocs ``` intérieurs (README, template literal) font partie du
    fichier et restent intacts."""
    first = next((n for n, line in enumerate(body) if line.strip()), None)
    last = next((n for n in range(len(body) - 1, -1, -1) if body[n].strip()), None)
    if first is None or last is None or first == last:
        return body, None
    opening = FENCE_LINE.match(body[first].strip())
    if not opening:
        return body, None

    def is_closing(line: str) -> bool:
        stripped = line.strip()
        return bool(stripped) and set(stripped) == {opening.group(1)[0]} and len(stripped) >= len(opening.group(1))

    if not is_closing(body[last]):
        if any(is_closing(line) for line in body[first + 1:]):
            # Une clôture existe, mais du texte la suit avant <<<FIN_FICHIER>>> (une note de
            # l'Analyste ?) : impossible de savoir s'il fait partie du fichier — on le signale
            # plutôt que de committer une note dans le code (ou dans un fichier texte), ou de
            # couper du vrai contenu. Vrai pour le code ET le texte : la première ligne
            # ressemble déjà à une ouverture de bloc (`opening` a matché ci-dessus), un README
            # authentique commence rarement littéralement par une ligne de clôture ``` seule.
            return body, "texte après la clôture ``` du bloc de code : place la note hors des balises"
        # Ouverture sans clôture : la clôture a été écrite APRÈS la balise <<<FIN_FICHIER>>>.
        # Pour du code, la ligne ```lang n'en fait jamais partie ; pour du texte, seulement si
        # le reste est bien formé sans elle.
        rest = body[first + 1:]
        return (rest if not prose or _fences_are_balanced(rest) else body), None
    inner = body[first + 1:last]
    # Un fichier de code ne commence jamais par une ligne ``` : c'est forcément l'enveloppe, même
    # si le code contient lui-même un ``` isolé (template literal). Un fichier de texte (README),
    # si : l'enveloppe n'est retirée que si l'intérieur reste une suite de blocs bien formée —
    # un README qui commence par ```bash et finit par ``` n'est PAS encadré.
    return (inner if not prose or _fences_are_balanced(inner) else body), None

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

def parse_file_sections(text: str) -> tuple[list[dict], dict[str, str]]:
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
    # Vrai entre une balise mal formée et sa <<<FIN_FICHIER>>> : ce contenu n'appartient à aucun
    # fichier exploitable, et sa balise de fin n'est pas une balise orpheline.
    in_malformed = False
    # Balise d'ouverture indentée (fichiers écrits dans une liste Markdown) : l'indentation
    # COMMUNE à toutes les lignes du contenu est alors retirée (voir textwrap.dedent) — jamais
    # un préfixe fixe retiré ligne par ligne, qui décalerait seulement une partie des lignes.
    marker_indented = False

    def mark_broken(path: str | None, raw: str, reason: str) -> None:
        key = path or raw.strip() or "(chemin vide)"
        files.pop(key, None)
        broken[key] = reason

    for line in (text or "").splitlines():
        stripped = undecorate(line.strip())
        start = FILE_START.match(stripped)
        if start:
            in_malformed = False
            if current_raw or current_path:
                mark_broken(current_path, current_raw, "balise <<<FIN_FICHIER>>> manquante : contenu probablement tronqué")
            current_raw = start.group(1) or " "
            current_path = normalize_path(start.group(1))
            marker_indented = line != line.lstrip()
            body = []
            continue
        end = FILE_END.match(stripped)
        if end:
            closing_path = normalize_path(end.group(1)) if end.group(1) else None
            if in_malformed:
                in_malformed = False
            elif (current_path and closing_path and closing_path != current_path
                  and not current_path.endswith("/" + closing_path)):
                # "<<<FIN_FICHIER: App.tsx>>>" pour "src/components/App.tsx" reste le même fichier.
                mark_broken(current_path, current_raw, f"fermé par la balise de fin d'un autre fichier ({closing_path})")
            elif current_path:
                if marker_indented:
                    body = textwrap.dedent("\n".join(body)).split("\n")
                lines, problem = _strip_outer_fence(body, extension(current_path) in PROSE_EXTENSIONS)
                if problem:
                    mark_broken(current_path, current_raw, problem)
                else:
                    content = "\n".join(lines)
                    files[current_path] = content + "\n" if content and not content.endswith("\n") else content
                    broken.pop(current_path, None)
            elif current_raw:
                mark_broken(None, current_raw, "chemin invalide")
            else:
                mark_broken(None, "(balise orpheline)", "<<<FIN_FICHIER>>> sans balise <<<FICHIER: ...>>> d'ouverture")
            current_path, current_raw, body = None, "", []
            continue
        if MARKER_LIKE.match(stripped):
            mark_broken(None, stripped[:80], "balise mal formée, attendu : <<<FICHIER: chemin/du/fichier>>>")
            if current_raw or current_path:
                # Une balise mal formée en plein fichier annonce presque toujours le fichier
                # SUIVANT : continuer ferait absorber son contenu par le fichier en cours.
                mark_broken(current_path, current_raw, "balise mal formée rencontrée avant <<<FIN_FICHIER>>>")
            current_path, current_raw, body = None, "", []
            in_malformed = True
            continue
        if current_raw or current_path:
            body.append(line)
    if current_raw or current_path:
        mark_broken(current_path, current_raw, "balise <<<FIN_FICHIER>>> manquante : contenu probablement tronqué")
    return [{"path": p, "content": c} for p, c in files.items()], broken

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
