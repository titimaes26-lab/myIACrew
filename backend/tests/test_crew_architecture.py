"""Contrôles automatiques du plan d'architecture : sections, fichiers, contrats, plafond de sortie du modèle."""
"""Contrôles automatiques des specs, du plan d'architecture et du rapport QA (verdicts, fichiers retirés)."""

import pytest


cq = pytest.importorskip("crewquestion")
import crew_guardrails
import crew_llms
from crew_support import new_crew, GOOD_ARCH


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
