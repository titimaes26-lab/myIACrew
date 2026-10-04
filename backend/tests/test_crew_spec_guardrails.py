"""Contrôles automatiques des specs, du plan d'architecture et du rapport QA (verdicts, fichiers retirés)."""

import pytest


cq = pytest.importorskip("crewquestion")
import qa_report
import crew_guardrails
import crew_llms
from crew_support import output, new_crew, GOOD_SPEC, GOOD_ARCH


@pytest.mark.parametrize("text, expected", [
    ("Verdict final (après revue complète) : GO", "GO"),
    ("**Verdict** : NO GO", "NO GO"),
    ("Verdict : ✅ GO", "GO"),
    ("Verdict : GO avec réserves", "GO avec réserves"),
    ("Verdict : `NO_GO`", "NO_GO"),
    ("## Verdict\n\n**GO**", "GO"),
    ("Verdict\nGO_AVEC_RESERVES", "GO_AVEC_RESERVES"),
    ("Verdict — GO", "GO"),
    ("Verdict : NON GO", "NON GO"),
    ("Verdict : GO ✅", "GO"),
    ("Verdict : NO_GO, 3 bloquants", "NO_GO"),
    ("Verdict : NO_GO car les tests échouent", "NO_GO"),
    ("Verdict : GO avec réserves mineures", "GO"),
    ("Verdict : GO\r\n", "GO"),
    ("Verdict : go", "go"),
    ("Verdict : Go ✅", "Go"),
    ("Verdict : No go, 2 bloquants", "No go"),
    ("| Verdict | GO |", "GO"),
    ("Verdict (2 échecs mineurs | tolérés) : GO", "GO"),
])
def test_qa_verdict_variants(text, expected):
    match = qa_report.QA_VERDICT.search(text)
    assert (match.group("eol") or match.group("upper")) == expected


def test_qa_guardrail_adds_missing_verdict():
    ok, out = crew_guardrails._qa_verdict_guardrail(output("rapport sans conclusion"))
    assert ok and "NON FOURNI" in out


def test_design_spec_issues_complete_spec_has_none():
    assert crew_guardrails._design_spec_issues(GOOD_SPEC) == []


def test_design_spec_issues_flags_must_without_criterion():
    issues = crew_guardrails._design_spec_issues(GOOD_SPEC.replace("- F2 [Should] Filtrer", "- F2 [Must] Filtrer"))
    assert any("F2 [Must]" in i for i in issues) and not any("F1 [Must]" in i for i in issues)


def test_design_spec_issues_flags_vague_criterion_but_not_measurable_one():
    vague = crew_guardrails._design_spec_issues(GOOD_SPEC + "- AC-F1-2 Alors l'écran s'affiche rapidement\n")
    assert any("Critère vague" in i for i in vague)
    assert crew_guardrails._design_spec_issues(GOOD_SPEC + "- AC-F1-3 Alors l'écran s'affiche rapidement en moins de 2 s\n") == []


def test_design_spec_issues_flags_missing_sections():
    issues = crew_guardrails._design_spec_issues("## Besoin\nseulement ça")
    assert any("Utilisateurs" in i for i in issues) and any("Hypothèses retenues" in i for i in issues)


def test_design_spec_issues_accepts_typographic_apostrophe():
    assert crew_guardrails._design_spec_issues(GOOD_SPEC.replace("d'acceptation", "d\u2019acceptation")) == []


def test_design_spec_issues_accepts_alternative_id_formats():
    spec = GOOD_SPEC.replace("- F1 [Must] Ajouter une tâche", "- [Must] **F1** Ajouter une tâche").replace("AC-F1-1", "AC-F1.1")
    assert crew_guardrails._design_spec_issues(spec) == []
    missing = crew_guardrails._design_spec_issues(spec.replace("AC-F1.1", "critère"))
    assert any("F1 [Must]" in i for i in missing)


def test_design_spec_issues_context_digit_does_not_excuse_vague_outcome():
    spec = GOOD_SPEC + "- AC-F1-2 Étant donné 3 tâches / Quand je filtre / Alors l'affichage est fluide\n"
    assert any("Critère vague" in i for i in crew_guardrails._design_spec_issues(spec))
    ok = GOOD_SPEC + "- AC-F1-2 Étant donné 3 tâches / Quand je filtre / Alors l'affichage prend moins de 200 ms\n"
    assert crew_guardrails._design_spec_issues(ok) == []


def test_design_spec_guardrail_never_fails_and_annotates_only_on_issues():
    class Out:
        raw = GOOD_SPEC
    out = Out()
    ok, result = crew_guardrails._design_spec_guardrail(out)
    assert ok is True and result is out
    Out.raw = "## Besoin\nx"
    ok, result = crew_guardrails._design_spec_guardrail(Out())
    assert ok is True and "## Contrôle automatique des specs" in result


def test_design_task_yaml_placeholders_are_known():
    import pathlib
    import string
    import yaml
    tasks = yaml.safe_load((pathlib.Path(cq.__file__).parent / "tasksquestion.yaml").read_text(encoding="utf-8"))
    known = {"user_request", "conversation_context", "repo_instructions", "repo_owner", "repo_name", "base_branch", "repo_snapshot", "previous_plan"}
    for name in ("design_task", "architecture_task", "diagnostic_task", "development_task"):
        fields = {f[1] for f in string.Formatter().parse(tasks[name]["description"]) if f[1]}
        assert fields <= known | {"work_branch"}, name


def test_architecture_prompt_keeps_its_static_instructions_before_the_variable_data():
    # Préfixe stable (consignes fixes) puis données variables : le cache de préfixe du fournisseur ne sert que si
    # RIEN de variable ne précède les consignes.
    import pathlib
    import string
    import yaml
    tasks = yaml.safe_load((pathlib.Path(cq.__file__).parent / "tasksquestion.yaml").read_text(encoding="utf-8"))
    description = tasks["architecture_task"]["description"]
    marker = "=== Données de cette demande ==="
    assert description.count(marker) == 1
    static, variable = description.split(marker)
    assert not [f[1] for f in string.Formatter().parse(static) if f[1]]
    assert {f[1] for f in string.Formatter().parse(variable) if f[1]} == {
        "user_request", "conversation_context", "repo_instructions", "repo_snapshot", "previous_plan"}
    assert "APERÇU DU REPOSITORY" in static


def test_architecture_prompt_stays_short_and_still_names_every_required_section():
    import pathlib
    import yaml
    tasks = yaml.safe_load((pathlib.Path(cq.__file__).parent / "tasksquestion.yaml").read_text(encoding="utf-8"))
    task = tasks["architecture_task"]
    assert len(task["description"].split()) <= 520
    text = task["description"] + task["expected_output"]
    for heading in crew_guardrails.ARCHITECTURE_REQUIRED_HEADINGS:
        assert heading in text, heading
    assert "Ne propose jamais un paquet déjà couvert" in task["description"] and "200 lignes" in task["description"]
    assert "signatures seules" in task["description"] and "=== Données de cette demande ===" in task["description"]
    # Le format lu par le garde-fou (« - CRÉER|MODIFIER <chemin> : <rôle> ») doit rester demandé.
    assert "- CRÉER|MODIFIER <chemin> : <rôle" in task["description"]


def test_only_the_architect_output_is_capped_and_make_llm_omits_the_cap_by_default():
    assert crew_llms.architect_llm.max_tokens == 8192
    for llm in (crew_llms.qualification_llm, crew_llms.designer_llm, crew_llms.diagnostic_llm, crew_llms.developer_llm, crew_llms.qa_llm):
        assert llm.max_tokens is None
    assert crew_llms._make_llm(0.1).max_tokens is None
    assert crew_llms._make_llm(0.1, max_tokens=123).max_tokens == 123


@pytest.mark.parametrize("env, expected", [(None, 8192), ("6000", 6000), ("abc", 8192), ("100", 8192), ("", 8192)])
def test_architect_output_cap_is_configurable_with_a_safe_default(monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("ARCHITECT_MAX_OUTPUT_TOKENS", raising=False)
    else:
        monkeypatch.setenv("ARCHITECT_MAX_OUTPUT_TOKENS", env)
    assert crew_llms._architect_max_tokens() == expected


def test_architecture_issues_flag_a_plan_cut_by_the_token_limit():
    empty_last = GOOD_ARCH.replace("- Taille : découper\n", "")
    assert any("tronquée" in i for i in crew_guardrails._architecture_issues(empty_last))
    cut = GOOD_ARCH.rstrip() + "\n- Dépendance : paquet,"
    assert any("tronquée" in i for i in crew_guardrails._architecture_issues(cut))
    unclosed = GOOD_ARCH + "```ts\nexport const x ="
    assert any("tronquée" in i for i in crew_guardrails._architecture_issues(unclosed))


def test_architecture_issues_complete_plan_has_none():
    assert crew_guardrails._architecture_issues(GOOD_ARCH) == []


def test_architecture_issues_flags_missing_sections_and_empty_file_list():
    issues = crew_guardrails._architecture_issues("## Existant\nrien")
    assert any("Cible" in i for i in issues) and any("Aucune ligne" in i for i in issues)


def test_architecture_issues_flags_malformed_duplicate_and_outside_paths():
    plan = GOOD_ARCH.replace(
        "- MODIFIER src/hooks/useCart.ts : logique du panier",
        "- MODIFIER src/hooks/useCart.ts : logique du panier\n- CRÉER src/App.tsx : doublon\n"
        "- CRÉER ../evil.ts : hors projet\n- CRÉER src/x.ts sans rôle",
    )
    issues = crew_guardrails._architecture_issues(plan)
    assert any("plusieurs fois" in i for i in issues)
    assert any("hors du projet" in i for i in issues)
    assert any("mal formée" in i for i in issues)


def test_architecture_issues_flags_code_file_without_contract():
    plan = GOOD_ARCH.replace("- MODIFIER src/hooks/useCart.ts : logique du panier", "- MODIFIER src/hooks/useCart.ts : logique du panier\n- CRÉER src/utils/format.ts : formats")
    issues = crew_guardrails._architecture_issues(plan)
    assert any("src/utils/format.ts n'a pas de contrat" in i for i in issues)
    assert not any("useCart" in i for i in issues)


def test_architecture_guardrail_never_fails_and_annotates_only_on_issues():
    class Out:
        raw = GOOD_ARCH
    out = Out()
    ok, result = crew_guardrails._architecture_guardrail(out)
    assert ok is True and result is out
    Out.raw = "## Existant\nrien"
    ok, result = crew_guardrails._architecture_guardrail(Out())
    assert ok is True and "## Contrôle automatique de l'architecture" in result


def test_architecture_issues_ignores_prose_bullets_outside_file_list():
    plan = GOOD_ARCH.replace("src/ avec composants.", "- Modifier App.tsx pour brancher le panier\n- Créer un hook useCart : état du panier")
    assert crew_guardrails._architecture_issues(plan) == []


def test_architecture_issues_contract_section_survives_later_mentions_of_contrat():
    plan = GOOD_ARCH + "- Un contrat d'API flou entre composants : figer les props\n"
    assert crew_guardrails._architecture_issues(plan) == []


def test_architecture_issues_contract_match_is_exact_path_not_substring():
    plan = GOOD_ARCH.replace("src/hooks/useCart.ts : export", "src/hooks/useCart.tsx : export")
    assert any("src/hooks/useCart.ts n'a pas de contrat" in i for i in crew_guardrails._architecture_issues(plan))


def test_architecture_issues_accepts_bold_and_backticked_entries():
    plan = GOOD_ARCH.replace("- CRÉER src/App.tsx : composant racine", "- **CRÉER** `src/App.tsx` : composant racine")
    assert crew_guardrails._architecture_issues(plan) == []


def test_architecture_task_has_a_non_retrying_guardrail():
    task = new_crew().architecture_task()
    assert task.guardrail is not None and task.guardrail_max_retries == 0


@pytest.mark.parametrize("text, path, expected", [
    ("- src/big.ts — NON réalisé : fichier trop volumineux", "src/big.ts", True),
    ("- src/a.ts réalisé ; src/b.ts NON réalisé (trop gros)", "src/a.ts", False),
    ("- src/a.ts réalisé ; src/b.ts NON réalisé (trop gros)", "src/b.ts", True),
    ("Fichiers src/b.ts — NON réalisés : aucun", "src/b.ts", False),
    ("- src/App.tsx : réécrit en entier (rien de NON réalisé)", "src/App.tsx", False),
    ("- src/App.tsx.bak NON réalisé", "src/App.tsx", False),
    ("- Dockerfile — NON réalisé (trop long)", "Dockerfile", True),
    ("- app/(auth)/page.tsx — NON réalisé", "app/(auth)/page.tsx", True),
    ("- src/[id].tsx — NON réalisé", "src/[id].tsx", True),
    ("- src/a.ts, src/b.ts — NON réalisés (trop gros)", "src/a.ts", True),
    ("- src/a.ts : migration React 18.2 NON réalisée", "src/a.ts", True),
    ("- src/utils/casino.ts piano NON réalisé", "src/utils/casino.ts", True),
    ("| src/a.ts | NON réalisé | trop gros |", "src/a.ts", True),
    ("- ./src/a.ts — NON réalisé", "src/a.ts", True),
    ("Fichiers src/a.ts — NON réalisé(s) : aucun", "src/a.ts", False),
    ("Fichiers src/a.ts — tâches NON réalisées : aucune", "src/a.ts", False),
    ("- src/a.ts réalisé, src/b.ts NON réalisé", "src/a.ts", False),
    ("<<<FICHIER: src/a.ts>>>\n// src/a.ts NON réalisé\n<<<FIN_FICHIER>>>", "src/a.ts", False),
    # Chemin APRÈS la mention (pas seulement avant) : "NON réalisé : <chemin> (raison)".
    ("NON réalisé : src/b.ts (trop volumineux)", "src/b.ts", True),
    # Ordre inversé "mot-clé : chemin" (pas "chemin mot-clé") : le mot positif "Réalisé"
    # réclame IMMÉDIATEMENT son propre chemin, qui ne doit pas se faire retirer par la
    # mention négative suivante sur la même ligne.
    ("Réalisé : src/a.ts. NON réalisé : src/b.ts", "src/a.ts", False),
    ("Réalisé : src/a.ts. NON réalisé : src/b.ts", "src/b.ts", True),
    # La raison entre parenthèses ne doit jamais être prise pour un 2e chemin retiré.
    ("- src/old.ts : NON réalisé (remplacé par src/new.ts)", "src/new.ts", False),
    ("- src/old.ts : NON réalisé (remplacé par src/new.ts)", "src/old.ts", True),
    # Plusieurs chemins réclamés par un mot positif ("Réalisé : a, c") ne sont pas
    # récupérables par une mention négative plus loin sur la même ligne.
    ("Réalisé : src/a.ts, src/c.ts. NON réalisé : src/b.ts (trop complexe).", "src/a.ts", False),
    ("Réalisé : src/a.ts, src/c.ts. NON réalisé : src/b.ts (trop complexe).", "src/b.ts", True),
    # Plusieurs chemins listés APRÈS une même mention négative sont tous retirés.
    ("NON réalisé : src/b.ts, src/d.ts (trop gros)", "src/b.ts", True),
    # Aucune ponctuation entre deux fichiers : pas de "réclamation" par erreur du 2e.
    ("src/a.ts réalisé src/b.ts NON réalisé", "src/b.ts", True),
])
def test_is_withdrawn(text, path, expected):
    assert (path in crew_guardrails._withdrawn_paths(text, [path])) is expected


def test_is_withdrawn_with_full_candidate_set():
    # _withdrawn_paths est TOUJOURS appelée en production avec l'ensemble des chemins connus
    # (voir _diagnostic_guardrail : list(merged) + sorted(delivered_now)) — ces deux cas de
    # liste ne peuvent être jugés correctement qu'avec les DEUX chemins comme candidats : un
    # chemin absent des candidats ne peut pas être "traversé" pour continuer la liste.
    withdrawn = crew_guardrails._withdrawn_paths(
        "Réalisé : src/a.ts, src/c.ts. NON réalisé : src/b.ts (trop complexe).",
        ["src/a.ts", "src/b.ts", "src/c.ts"],
    )
    assert withdrawn == {"src/b.ts"}
    withdrawn = crew_guardrails._withdrawn_paths("NON réalisé : src/b.ts, src/d.ts (trop gros)", ["src/b.ts", "src/d.ts"])
    assert withdrawn == {"src/b.ts", "src/d.ts"}
    # Liste à la virgule après un mot positif dont le DERNIER élément est en réalité la cible
    # de la mention négative qui le suit directement (avec ou sans virgule) : seul le test avec
    # les DEUX chemins candidats exerce vraiment le "regard en avant" de _consume_adjacent_paths
    # (un seul candidat laisserait la recherche brute sur `clause` donner la même réponse pour
    # une mauvaise raison, sans jamais passer par ce mécanisme).
    assert crew_guardrails._withdrawn_paths("Réalisé : src/a.ts, src/b.ts NON réalisé", ["src/a.ts", "src/b.ts"]) == {"src/b.ts"}
    assert crew_guardrails._withdrawn_paths("Réalisé : src/a.ts src/b.ts NON réalisé", ["src/a.ts", "src/b.ts"]) == {"src/b.ts"}
    # Un SEUL chemin (pas de liste à la virgule) suivi directement d'une mention négative : le
    # garde-fou du "regard en avant" doit s'appliquer dès le 1er chemin, pas seulement à partir
    # du 2e élément d'une liste.
    assert crew_guardrails._withdrawn_paths("Réalisé : src/a.ts NON réalisé", ["src/a.ts"]) == {"src/a.ts"}


@pytest.mark.parametrize("text", [
    "verdict: No go-live possible sans tests",
    "Le verdict ne peut pas être rendu :\nGo figure",
])
def test_incidental_verdict_mentions_are_not_verdicts(text):
    assert qa_report.QA_VERDICT.search(text) is None
