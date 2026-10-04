"""Repérage des raccourcis laissés par le modèle dans un fichier complet (« // … reste du code inchangé »)."""
import re
import analyst_blocks

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
