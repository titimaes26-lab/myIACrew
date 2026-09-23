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
from typing import Callable

from tools import check_syntax_content

FILE_HEADING = re.compile(r"^#{2,4}\s*Fichier\s*:\s*`?([^`\n]+?)`?\s*$", re.IGNORECASE)
FENCE_OPEN = re.compile(r"^(`{3,}|~{3,})[^`\n]*$")

# Seules les lignes de COMMENTAIRE sont inspectées : un "..." ou le mot "existant" peuvent
# apparaître légitimement dans du code (spread JS `...props`, texte d'interface), alors
# qu'un commentaire "// ... reste du code inchangé" ne l'est jamais dans un fichier complet.
COMMENT_LINE = re.compile(r"^\s*(//|#|/\*|\*|\{/\*|<!--)")
PLACEHOLDER_IN_COMMENT = re.compile(
    r"(^\s*(//|#|/\*|\*|\{/\*|<!--)\s*(\.{3}|…)\s*($|\*/|\*/\}|-->|[a-zà-ÿ(\[]))"
    r"|reste du (code|fichier|composant)"
    r"|code (existant|inchangé|inchange)"
    r"|(inchangé|inchange)s?\s*(\.{3}|…)"
    r"|à compléter|a completer"
    r"|rest of (the )?(code|file|component)"
    r"|existing code|unchanged code",
    re.IGNORECASE,
)

# Marqueur que diagnostic_task utilise pour signaler un fichier volontairement NON fourni (voir
# tasksquestion.yaml) : une sortie sans aucun bloc de fichier mais qui l'emploie est un choix
# assumé et documenté, pas un oubli de format.
NOT_DELIVERED_MARKER = re.compile(r"non\s+r[ée]alis[ée]", re.IGNORECASE)

MAX_DIFF_LINES_PER_FILE = 40


def parse_file_blocks(text: str) -> list[dict]:
    """Liste ordonnée de {"path", "content"} extraite des sections "### Fichier : <chemin>".

    La clôture d'un bloc doit reprendre EXACTEMENT la même clôture (même caractère, au moins
    autant de répétitions) que son ouverture : un fichier Markdown qui contient lui-même des
    blocs ``` peut ainsi être encadré par ```` sans être coupé à son premier bloc interne.
    En cas de chemin dupliqué, la DERNIÈRE version gagne (l'Analyste corrige parfois un
    fichier plus bas dans sa réponse).
    """
    if not text:
        return []
    lines = text.splitlines()
    files: dict[str, str] = {}
    i = 0
    while i < len(lines):
        heading = FILE_HEADING.match(lines[i].strip())
        if not heading:
            i += 1
            continue
        path = heading.group(1).strip().strip("'\"")
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        fence = FENCE_OPEN.match(lines[j].strip()) if j < len(lines) else None
        if not fence:
            i += 1
            continue
        fence_str = fence.group(1)
        body: list[str] = []
        k = j + 1
        closed = False
        while k < len(lines):
            stripped = lines[k].strip()
            if stripped and set(stripped) == {fence_str[0]} and len(stripped) >= len(fence_str):
                closed = True
                break
            body.append(lines[k])
            k += 1
        if closed and path:
            content = "\n".join(body)
            files[path] = content + "\n" if content and not content.endswith("\n") else content
        i = k + 1
    return [{"path": p, "content": c} for p, c in files.items()]


def find_placeholders(files: list[dict]) -> list[tuple[str, int, str]]:
    """(chemin, numéro de ligne, ligne) pour chaque commentaire trahissant un fichier incomplet."""
    issues = []
    for f in files:
        for n, line in enumerate(f["content"].splitlines(), start=1):
            if COMMENT_LINE.match(line) and PLACEHOLDER_IN_COMMENT.search(line):
                issues.append((f["path"], n, line.strip()[:120]))
    return issues


def review_diagnostic_output(text: str) -> tuple[list[dict], str | None]:
    """(fichiers extraits, problème bloquant éventuel à renvoyer à l'Analyste pour correction)."""
    files = parse_file_blocks(text)
    if not files:
        if NOT_DELIVERED_MARKER.search(text or ""):
            return [], None
        return [], (
            "Aucun fichier exploitable trouvé : chaque fichier doit être introduit par une ligne "
            "'### Fichier : <chemin>' suivie IMMÉDIATEMENT d'un bloc de code (```) contenant son "
            "contenu COMPLET. Si tu ne peux livrer aucun fichier, dis-le explicitement en le "
            "marquant 'NON réalisé' avec la raison."
        )
    placeholders = find_placeholders(files)
    if placeholders:
        listing = "\n".join(f"- {p} ligne {n} : {line}" for p, n, line in placeholders[:10])
        return files, (
            "Contenu incomplet détecté (commentaires de remplacement qui seraient committés tels "
            f"quels) :\n{listing}\nRéécris CES fichiers EN ENTIER, sans aucun raccourci, ou "
            "retire leur bloc et liste-les comme 'NON réalisé' dans ton plan."
        )
    return files, None


def format_manifest(files: list[dict]) -> str:
    return "\n".join(
        f"- {f['path']} ({len(f['content'].splitlines())} lignes)" for f in files
    ) or "- (aucun fichier)"


def build_delivery_report(
    files: list[dict],
    fetch: Callable[[str], tuple[str | None, str | None]],
) -> str:
    """Rapport par fichier : présence réelle, identité avec la version de l'Analyste, syntaxe.

    fetch(path) -> (contenu, erreur) : contenu None si le fichier est absent/illisible, avec
    l'erreur correspondante. Les résultats sont étiquetés [vérifié outil] : ils proviennent
    d'une comparaison exacte en Python et de check_syntax_content, jamais d'une lecture LLM.
    """
    if not files:
        return (
            "INFO : l'Analyste n'a fourni aucun fichier exploitable pour cette exécution : rien "
            "à comparer. Vérifie le code avec github_read_file/check_syntax si nécessaire."
        )
    rows = []
    for f in files:
        path, expected = f["path"], f["content"]
        actual, error = fetch(path)
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
