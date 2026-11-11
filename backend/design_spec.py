"""Contrôle DÉTERMINISTE du document de specs du Designer (titres, identifiants F/AC, composants, termes vagues).

`blocking_issues` : défauts qui justifient UN refus avec reprise (voir CrewChecksMixin._design_guardrail) ; `spec_issues` : tous les défauts,
dont les critères vagues (signalés seulement en note, jamais cause de relance)."""
import re
from typing import List

from md_sections import markdown_section, missing_heading_issues

# Titres dont l'absence justifie un refus ; « Composants d'interface » n'a de sens que pour une demande avec interface (pas pour une analyse seule).
DESIGN_REQUIRED_HEADINGS = (
    "Besoin", "Utilisateurs", "Fonctionnalités", "Règles et cas limites", "Critères d'acceptation", "Composants d'interface", "Hypothèses retenues",
)
# Titre seulement NOTÉ (jamais cause de relance) : la section « Contrôle » est une auto-évaluation de l'agent, invérifiable.
DESIGN_NOTED_HEADINGS = ("Contrôle",)

_DESIGN_VAGUE_TERMS = re.compile(
    r"\b(rapide(?:ment)?|fluide|intuitif|intuitive|simple|convivial|ergonomique|joli|moderne)\b", re.IGNORECASE
)
_DESIGN_FEATURE_ID = r"(?<![A-Za-z-])F(\d+)\b"
_DESIGN_MUST_BEFORE = re.compile(_DESIGN_FEATURE_ID + r"[^\n]*\[\s*Must\s*\]", re.IGNORECASE)
_DESIGN_MUST_AFTER = re.compile(r"\[\s*Must\s*\][^\n]*?" + _DESIGN_FEATURE_ID, re.IGNORECASE)
_DESIGN_CRITERION_ID = re.compile(r"\bAC-F(\d+)[-.](\d+)")
# Définition d'une fonctionnalité : identifiant EN TÊTE de ligne (puce, gras ou ligne de tableau facultatifs), quel que soit le format de la priorité.
_FEATURE_DEF = re.compile(r"^[^\S\n]*+\|?+[^\S\n]*+(?:[-*][^\S\n]*+)?+\**+[^\S\n]*+F(\d+)\b", re.MULTILINE)    # possessif et [^\S\n] : temps linéaire
_FEATURE_REF = re.compile(_DESIGN_FEATURE_ID)


def _normalize(raw: str) -> str:
    # Apostrophes typographiques courantes dans une sortie LLM : sans normalisation, « Critères d’acceptation » serait signalé absent à tort.
    return raw.replace("’", "'").replace("‘", "'")


def _features_text(raw: str) -> str:
    """Section « Fonctionnalités » (sinon tout le document) : ni la section « Contrôle » ni les hypothèses ne définissent de fonctionnalité."""
    return markdown_section(raw, "Fonctionnalités") or raw


def _component_issues(raw: str, known: set[str]) -> List[str]:
    """Chaque composant d'interface renvoie à au moins une fonctionnalité EXISTANTE (« - NomDuComposant (F1) : états … »)."""
    section = markdown_section(raw, "Composants d'interface")
    issues = []
    for line in (section or "").splitlines():
        if not re.match(r"^[-*]\s", line) or line.strip(" -*") == "":          # puces de PREMIER niveau seulement : les états imbriqués ne sont pas des composants
            continue
        refs = {m.group(1) for m in _FEATURE_REF.finditer(line)}
        name = line.strip(" -*")[:60]
        if not refs:
            issues.append(f"Composant sans fonctionnalité : « {name} ».")
        for number in sorted(refs - known, key=int):
            issues.append(f"Composant « {name} » renvoie à F{number}, qui n'existe pas dans la liste des fonctionnalités.")
    return issues


def blocking_issues(raw: str, with_components: bool = True) -> List[str]:
    """Défauts qui justifient UN refus. `with_components` : False pour une demande sans interface (le titre et ses renvois ne sont pas exigés)."""
    raw = _normalize(raw)
    headings = [h for h in DESIGN_REQUIRED_HEADINGS if with_components or h != "Composants d'interface"]
    issues = missing_heading_issues(raw, headings)
    features = _features_text(raw)
    ids = [m.group(1) for m in _FEATURE_DEF.finditer(features)]
    for number in sorted({i for i in ids if ids.count(i) > 1}, key=int):
        issues.append(f"Identifiant F{number} utilisé par plusieurs fonctionnalités.")
    must_numbers = {m.group(1) for m in _DESIGN_MUST_BEFORE.finditer(features)} | {m.group(1) for m in _DESIGN_MUST_AFTER.finditer(features)}
    covered = {m.group(1) for m in _DESIGN_CRITERION_ID.finditer(raw)}
    for number in sorted(must_numbers - covered, key=int):
        issues.append(f"F{number} [Must] n'a aucun critère d'acceptation AC-F{number}-n.")
    if with_components and ids:                       # sans liste lisible de fonctionnalités, le titre manquant est déjà signalé : pas de bruit en plus
        known = set(ids) | must_numbers
        issues += _component_issues(raw, known)
    return issues


def spec_issues(raw: str, with_components: bool = True) -> List[str]:
    """Tous les défauts vérifiables : bloquants d'abord, puis titres seulement notés, puis critères vagues sans seuil mesurable."""
    raw = _normalize(raw)
    issues = blocking_issues(raw, with_components) + missing_heading_issues(raw, DESIGN_NOTED_HEADINGS)
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
