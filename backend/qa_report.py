"""Contrôle DÉTERMINISTE du rapport de la QA (sans LLM) : verdict minimal imposé par les faits
constatés par les outils, qualité des preuves du tableau de critères. Fonctions pures, testables."""
import re

from delivery import extract_section

# Tolère "Verdict final (après revue complète) : GO", "**Verdict** : NO GO", "Verdict — GO",
# "Verdict : ✅ GO", "Verdict : GO avec réserves", "Verdict : NON GO", "| Verdict | GO |" (ligne
# de tableau Markdown), ou "## Verdict" en titre suivi de "**GO**" sur une ligne suivante.
# Pour écarter les mentions fortuites ("verdict: No go-live possible", "... :\nGo figure"), une
# valeur qui n'est pas en MAJUSCULES ne doit être suivie ni d'un tiret collé ni d'un mot en
# minuscules ; en MAJUSCULES (la forme demandée à la QA), tout est accepté ("NO_GO car ...").
_VERDICT_VALUES = r"GO[ _]AVEC[ _]R[ÉE]SERVES|NON?[ _-]?GO|GO"
# Deux formes de séparateur, DISTINCTES pour ne pas se gêner l'une l'autre : ":"/tiret dans une
# marge large (60 car., "|" toléré dedans — ex: "Verdict (2 échecs | tolérés) : GO", un "|"
# incident dans le texte ne doit jamais empêcher d'atteindre le ":" voulu plus loin) ; "|" d'un
# tableau Markdown seulement TOUT PRÈS de "verdict" (8 car., sans ":" ni "|" avant lui, la
# cellule d'un tableau n'en contenant normalement aucun) — ex: "| Verdict | GO |".
QA_VERDICT = re.compile(
    r"(?i:verdict)(?:[^:\n—–=-]{0,60}[:—–=]|[^|:\n]{0,8}\||[ \t*]*\r?\n)\s*\W{0,8}"
    r"(?:(?P<eol>(?i:" + _VERDICT_VALUES + r"))(?![\w-])(?![ \t]+[a-zà-ÿ])"
    r"|(?P<upper>" + _VERDICT_VALUES + r")(?![\w-]))",
)


SEVERITY = {"GO": 0, "GO_AVEC_RESERVES": 1, "NO_GO": 2}
_NONE_VALUE = re.compile(r"^[\s*_`>-]*(aucune?s?|n[ée]ant|none|rien|n/?a|-|—)?[\s.*_`]*$", re.IGNORECASE)
_END_OF_BLOCKING = re.compile(
    r"^[\s*_`>#|-]*(verdict|probl[èe]mes?\s+mineurs?|fichiers?\s+non\s+v[ée]rifi[ée]s?|crit[èe]res?\s+d'acceptation)\b",
    re.IGNORECASE,
)
_STATUS_NV = re.compile(r"NON[ _-]?V[ÉE]RIFIABLE", re.IGNORECASE)
_STATUS_KO = re.compile(r"(?<![A-Za-z])KO(?![A-Za-z])")
_STATUS_OK = re.compile(r"(?<![A-Za-z])OK(?![A-Za-z])")
_FILE_LINE_REF = re.compile(r"[\w./\\-]+\.\w{1,5}:\d+|\bligne\s+\d+", re.IGNORECASE)
_MANUAL_TEST_HINT = re.compile(
    r"\b(test|tester|manuel|manuellement|v[ée]rifier|ouvrir|cliquer|lancer|[ée]tapes?|saisir|ex[ée]cuter)\b", re.IGNORECASE
)
MIN_PROOF_CHARS = 15


def normalize_verdict(token: str) -> str:
    compact = re.sub(r"[ _-]+", "_", token.strip().upper()).replace("É", "E")
    if compact.startswith("GO_AVEC"):
        return "GO_AVEC_RESERVES"
    if compact in ("NO_GO", "NON_GO", "NOGO", "NONGO"):
        return "NO_GO"
    return "GO"


def _verdict_spans(raw: str) -> list[tuple[int, int, str]]:
    """(début, fin, verdict normalisé) de CHAQUE mention de verdict, dans l'ordre du texte."""
    spans = []
    for match in QA_VERDICT.finditer(raw):
        group = "eol" if match.group("eol") else "upper"
        spans.append((match.start(group), match.end(group), normalize_verdict(match.group(group))))
    return spans


def final_verdict(raw: str) -> str | None:
    """Dernier verdict QA mentionné dans un résultat (« GO », « GO_AVEC_RESERVES », « NO_GO »), ou None.
    Sert à suivre la qualité dans le temps ; le DERNIER gagne (un rapport peut citer un verdict puis le corriger)."""
    spans = _verdict_spans(raw or "")
    return spans[-1][2] if spans else None


_DECORATION = re.compile(r"[*_`\u2705\u274c\u26a0\ufe0f\u2611\u2714\u2716\U0001F7E2\U0001F534\U0001F7E1]")
_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)\s*$")
_STATUS_ONLY = re.compile(r"(OK|KO|NON[ _-]?V[ÉE]RIFIABLE)", re.IGNORECASE)


def _status_of(cell: str) -> str | None:
    """OK, KO ou NV si la cellule EST un statut (mise en forme, emoji et parenthèse finale tolérés,
    ex: « ✅ **OK** », « OK (heuristique) ») ; None sinon. Un en-tête « Statut OK/KO/NON VÉRIFIABLE »
    n'en est donc pas un."""
    cleaned = _TRAILING_PAREN.sub("", _DECORATION.sub("", cell)).strip()
    if not _STATUS_ONLY.fullmatch(cleaned):
        return None
    if _STATUS_NV.search(cleaned):
        return "NV"
    return "KO" if cleaned.upper() == "KO" else "OK"


def parse_criteria(raw: str) -> list[dict]:
    """Lignes du tableau « Critères d'acceptation » : {criterion, status (OK|KO|NV), proof, fix}.
    La colonne de statut est repérée par la ligne d'en-tête (cellule « Statut »), sinon, par ligne, la
    première cellule d'indice 1 ou plus qui est un statut ; critère, preuve et correctif sont lus par
    rapport à elle (cellule précédente, suivante, d'après). Les en-têtes et séparateurs sont ignorés ;
    sans section dédiée, tout le rapport est parcouru."""
    text = raw.replace("\u2019", "'")
    section = extract_section(text, "Critères d'acceptation", limit=10**6)
    rows = []
    status_column: int | None = None
    for line in (section if section is not None else text).splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 3 or set("".join(cells)) <= set("-: "):
            continue
        header = next((i for i, c in enumerate(cells) if re.match(r"^[*_`\s]*statut\b", c, re.IGNORECASE)), None)
        if header is not None and _status_of(cells[header]) is None:
            status_column = header
            continue
        index = status_column if status_column is not None else next(
            (i for i in range(1, len(cells)) if _status_of(cells[i])), None
        )
        if index is None or index >= len(cells) or index < 1:
            continue
        status = _status_of(cells[index])
        if status is None:
            continue
        rows.append({
            "criterion": cells[index - 1], "status": status,
            "proof": cells[index + 1] if index + 1 < len(cells) else "",
            "fix": cells[index + 2] if index + 2 < len(cells) else "",
        })
    return rows


def has_blocking_problems(raw: str) -> bool:
    section = extract_section(raw.replace("\u2019", "'"), "Problèmes bloquants", limit=10**6)
    if not section:
        return False
    for line in section.splitlines():
        # La section s'arrête aux titres en texte brut qui la suivent : sans cela, « Verdict : GO »
        # (dernière ligne du rapport) serait lu comme un problème bloquant.
        if _END_OF_BLOCKING.match(line):
            break
        if line.strip() and not _NONE_VALUE.match(line.lstrip("-*• \t")):
            return True
    return False


def qa_report_issues(raw: str) -> list[str]:
    """Défauts vérifiables du rapport : tableau absent, KO sans preuve localisée ni correctif,
    NON VÉRIFIABLE sans raison ni test manuel concret."""
    criteria = parse_criteria(raw)
    if not criteria:
        return ["Aucun tableau de critères d'acceptation exploitable (| Critère | Statut | Preuve | Correctif suggéré |)."]
    issues = []
    for row in criteria:
        label = row["criterion"][:60]
        if row["status"] == "KO":
            if not _FILE_LINE_REF.search(row["proof"]):
                issues.append(f"Critère KO sans preuve localisée (fichier:ligne) : « {label} ».")
            if len(row["fix"]) < MIN_PROOF_CHARS or _NONE_VALUE.match(row["fix"]):
                issues.append(f"Critère KO sans correctif suggéré : « {label} ».")
        elif row["status"] == "NV":
            if len(row["proof"]) < MIN_PROOF_CHARS or not _MANUAL_TEST_HINT.search(row["proof"]):
                issues.append(f"Critère NON VÉRIFIABLE sans raison ni test manuel concret : « {label} ».")
    return issues


def required_verdict(
    raw: str,
    *,
    delivery_gaps: dict[str, str],
    analyst_file_count: int,
    committed_count: int,
    commit_tool_used: bool,
) -> tuple[str, list[str]]:
    """(verdict MINIMAL imposé par les faits, raisons). La QA peut être plus sévère, jamais plus
    indulgente : NO_GO si un critère est KO, si un problème bloquant est listé ou si l'outil de commit
    n'a livré aucun fichier de l'Analyste ; GO_AVEC_RESERVES si des fichiers sont non livrés ou si un
    critère reste NON VÉRIFIABLE (ou absent) ; GO sinon."""
    criteria = parse_criteria(raw)
    reasons: list[tuple[str, str]] = []
    knocked_out = [r for r in criteria if r["status"] == "KO"]
    if knocked_out:
        reasons.append(("NO_GO", f"{len(knocked_out)} critère(s) KO"))
    if has_blocking_problems(raw):
        reasons.append(("NO_GO", "des problèmes bloquants sont listés"))
    if commit_tool_used and analyst_file_count > 0 and committed_count == 0:
        reasons.append(("NO_GO", "aucun fichier de l'Analyste n'a été committé"))
    if delivery_gaps:
        reasons.append(("GO_AVEC_RESERVES", f"{len(delivery_gaps)} fichier(s) non livré(s) : " + ", ".join(sorted(delivery_gaps)[:5])))
    unverifiable = [r for r in criteria if r["status"] == "NV"]
    if unverifiable:
        reasons.append(("GO_AVEC_RESERVES", f"{len(unverifiable)} critère(s) NON VÉRIFIABLE(S)"))
    if not criteria:
        reasons.append(("GO_AVEC_RESERVES", "aucun critère d'acceptation évalué"))
    verdict = max((v for v, _ in reasons), key=SEVERITY.__getitem__, default="GO")
    return verdict, [reason for _, reason in reasons]


_OPENS_LINE = re.compile(r"^[\s*_`>#|-]*verdict\b", re.IGNORECASE)


def reconcile_verdict(raw: str, required: str) -> tuple[str, str | None]:
    """(rapport, verdict d'origine ou None). Les mentions de verdict PLUS indulgentes que `required`
    sont réécrites en place (l'interface lit la première mention) : la DERNIÈRE (la conclusion) et
    celles qui ouvrent une ligne ou une cellule de tableau (« Verdict : … », « | Verdict | … | »).
    Une phrase de prose qui cite un verdict n'est pas modifiée ; sans écart, rapport inchangé."""
    spans = _verdict_spans(raw)
    adjusted_from = None
    for position in range(len(spans) - 1, -1, -1):
        start, end, verdict = spans[position]
        line_start = raw.rfind("\n", 0, start) + 1
        opens_line = bool(_OPENS_LINE.match(raw[line_start:end]))
        if SEVERITY[verdict] < SEVERITY[required] and (position == len(spans) - 1 or opens_line):
            raw = raw[:start] + required + raw[end:]
            adjusted_from = adjusted_from or verdict
    return raw, adjusted_from
