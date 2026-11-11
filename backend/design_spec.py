"""Contrôle DÉTERMINISTE du document de specs du Designer (titres, identifiants F/AC, composants, termes vagues).

`blocking_issues` : défauts qui justifient UN refus avec reprise (voir CrewChecksMixin._design_guardrail) ; `spec_issues` : tous les défauts,
dont les critères vagues (signalés seulement en note, jamais cause de relance)."""
import re
from typing import List

from md_sections import markdown_section, missing_heading_issues

DESIGN_REQUIRED_HEADINGS = (
    "Besoin", "Utilisateurs", "Fonctionnalités", "Règles et cas limites",
    "Critères d'acceptation", "Composants d'interface", "Hypothèses retenues", "Contrôle",
)

_DESIGN_VAGUE_TERMS = re.compile(
    r"\b(rapide(?:ment)?|fluide|intuitif|intuitive|simple|convivial|ergonomique|joli|moderne)\b", re.IGNORECASE
)
_DESIGN_FEATURE_ID = r"(?<![A-Za-z-])F(\d+)\b"
_DESIGN_MUST_BEFORE = re.compile(_DESIGN_FEATURE_ID + r"[^\n]*\[\s*Must\s*\]", re.IGNORECASE)
_DESIGN_MUST_AFTER = re.compile(r"\[\s*Must\s*\][^\n]*?" + _DESIGN_FEATURE_ID, re.IGNORECASE)
_DESIGN_CRITERION_ID = re.compile(r"\bAC-F(\d+)[-.](\d+)")
# Ligne de fonctionnalité MoSCoW : « - F1 [Must] titre » (identifiant AVANT la priorité entre crochets).
_FEATURE_LINE = re.compile(_DESIGN_FEATURE_ID + r"[^\n\[]*\[\s*(?:Must|Should|Could|Won'?t)\s*\]", re.IGNORECASE)
_FEATURE_REF = re.compile(_DESIGN_FEATURE_ID)


def _normalize(raw: str) -> str:
    # Apostrophes typographiques courantes dans une sortie LLM : sans normalisation, « Critères d’acceptation » serait signalé absent à tort.
    return raw.replace("’", "'").replace("‘", "'")


def _feature_ids(raw: str) -> List[str]:
    return [m.group(1) for m in _FEATURE_LINE.finditer(raw)]


def _component_issues(raw: str, known: set[str]) -> List[str]:
    """Chaque composant d'interface renvoie à au moins une fonctionnalité EXISTANTE (« - NomDuComposant (F1) : états … »)."""
    section = markdown_section(raw, "Composants d'interface")
    issues = []
    for line in (section or "").splitlines():
        if not re.match(r"^\s*[-*]\s", line) or line.strip(" -*") == "":
            continue
        refs = {m.group(1) for m in _FEATURE_REF.finditer(line)}
        name = line.strip(" -*")[:60]
        if not refs:
            issues.append(f"Composant sans fonctionnalité : « {name} ».")
        for number in sorted(refs - known, key=int):
            issues.append(f"Composant « {name} » renvoie à F{number}, qui n'existe pas dans la liste des fonctionnalités.")
    return issues


def blocking_issues(raw: str) -> List[str]:
    raw = _normalize(raw)
    issues = missing_heading_issues(raw, DESIGN_REQUIRED_HEADINGS)
    ids = _feature_ids(raw)
    for number in sorted({i for i in ids if ids.count(i) > 1}, key=int):
        issues.append(f"Identifiant F{number} utilisé par plusieurs fonctionnalités.")
    must_numbers = {m.group(1) for m in _DESIGN_MUST_BEFORE.finditer(raw)} | {m.group(1) for m in _DESIGN_MUST_AFTER.finditer(raw)}
    covered = {m.group(1) for m in _DESIGN_CRITERION_ID.finditer(raw)}
    for number in sorted(must_numbers - covered, key=int):
        issues.append(f"F{number} [Must] n'a aucun critère d'acceptation AC-F{number}-n.")
    if ids:                                         # sans liste lisible de fonctionnalités, le titre manquant est déjà signalé : pas de bruit en plus
        issues += _component_issues(raw, set(ids) | must_numbers)
    return issues


def spec_issues(raw: str) -> List[str]:
    """Tous les défauts vérifiables : bloquants d'abord, puis critères vagues sans seuil mesurable."""
    raw = _normalize(raw)
    issues = blocking_issues(raw)
    for line in raw.splitlines():
        criterion = _DESIGN_CRITERION_ID.search(line)
        if criterion:
            body = line[criterion.end():]
            # Seuil attendu dans le résultat observable ("Alors ..."), pas dans le contexte ("Étant donné 3 tâches").
            outcome = re.search(r"\bAlors\b", body, re.IGNORECASE)
            outcome_text = body[outcome.end():] if outcome else body
            if _DESIGN_VAGUE_TERMS.search(outcome_text) and not re.search(r"\d", outcome_text):
                issues.append(f"Critère vague sans seuil mesurable : « {line.strip()[:90]} ».")
    return issues
