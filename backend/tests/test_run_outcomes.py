"""Issue d'une exécution : messages d'échec et de livraison, blocs du résultat partiel, décision de relance."""
"""Étapes extraites de _run_crew_and_persist : fonctions pures ou presque, testées une à une."""
import asyncio
from types import SimpleNamespace


import pytest

import execution_outcomes
import execution_context
import execution_resume
import execution_persistence
import execution_state
from crew_workflow import CrewStepError
from errors import classify_exception
from run_support import no_network_snapshot  # noqa: E402,F401  (fixture automatique : jamais de réseau pour l'aperçu du dépôt)
from run_support import _data, _step_error


def test_delivery_failure_message_depends_on_the_kind_of_problem():
    access = execution_outcomes.delivery_failure_message(SimpleNamespace(message="branche introuvable", likely_access_problem=True), "rapport")
    other = execution_outcomes.delivery_failure_message(SimpleNamespace(message="PR manquante", likely_access_problem=False), "rapport")
    assert "Vérifie la configuration GITHUB_TOKEN" in access and "branche introuvable" in access
    assert "n'a pas terminé sa procédure" in other and "PR manquante" in other
    assert "--- Rapport de l'agent (non vérifié sur GitHub) ---\nrapport" in access
    long = execution_outcomes.delivery_failure_message(SimpleNamespace(message="m", likely_access_problem=True), "x" * 5000)
    assert long.endswith("x" * 3000) and "x" * 3001 not in long


def test_pull_request_line_is_appended_on_its_own_lines_without_a_section_separator():
    merged = SimpleNamespace(merged=True, html_url="https://github.com/o/r/pull/1")
    opened = SimpleNamespace(merged=False, html_url="https://github.com/o/r/pull/2")
    out = execution_outcomes.with_pull_request_line("## Résumé\n\ntexte", merged)
    assert out == "## Résumé\n\ntexte\n\n**Pull Request fusionnée :** https://github.com/o/r/pull/1"
    assert "**Pull Request ouverte :**" in execution_outcomes.with_pull_request_line("x", opened)
    assert "\n\n---\n\n## " not in out and "\\n" not in out
    assert execution_outcomes.with_pull_request_line("x", None) == "x"


def test_failure_detail_names_the_step_and_keeps_the_technical_text():
    error = CrewStepError(2, 5, "Architecte", RuntimeError("429 quota " + "z" * 600))
    detail = execution_outcomes.failure_detail(error, classify_exception(error))
    assert detail.startswith("Échec à l'étape 2/5 (Architecte) : Le quota du modèle IA")
    assert "(détail : 429 quota" in detail and len(detail) < 800 + 3800
    # Régression : le rapport de l'agent joint à un échec de livraison ne doit pas être coupé à 500 caractères.
    # Même avec un constat très long, la fin du rapport (3000 caractères au plus) reste.
    issue = SimpleNamespace(message="m" * 600, likely_access_problem=False)
    report = "RAPPORT-FINAL " + "r" * 2900 + " FIN-DU-RAPPORT"
    delivery = execution_outcomes.DeliveryError(execution_outcomes.delivery_failure_message(issue, report))
    info = classify_exception(delivery)
    kept = execution_outcomes.failure_detail(delivery, info)
    assert info.code == "DELIVERY_FAILED" and "Rapport de l'agent" in kept and "FIN-DU-RAPPORT" in kept
    assert kept.startswith("Un repository GitHub cible")
    plain = RuntimeError("bug interne")
    assert execution_outcomes.failure_detail(plain, classify_exception(plain)) == "bug interne"


def test_failure_report_separates_blocks_with_real_blank_lines(monkeypatch):
    # Régression : un « \\n » littéral (au lieu d'un saut de ligne) collait le bloc GitHub au message.
    async def fake_block(*args):
        return "--- Travail déjà présent sur GitHub ---\n- Rien n'a été poussé"
    monkeypatch.setattr(execution_outcomes, "partial_delivery_block", fake_block)

    class Session:
        def add(self, *a):
            pass

        def commit(self):
            pass

        def refresh(self, *a):
            pass

        def exec(self, *a, **k):
            raise AssertionError("pas de lecture attendue")

    entry = SimpleNamespace(id=1, result=None, status="running", current_step="qa", error_code=None,
                            error_retryable=None, updated_at=None, api_calls_count=None)
    conversation = SimpleNamespace(updated_at=None)
    monkeypatch.setattr(execution_state, "safe_refresh", lambda *a, **k: None)
    monkeypatch.setattr(execution_persistence, "cleanup_persisted_agents", lambda *a: None)
    error = RuntimeError("boum")
    asyncio.run(execution_outcomes.persist_failure(
        Session(), entry, conversation, error, classify_exception(error), _data(), "crewai/b", "main", True, execution_context.RunState(),
    ))
    assert entry.status == "failed" and entry.current_step is None
    assert entry.result == "boum\n\n--- Travail déjà présent sur GitHub ---\n- Rien n'a été poussé"


def _persist_failure_with_result(monkeypatch, previous_result):
    async def fake_block(*args):
        return "--- Travail déjà présent sur GitHub ---\n- Rien n'a été poussé"
    monkeypatch.setattr(execution_outcomes, "partial_delivery_block", fake_block)

    class Session:
        def add(self, *a):
            pass

        def commit(self):
            pass

        def refresh(self, *a):
            pass

    entry = SimpleNamespace(id=1, result=previous_result, status="running", current_step="qa", error_code=None,
                            error_retryable=None, updated_at=None, api_calls_count=None)
    monkeypatch.setattr(execution_state, "safe_refresh", lambda *a, **k: None)
    monkeypatch.setattr(execution_persistence, "cleanup_persisted_agents", lambda *a: None)
    error = RuntimeError("boum")
    asyncio.run(execution_outcomes.persist_failure(
        Session(), entry, SimpleNamespace(updated_at=None), error, classify_exception(error), _data(), "crewai/b", "main",
        True, execution_context.RunState(),
    ))
    return entry.result


def test_failure_lists_work_already_done_after_github_block(monkeypatch):
    previous = "## Architecte Logiciel\n\nPlan en trois blocs\n\n---\n\n## Analyste Diagnostic Technique\n\nCause trouvée"
    result = _persist_failure_with_result(monkeypatch, previous)
    github = result.index("--- Travail déjà présent sur GitHub ---")
    partial = result.index("--- Déjà réalisé avant l'échec ---")
    assert github < partial
    assert "2 étapes terminées avant l'échec" in result
    assert "- Architecte Logiciel : Plan en trois blocs" in result and "- Analyste Diagnostic Technique : Cause trouvée" in result


def test_failure_without_completed_agents_has_no_partial_block(monkeypatch):
    assert "Déjà réalisé" not in _persist_failure_with_result(monkeypatch, None)


@pytest.mark.parametrize("scenario, expected", [
    ("transient_early_with_checkpoints", {"design": "d"}),
    ("not_a_step_error", None),
    ("auto_retry_not_allowed", None),
    ("not_retryable", None),
    ("missing_checkpoint_for_a_finished_step", None),
    ("failure_at_development", None),
])
def test_retry_decision(monkeypatch, scenario, expected):
    saved = {"design": "d"}
    error = _step_error(2)
    allowed = True
    if scenario == "not_a_step_error":
        error = RuntimeError("503 UNAVAILABLE")
    elif scenario == "auto_retry_not_allowed":
        allowed = False
    elif scenario == "not_retryable":
        error = _step_error(2, "bug interne")
    elif scenario == "missing_checkpoint_for_a_finished_step":
        error, saved = _step_error(3), {"design": "d"}  # étape 3 en échec mais l'étape 2 n'a pas de sauvegarde
    elif scenario == "failure_at_development":
        error = _step_error(4)
    monkeypatch.setattr(execution_persistence, "load_checkpoints_for", lambda execution_id: saved)
    data = _data(target_workflow="DESIGN_AND_DEV")
    result = asyncio.run(execution_outcomes.retry_outputs_if_transient(error, classify_exception(error), 1, data, allowed))
    assert result == expected


def test_a_small_feature_failure_is_judged_against_its_own_steps():
    # Sans l'architecture, l'étape 1 est le Diagnostic (reprenable) et l'étape 2 le développement (jamais rejoué).
    before_dev = execution_resume.failed_before_development(CrewStepError(1, 3, "Analyste", RuntimeError("x")), "FEATURE", "PETIT")
    in_dev = execution_resume.failed_before_development(CrewStepError(2, 3, "Développeur", RuntimeError("x")), "FEATURE", "PETIT")
    assert before_dev is True and in_dev is False
    # Même rang d'étape dans le parcours complet : c'est l'architecture (reprenable) pour 1, le diagnostic pour 2.
    assert execution_resume.failed_before_development(CrewStepError(2, 4, "Analyste", RuntimeError("x")), "FEATURE") is True
