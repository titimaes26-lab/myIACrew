"""Contrôles automatiques de la sortie des agents : specs, architecture, verdict QA, fichiers retirés, périmètre."""
import re
from typing import List, Optional

from analyst_output import NOT_DELIVERED_MARKER
from analyst_blocks import FILE_BLOCKS, normalize_path
from qa_report import QA_VERDICT

_NEGATION_BEFORE = re.compile(r"\b(rien|aucun|pas|nothing|no)\s+(de\s+|d'\s*)?$", re.IGNORECASE)

# Mention POSITIVE ("src/a.ts réalisé, src/b.ts NON réalisé") : ce qui la précède appartient à
# une autre proposition. Plus fiable qu'une ponctuation (",", "|" d'un tableau Markdown...).
_DONE_POSITIVE = re.compile(r"(?<!non\s)(?<!non\s\s)\br[ée]alis[ée]e?s?(?:\(e?s\))?(?!\w)", re.IGNORECASE)

# Séparateur exigé entre un mot positif/négatif et le PREMIER chemin qui le suit ("Réalisé :
# src/a.ts", "NON réalisé — src/b.ts") : au moins un signe de ponctuation (jamais du simple
# espace seul), pour qu'une phrase sans rapport ("src/a.ts réalisé src/b.ts NON réalisé", sans
# aucune ponctuation entre les deux chemins) ne fasse jamais réclamer par erreur le second par
# la mention positive du premier. Entre deux chemins d'une même liste ("src/a.ts, src/c.ts"),
# la virgule suffit (voir _LIST_SEPARATOR, qui ne sert qu'à partir du 2e chemin).
_ADJACENT_PATH_SEPARATOR = re.compile(r"\s*[:—–-]\s*")

# Une VRAIE virgule est exigée pour continuer une liste déjà commencée (jamais du simple
# espace) : sans ça, "Réalisé : src/a.ts src/b.ts NON réalisé" (séparés par un espace, sans
# virgule) ferait réclamer src/b.ts par la mention positive AVANT même d'atteindre "NON
# réalisé", qui devait pourtant le retirer.
_LIST_SEPARATOR = re.compile(r"\s*,\s*")

# Écart toléré entre un chemin qu'on s'apprête à réclamer POUR UNE LISTE et une mention NON
# réalisé qui le suivrait de près : un simple espace suffit ici (contrairement à
# _ADJACENT_PATH_SEPARATOR, qui sert à RÉCLAMER un chemin, pas seulement à regarder devant soi).
_LOOKAHEAD_GAP = re.compile(r"\s{0,3}")

def _consume_adjacent_paths(text: str, start: int, patterns: dict) -> tuple[int, set[str]]:
    """Consomme, à partir de `start`, une liste de chemins séparés par des virgules
    ("src/a.ts, src/c.ts") tant qu'un chemin candidat suit immédiatement (au plus un séparateur
    court entre deux). Renvoie (position juste après le dernier chemin consommé, chemins
    trouvés) : ces chemins sont "réclamés" par le mot qui précède `start`, jamais disponibles
    pour un retrait/une conservation décidé par une AUTRE mention plus loin sur la ligne.

    `text` doit être la ligne COMPLÈTE (pas tronquée à la mention en cours) : un chemin de la
    liste (à partir du 2e, jamais le 1er) directement suivi d'une mention "NON réalisé" lui
    appartient probablement plutôt qu'à la liste positive qui précède ("Réalisé : src/a.ts,
    src/b.ts NON réalisé" — la virgule ne fait pas de src/b.ts un 2e fichier réalisé) : la
    consommation s'arrête AVANT de le réclamer, il sera jugé par SA propre mention.
    """
    found: set[str] = set()
    pos = start
    separator = _ADJACENT_PATH_SEPARATOR
    while True:
        sep = separator.match(text, pos)
        if sep is None:
            # _ADJACENT_PATH_SEPARATOR (1er chemin) exige une vraie ponctuation, contrairement à
            # _LIST_SEPARATOR (chemins suivants) : aucune ici, la liste s'arrête.
            break
        matched = next(
            ((path, m.end()) for path, pattern in patterns.items() if (m := pattern.match(text, sep.end()))),
            None,
        )
        if not matched:
            break
        path, end = matched
        # Vérifié pour CHAQUE chemin réclamé, y compris le tout premier (pas seulement à partir
        # du 2e via _LIST_SEPARATOR) : "Réalisé : src/a.ts NON réalisé" (un seul chemin, suivi
        # SANS virgule d'une mention négative) ne doit pas plus réclamer src/a.ts que ne le
        # ferait une liste à plusieurs éléments.
        gap = _LOOKAHEAD_GAP.match(text, end)
        # `\s{0,3}` correspond toujours (éventuellement à vide) : le repli sur `end` n'est là que pour le typage.
        if NOT_DELIVERED_MARKER.match(text, gap.end() if gap else end):
            break
        pos = end
        found.add(path)
        separator = _LIST_SEPARATOR  # une virgule n'introduit un chemin SUIVANT qu'après le 1er.
    return pos, found

def _withdrawn_paths(text: str, candidates) -> set[str]:
    """Chemins de `candidates` que `text` déclare "NON réalisé" (retrait explicite), en une
    seule passe. Un chemin est retiré s'il figure, délimité exactement (pas "src/App.tsx.bak"
    pour "src/App.tsx", "./" ou "/" initial toléré), dans la même proposition que la mention,
    AVANT elle ("src/a.ts, src/b.ts — NON réalisés", une ligne de tableau "| src/a.ts | NON
    réalisé |") OU APRÈS elle ("NON réalisé : src/b.ts, src/d.ts (raison)"). Une mention positive
    ("réalisé") immédiatement suivie d'UNE OU PLUSIEURS de ses chemins ("Réalisé : src/a.ts,
    src/c.ts. NON réalisé : src/b.ts") les réclame tous, jamais retirables par une mention plus
    loin sur la ligne — ni "src/a.ts" dans "src/a.ts réalisé, src/b.ts NON réalisé". Une mention
    niée ("rien de NON réalisé") et le contenu des fichiers livrés (entre balises) sont ignorés."""
    patterns = {
        path: re.compile(r"(?<![\w./-])(?:\./|/)?" + re.escape(path) + r"(?![\w/-]|\.\w)")
        for path in candidates
    }
    withdrawn: set[str] = set()
    for line in FILE_BLOCKS.sub("", text).splitlines():
        for match in NOT_DELIVERED_MARKER.finditer(line):
            before = line[:match.start()]
            if _NEGATION_BEFORE.search(before):
                continue
            positives = list(_DONE_POSITIVE.finditer(before))
            cut = positives[-1].end() if positives else 0
            if positives:
                # Le mot positif est-il immédiatement suivi d'un ou plusieurs chemins ? Si oui,
                # ils sont à lui — on étend la coupure pour les exclure de `clause` (docstring).
                # `line` COMPLÈTE (pas `before`, tronquée pile avant la mention en cours) : sans
                # ça, _consume_adjacent_paths ne pourrait jamais voir la mention NON réalisé qui
                # suit pour interrompre à temps une liste ambiguë (voir sa docstring). `cut` ne
                # peut de toute façon jamais dépasser `match.start()` : la liste s'arrête net dès
                # que le séparateur ou le chemin suivant se heurte à la mention elle-même.
                cut, _claimed = _consume_adjacent_paths(line, cut, patterns)
            clause = before[cut:]
            withdrawn.update(path for path, pattern in patterns.items() if pattern.search(clause))
            # Côté "après" : seulement les chemins IMMÉDIATEMENT adjacents à la mention (une
            # liste à la virgule y compris) — jamais un chemin cité plus loin dans une raison
            # entre parenthèses ("NON réalisé (remplacé par src/new.ts)"), qui décrit autre
            # chose que ce qui est retiré.
            _, after_claimed = _consume_adjacent_paths(line, match.end(), patterns)
            withdrawn.update(after_claimed)
    return withdrawn

def _qa_verdict_guardrail(task_output):
    """Garantit un verdict QA lisible sans relancer l'agent (une relance QA coûterait jusqu'à
    10 appels d'outils) : sans verdict explicite, on l'ajoute comme NON FOURNI, à traiter en
    NO_GO, plutôt que de laisser l'utilisateur deviner la conclusion."""
    raw = getattr(task_output, "raw", "") or ""
    if QA_VERDICT.search(raw):
        return True, task_output
    return True, (
        f"{raw}\n\n**Verdict : NON FOURNI** — la QA n'a pas conclu explicitement : à considérer "
        "comme NO_GO tant qu'une vérification humaine n'a pas eu lieu."
    )

def _missing_heading_issues(raw: str, headings) -> List[str]:
    return [
        f"Section « {heading} » absente."
        for heading in headings
        if not re.search(rf"^#+\s*.*{re.escape(heading)}", raw, re.IGNORECASE | re.MULTILINE)
    ]

def _make_issue_note_guardrail(issues_fn, title: str):
    """Guardrail qui signale les anomalies SANS relancer l'agent (une relance coûterait un appel
    LLM de plus sous un quota serré) : la note ajoutée est lue par les tâches suivantes."""
    def guardrail(task_output):
        raw = getattr(task_output, "raw", "") or ""
        issues = issues_fn(raw)
        if not issues:
            return True, task_output
        return True, f"{raw}\n\n## {title}\n" + "\n".join(f"- {issue}" for issue in issues)
    return guardrail

DESIGN_REQUIRED_HEADINGS = (
    "Besoin", "Utilisateurs", "Fonctionnalités", "Règles et cas limites",
    "Critères d'acceptation", "Hypothèses retenues",
)

_DESIGN_VAGUE_TERMS = re.compile(
    r"\b(rapide(?:ment)?|fluide|intuitif|intuitive|simple|convivial|ergonomique|joli|moderne)\b", re.IGNORECASE
)

_DESIGN_FEATURE_ID = r"(?<![A-Za-z-])F(\d+)\b"

_DESIGN_MUST_BEFORE = re.compile(_DESIGN_FEATURE_ID + r"[^\n]*\[\s*Must\s*\]", re.IGNORECASE)

_DESIGN_MUST_AFTER = re.compile(r"\[\s*Must\s*\][^\n]*?" + _DESIGN_FEATURE_ID, re.IGNORECASE)

_DESIGN_CRITERION_ID = re.compile(r"\bAC-F(\d+)[-.](\d+)")

def _design_spec_issues(raw: str) -> List[str]:
    """Anomalies vérifiables d'un document de specs du designer (titres, identifiants, termes vagues)."""
    # Apostrophes typographiques courantes dans une sortie LLM : sans normalisation, un titre
    # « Critères d’acceptation » serait signalé absent à tort.
    raw = raw.replace("\u2019", "'").replace("\u2018", "'")
    issues = _missing_heading_issues(raw, DESIGN_REQUIRED_HEADINGS)
    must_numbers = {m.group(1) for m in _DESIGN_MUST_BEFORE.finditer(raw)}
    must_numbers |= {m.group(1) for m in _DESIGN_MUST_AFTER.finditer(raw)}
    covered = {m.group(1) for m in _DESIGN_CRITERION_ID.finditer(raw)}
    for number in sorted(must_numbers - covered, key=int):
        issues.append(f"F{number} [Must] n'a aucun critère d'acceptation AC-F{number}-n.")
    for line in raw.splitlines():
        criterion = _DESIGN_CRITERION_ID.search(line)
        if criterion:
            body = line[criterion.end():]
            # Seuil attendu dans le résultat observable ("Alors ..."), pas dans le contexte
            # ("Étant donné 3 tâches") : un chiffre de contexte ne rend pas mesurable un « fluide ».
            outcome = re.search(r"\bAlors\b", body, re.IGNORECASE)
            outcome_text = body[outcome.end():] if outcome else body
            if _DESIGN_VAGUE_TERMS.search(outcome_text) and not re.search(r"\d", outcome_text):
                issues.append(f"Critère vague sans seuil mesurable : « {line.strip()[:90]} ».")
    return issues

_design_spec_guardrail = _make_issue_note_guardrail(_design_spec_issues, "Contrôle automatique des specs")

ARCHITECTURE_REQUIRED_HEADINGS = (
    "Existant", "Cible", "Décisions", "Contrat", "Fichiers à créer ou modifier", "Couverture", "Risques",
)

_ARCH_FILE_LINE = re.compile(r"^\s*[-*]\s*\**\s*(?:CRÉER|CREER|MODIFIER)\b\**\s*(.*)$", re.MULTILINE)

_ARCH_FILE_ENTRY = re.compile(r"^`?([^\s`:]+)`?\s*:\s*\S")

_ARCH_CODE_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")

def _markdown_section(raw: str, keyword: str) -> Optional[str]:
    """Corps de la première section dont le titre contient `keyword`, jusqu'au titre suivant."""
    lines = raw.splitlines()
    for index, line in enumerate(lines):
        if re.match(r"^#+\s", line) and keyword.lower() in line.lower():
            body = []
            for following in lines[index + 1:]:
                if re.match(r"^#+\s", following):
                    break
                body.append(following)
            return "\n".join(body)
    return None

def _truncation_issues(raw: str) -> List[str]:
    """Signes qu'une sortie a été coupée par la limite de tokens : dernière section vide, dernière ligne qui
    s'arrête sur une ponctuation ouvrante, ou bloc de code jamais refermé."""
    lines = [line for line in raw.rstrip().splitlines()]
    if not lines:
        return []
    last_heading = max((i for i, line in enumerate(lines) if re.match(r"^#+\s", line)), default=None)
    if last_heading is not None and not any(line.strip() for line in lines[last_heading + 1:]):
        return ["La dernière section est vide : la sortie a probablement été tronquée (limite de tokens)."]
    if raw.count("```") % 2 == 1 or lines[-1].rstrip().endswith((",", ";", ":", "(", "[", "{", "—", "-", "/")):
        return ["La sortie s'arrête en plein milieu : elle a probablement été tronquée (limite de tokens)."]
    return []

def _architecture_issues(raw: str) -> List[str]:
    """Anomalies vérifiables du plan de l'architecte : sections, format de la liste de fichiers,
    chemins dupliqués ou hors projet, fichier de code sans contrat d'interface."""
    raw = raw.replace("\u2019", "'").replace("\u2018", "'")
    issues = _missing_heading_issues(raw, ARCHITECTURE_REQUIRED_HEADINGS)
    # Seule la section dédiée est lue : une puce de prose ("- Modifier App.tsx pour ...") ailleurs
    # dans le plan n'est pas une ligne de la liste de fichiers. Sans section, repli sur tout le texte.
    files_section = _markdown_section(raw, "Fichiers à créer ou modifier")
    entries = [m.group(1).strip() for m in _ARCH_FILE_LINE.finditer(files_section if files_section is not None else raw)]
    if not entries:
        issues.append("Aucune ligne « - CRÉER|MODIFIER <chemin> : <rôle> » dans la liste des fichiers.")
    issues.extend(_truncation_issues(raw))
    contracts = _markdown_section(raw, "Contrat")
    contract_lines = [line.strip().lstrip("-* ").replace("`", "") for line in (contracts or "").splitlines()]
    seen = set()
    for entry in entries:
        parsed = _ARCH_FILE_ENTRY.match(entry)
        if not parsed:
            issues.append(f"Ligne de fichier mal formée (attendu « chemin : rôle ») : « {entry[:70]} ».")
            continue
        path = parsed.group(1)
        if path.startswith("/") or ".." in path.split("/"):
            issues.append(f"Chemin hors du projet : {path}.")
        if path in seen:
            issues.append(f"Chemin listé plusieurs fois : {path}.")
        seen.add(path)
        if contracts is not None and path.endswith(_ARCH_CODE_EXTENSIONS) and not any(
            re.match(rf"^\**{re.escape(path)}\**\s*:", line) for line in contract_lines
        ):
            issues.append(f"{path} n'a pas de contrat d'interface (exports et signatures).")
    return issues

_architecture_guardrail = _make_issue_note_guardrail(_architecture_issues, "Contrôle automatique de l'architecture")

def _planned_paths(architecture_raw: str) -> set[str]:
    """Chemins de la section « Fichiers à créer ou modifier » du plan de l'Architecte."""
    section = _markdown_section((architecture_raw or "").replace("\u2019", "'"), "Fichiers à créer ou modifier")
    paths = set()
    for match in _ARCH_FILE_LINE.finditer(section or ""):
        entry = _ARCH_FILE_ENTRY.match(match.group(1).strip())
        normalized = normalize_path(entry.group(1)) if entry else None
        if normalized:
            paths.add(normalized)
    return paths

# Dépendances déclarées dans une section dédiée du plan, pas dans sa liste de fichiers : leur
# livraison n'est jamais « hors plan ».
_DEPENDENCY_FILES = {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"}

def _scope_notes(planned: set[str], delivered: set[str], not_delivered: set[str]) -> list[str]:
    """Écarts entre le plan de l'Architecte et ce que l'Analyste a livré (vide = conforme)."""
    if not planned:
        return []
    notes = []
    unplanned = sorted(p for p in delivered - planned if p.rsplit("/", 1)[-1] not in _DEPENDENCY_FILES)
    if unplanned:
        notes.append("Fichiers livrés HORS du plan de l'Architecte (à justifier, risque de régression) : " + ", ".join(unplanned))
    missing = sorted(planned - delivered - not_delivered)
    if missing:
        notes.append("Fichiers PRÉVUS par l'Architecte mais jamais livrés : " + ", ".join(missing))
    return notes
