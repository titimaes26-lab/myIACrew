"""Faits de livraison constatés PAR LES OUTILS (jamais par le texte d'un LLM) : message de commit
conventionnel, description de Pull Request générée à partir des faits, et bloc de livraison
ajouté au rapport du Développeur. Fonctions pures, testables sans réseau ni LLM."""
import re

# « fix : x » (espace avant les deux-points, typographie française) est reconnu et normalisé en « fix: x ».
CONVENTIONAL_COMMIT = re.compile(
    r"^(feat|fix|refactor|chore|docs|test|style|perf|build|ci)(\([^)\n]+\))?(!?)\s*:\s*(\S.*)$", re.IGNORECASE
)
_BARE_COMMIT_TYPE = re.compile(
    r"^(feat|fix|refactor|chore|docs|test|style|perf|build|ci)(\([^)\n]+\))?(!?)\s*:?$", re.IGNORECASE
)
_COMMIT_TYPE_BY_WORKFLOW = {"BUGFIX": "fix", "FEATURE": "feat", "DESIGN_AND_DEV": "feat", "ANALYSE_ONLY": "docs"}
MAX_COMMIT_SUBJECT = 72
MAX_SECTION_CHARS = 1500
MAX_REQUEST_CHARS = 400
PULL_REQUEST_URL = re.compile(r"https://github\.com/[^\s)>\]]+/pull/\d+")
_HEADING = re.compile(r"^\s*(#{1,6}\s|\*\*[^*\n]+\*\*\s*:?\s*$)")
_OTHER_PLAIN_SECTION = re.compile(
    r"^\s*[-*>]*\s*(hypoth[èe]ses?|plan|auto-?revue|fichiers?|conclusion|limites?|r[ée]sum[ée])\b[^:\n]{0,40}:",
    re.IGNORECASE,
)
_PLAIN_HEADING = re.compile(r"^\s*[A-Za-zÀ-ÿ][^:\n]{0,60}:")


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
    elif bare := _BARE_COMMIT_TYPE.fullmatch(first):
        # « fix: » sans résumé : le type de l'auteur est gardé, le résumé est générique.
        first = f"{bare.group(1).lower()}{bare.group(2) or ''}{bare.group(3)}: mise à jour"
    else:
        if len(first) > 1 and first[0].isupper() and first[1].islower():
            first = first[0].lower() + first[1:]
        first = f"{_COMMIT_TYPE_BY_WORKFLOW.get(request_type or '', 'chore')}: {first}"
    if len(first) > MAX_COMMIT_SUBJECT:
        first = first[: MAX_COMMIT_SUBJECT - 1].rstrip() + "…"
    return first + (newline + rest if rest.strip() else "")


def extract_section(raw: str, keyword: str, limit: int = MAX_SECTION_CHARS) -> str | None:
    """Corps de la première section dont le titre (« ## … », « **…** » ou une ligne « Mot-clé : … »
    en tête de ligne) contient `keyword`, jusqu'au titre ou à la balise de fichier suivant ; None
    s'il est absent ou vide. Un titre en texte brut peut porter le début du contenu après « : »."""
    lines = (raw or "").splitlines()
    wanted = keyword.lower()
    for index, line in enumerate(lines):
        lowered = line.lower()
        if wanted not in lowered:
            continue
        inline, inline_mode = "", False
        if _HEADING.match(line):
            pass
        elif _PLAIN_HEADING.match(line) and lowered.lstrip(" -*>").startswith(wanted):
            # Titre en texte brut : le mot-clé OUVRE la ligne (« Voir la Traçabilité : … » n'en est pas un).
            inline, inline_mode = line.split(":", 1)[1].strip(), True
        else:
            continue
        body = [inline] if inline else []
        for following in lines[index + 1:]:
            if _HEADING.match(following) or following.strip().startswith("<<<"):
                break
            # Après un titre en texte brut, les titres bruts des autres sections de l'Analyste
            # (« Plan : … », « Auto-revue : … ») terminent aussi la section.
            if inline_mode and _OTHER_PLAIN_SECTION.match(following):
                break
            body.append(following)
        text = "\n".join(body).strip()
        if not text:
            return None
        return text[:limit] + ("…" if len(text) > limit else "")
    return None


_ZERO_WIDTH = "\u200b"
_MENTION = re.compile(r"(?<![\w`])@(?=[A-Za-z0-9])")
_CLOSING_KEYWORD = re.compile(
    r"\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)(\s*:?\s+)((?:[\w.-]+/[\w.-]+)?#\d+)", re.IGNORECASE
)


def sanitize_for_github(text: str) -> str:
    """Neutralise ce qui aurait un effet de BORD sur GitHub : @mention (notification d'un
    utilisateur réel), mot-clé de fermeture suivi d'un n° d'issue (« fixes #12 » la fermerait à la
    fusion) et marqueurs de la zone générée (qui fausseraient sa mise à jour). Le texte reste lisible."""
    text = text.replace(GENERATED_START, "&lt;!-- myiacrew:start --&gt;").replace(GENERATED_END, "&lt;!-- myiacrew:end --&gt;")
    text = _MENTION.sub("@" + _ZERO_WIDTH, text)
    return _CLOSING_KEYWORD.sub(lambda m: f"{m.group(1)}{m.group(2)}{_ZERO_WIDTH}{m.group(3)}", text)


def build_pull_request_body(
    summary: str,
    request: str,
    request_type: str | None,
    committed: list[str],
    not_delivered: dict[str, str],
    traceability: str | None,
    commit_tool_used: bool = True,
) -> str:
    request_line = " ".join(sanitize_for_github(request or "").split())[:MAX_REQUEST_CHARS]
    summary = sanitize_for_github(summary or "")
    traceability = sanitize_for_github(traceability) if traceability else traceability
    parts = []
    if not_delivered:
        parts.append(
            "> ⚠️ **Livraison partielle** : des fichiers n'ont pas pu être livrés (voir plus bas). "
            "À ne pas fusionner avant d'avoir traité les fichiers non livrés."
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
    if request is None:
        request = re.match(r"\s*Demande initiale\s*:\s*(.*?)\s*(?=Précisions apportées\s*:|$)", text, re.DOTALL)
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
    generated = generated.replace(GENERATED_START, "").replace(GENERATED_END, "")
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


def render_partial_delivery_block(
    owner: str, repo: str, branch: str, base_branch: str, partial=None, unavailable_reason: str | None = None
) -> str:
    """Bloc ajouté à un échec d'exécution : ce qui est DÉJÀ sur GitHub (constaté par l'API, jamais
    déduit du texte d'un agent) et comment reprendre ou nettoyer. `partial` = PartialDelivery ;
    None + `unavailable_reason` quand GitHub n'a pas pu être interrogé."""
    lines = ["--- Travail déjà présent sur GitHub ---"]
    repo_url = f"https://github.com/{owner}/{repo}"
    if partial is None:
        lines.append(
            f"- Impossible de vérifier GitHub ({unavailable_reason or 'erreur inconnue'}) : la branche "
            f"`{branch}` de {owner}/{repo} peut contenir des modifications déjà poussées."
        )
        return "\n".join(lines)
    if not partial.branch_exists:
        lines.append(f"- Rien n'a été poussé : la branche `{branch}` n'existe pas sur {owner}/{repo}.")
        return "\n".join(lines)
    detail = []
    if partial.ahead_by is not None:
        detail.append(f"{partial.ahead_by} commit(s) d'avance sur `{base_branch}`")
    if partial.new_commits is True:
        detail.append("dont de nouveaux commits de cette tentative")
    elif partial.new_commits is False:
        detail.append("aucun nouveau commit de cette tentative")
    lines.append(
        f"- Branche `{branch}` : {', '.join(detail) if detail else 'existe'} — {repo_url}/tree/{branch}"
    )
    if partial.pr_url:
        lines.append(f"- Pull Request {'fusionnée' if partial.pr_state == 'merged' else 'ouverte'} : {partial.pr_url}")
    elif partial.pr_checked:
        lines.append("- Pull Request : aucune ouverte pour cette branche")
    else:
        lines.append("- Pull Request : non vérifiée (GitHub n'a pas répondu) — vérifiez-la avant de réessayer")
    lines.append(
        "- Reprise : « Relancer » continue sur cette même branche. "
        f"Pour abandonner, fermez la PR éventuelle et supprimez la branche `{branch}`."
    )
    return "\n".join(lines)
