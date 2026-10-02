"""Faits de livraison constatés PAR LES OUTILS (jamais par le texte d'un LLM) : message de commit
conventionnel, description de Pull Request générée à partir des faits, et bloc de livraison
ajouté au rapport du Développeur. Fonctions pures, testables sans réseau ni LLM."""
import re

# « fix : x » (espace avant les deux-points, typographie française) est reconnu et normalisé en « fix: x ».
CONVENTIONAL_COMMIT = re.compile(
    r"^(feat|fix|refactor|chore|docs|test|style|perf|build|ci)(\([^)\n]+\))?(!?)\s*:\s*(\S.*)$", re.IGNORECASE
)
_COMMIT_TYPE_BY_WORKFLOW = {"BUGFIX": "fix", "FEATURE": "feat", "DESIGN_AND_DEV": "feat", "ANALYSE_ONLY": "docs"}
MAX_COMMIT_SUBJECT = 72
MAX_SECTION_CHARS = 1500
MAX_REQUEST_CHARS = 400
PULL_REQUEST_URL = re.compile(r"https://github\.com/[^\s)>\]]+/pull/\d+")
_HEADING = re.compile(r"^\s*(#{1,6}\s|\*\*[^*\n]+\*\*\s*:?\s*$)")


def conventional_commit_message(message: str, request_type: str | None = None) -> str:
    """Première ligne au format « type: résumé » (type déduit du workflow si absent) et limitée à
    72 caractères ; le corps éventuel du message est conservé tel quel."""
    text = (message or "").strip() or "mise à jour"
    first, newline, rest = text.partition("\n")
    first = first.strip()
    conventional = CONVENTIONAL_COMMIT.match(first)
    if conventional:
        kind, scope, bang, subject = conventional.groups()
        first = f"{kind.lower()}{scope or ''}{bang}: {subject}"
    else:
        if len(first) > 1 and first[0].isupper() and first[1].islower():
            first = first[0].lower() + first[1:]
        first = f"{_COMMIT_TYPE_BY_WORKFLOW.get(request_type or '', 'chore')}: {first}"
    if len(first) > MAX_COMMIT_SUBJECT:
        first = first[: MAX_COMMIT_SUBJECT - 1].rstrip() + "…"
    return first + (newline + rest if rest.strip() else "")


def extract_section(raw: str, keyword: str, limit: int = MAX_SECTION_CHARS) -> str | None:
    """Corps de la première section dont le titre (« ## … » ou « **…** ») contient `keyword`,
    jusqu'au titre ou à la balise de fichier suivant ; None s'il est absent ou vide."""
    lines = (raw or "").splitlines()
    for index, line in enumerate(lines):
        if keyword.lower() in line.lower() and _HEADING.match(line):
            body = []
            for following in lines[index + 1:]:
                if _HEADING.match(following) or following.strip().startswith("<<<"):
                    break
                body.append(following)
            text = "\n".join(body).strip()
            if not text:
                return None
            return text[:limit] + ("…" if len(text) > limit else "")
    return None


def build_pull_request_body(
    summary: str,
    request: str,
    request_type: str | None,
    committed: list[str],
    not_delivered: dict[str, str],
    traceability: str | None,
    commit_tool_used: bool = True,
) -> str:
    request_line = " ".join((request or "").split())[:MAX_REQUEST_CHARS]
    parts = []
    if not_delivered:
        parts.append(
            "> ⚠️ **Livraison partielle** : des fichiers n'ont pas pu être livrés (voir plus bas), "
            "cette Pull Request est ouverte en brouillon."
        )
    parts.append("## Résumé\n" + ((summary or "").strip() or "_Aucun résumé fourni._"))
    if request_line:
        suffix = f" ({request_type})" if request_type else ""
        parts.append(f"## Demande{suffix}\n> {request_line}")
    if committed:
        delivered = "\n".join(f"- `{path}`" for path in committed)
    elif commit_tool_used:
        delivered = "_Aucun fichier n'a pu être committé._"
    else:
        delivered = "_Non constaté : l'écriture n'est pas passée par l'outil de commit des fichiers de l'Analyste (voir le diff de la PR)._"
    parts.append("## Fichiers livrés\n" + delivered)
    if not_delivered:
        parts.append("## Non livrés\n" + "\n".join(f"- `{path}` : {reason}" for path, reason in sorted(not_delivered.items())))
    if traceability:
        parts.append("## Traçabilité et vérification\n" + traceability)
    parts.append("---\n_Description générée par myIACrew à partir des faits constatés par les outils._")
    return "\n\n".join(parts)


def extract_user_request(final_prompt: str) -> str:
    """Texte de la demande seul, sans l'enveloppe « Demande initiale / Type d'exécution /
    Précisions apportées » construite par main.py, précisions comprises si elles existent."""
    text = final_prompt or ""
    request = re.search(r"Demande initiale\s*:\s*(.*?)\n\s*Type d'exécution", text, re.DOTALL)
    clarifications = re.search(r"Précisions apportées\s*:\s*(.*)$", text, re.DOTALL)
    result = (request.group(1) if request else text).strip()
    if clarifications and not clarifications.group(1).strip().lower().startswith("aucune"):
        result += f" — Précisions : {clarifications.group(1).strip()}"
    return result


GENERATED_START = "<!-- myiacrew:start -->"
GENERATED_END = "<!-- myiacrew:end -->"
_GENERATED_BLOCK = re.compile(re.escape(GENERATED_START) + r".*?" + re.escape(GENERATED_END), re.DOTALL)


def merge_pull_request_body(existing: str | None, generated: str) -> str:
    """Description finale : le contenu généré est encadré par des marqueurs ; sur une PR existante,
    seule cette zone est remplacée (un texte ajouté à la main autour est conservé), et une
    description sans marqueurs (PR humaine ou ancienne) reçoit la zone en fin de texte."""
    block = f"{GENERATED_START}\n{generated}\n{GENERATED_END}"
    current = (existing or "").strip()
    if not current:
        return block
    if _GENERATED_BLOCK.search(current):
        return _GENERATED_BLOCK.sub(lambda _: block, current, count=1)
    return f"{current}\n\n{block}"


def unconfirmed_pr_urls(report: str, trusted_urls: list[str]) -> list[str]:
    """URLs de Pull Request citées dans le rapport mais jamais renvoyées par l'outil."""
    trusted = {url.rstrip("/") for url in trusted_urls}
    return sorted({url.rstrip("/") for url in PULL_REQUEST_URL.findall(report or "")} - trusted)


def render_delivery_block(
    committed: list[str],
    not_delivered: dict[str, str],
    pr_urls: list[str],
    has_repo_target: bool,
    unconfirmed: list[str],
    commit_tool_used: bool = True,
) -> str:
    lines = ["## Livraison constatée par les outils"]
    if committed:
        lines.append("- Fichiers committés : " + ", ".join(f"`{p}`" for p in committed))
    elif commit_tool_used:
        lines.append("- Fichiers committés : aucun (github_commit_analyst_files n'a rien pu committer)")
    else:
        lines.append(
            "- Fichiers committés : non constatés (github_commit_analyst_files n'a pas été utilisé : "
            "écriture par un autre outil, ou aucun commit)"
        )
    if not_delivered:
        lines.append("- Non livrés : " + "; ".join(f"`{p}` ({reason})" for p, reason in sorted(not_delivered.items())))
    if not has_repo_target:
        lines.append("- Pull Request : sans objet (travail dans l'espace de travail local)")
    elif pr_urls:
        lines.append("- Pull Request (renvoyée par l'outil) : " + ", ".join(pr_urls))
    else:
        lines.append("- Pull Request : aucune ouverte par github_open_delivery_pull_request")
    if unconfirmed:
        lines.append(
            "- ⚠️ URL citée(s) dans le rapport mais jamais renvoyée(s) par un outil : "
            + ", ".join(unconfirmed) + " (à ne pas considérer comme une PR réelle)"
        )
    return "\n".join(lines)
