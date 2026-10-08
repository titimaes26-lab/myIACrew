"""Qualification du besoin : lecture tolérante du JSON, seuil de confiance, prompt, point d'accès."""

import pytest


cq = pytest.importorskip("crewquestion")
import qualification


def test_coerce_analysis_report_fixes_common_model_mistakes():
    report = qualification._coerce_analysis_report(
        {"request_type": "bugfix", "confidence": 85, "alternative_type": "AUCUNE", "is_clear": True}
    )
    assert report.request_type == "BUGFIX"
    assert report.confidence == pytest.approx(0.85)
    assert report.alternative_type is None
    assert qualification._coerce_analysis_report({"summary": "sans type"}) is None
    assert qualification._coerce_analysis_report({"request_type": "FEATURE", "confidence": 8}).confidence == pytest.approx(0.8)


def test_is_clear_string_false_is_false():
    report = qualification._coerce_analysis_report({"request_type": "FEATURE", "confidence": 0.8, "is_clear": "false"})
    assert report.is_clear is False


def test_confidence_is_required_in_structured_output():
    with pytest.raises(Exception):
        qualification.AnalysisReport(summary="s", request_type="FEATURE", is_clear=True)


def test_qualification_fallback_is_flagged_outside_llm_schema():
    result = qualification.QualificationResult(summary="s", request_type="BUGFIX", confidence=0.0, is_clear=True)
    assert result.fallback is False
    assert "fallback" not in qualification.AnalysisReport.model_json_schema()["properties"]


def test_low_confidence_forces_clarification_question():
    report = qualification._enforce_confidence_threshold(qualification.AnalysisReport(
        summary="s", request_type="FEATURE", alternative_type="BUGFIX", confidence=0.4, is_clear=True,
    ))
    assert not report.is_clear and "correction d'un bug" in report.questions[0]


def test_qualification_prompt_contains_examples_context_and_repo_line():
    with_repo = qualification._build_qualification_prompt("corrige ça", "Tour 1 : ajout d'un filtre", has_repo_target=True)
    without_repo = qualification._build_qualification_prompt("corrige ça")
    assert qualification.QUALIFICATION_EXAMPLES in with_repo
    assert "Tour 1 : ajout d'un filtre" in with_repo
    assert "Un repository GitHub cible est fourni." in with_repo
    assert "Aucun repository GitHub cible n'est fourni." in without_repo
    assert "Aucun échange précédent." in without_repo


def test_qualification_examples_use_valid_request_types():
    for line in qualification.QUALIFICATION_EXAMPLES.splitlines():
        if "->" in line:
            assert line.split("->")[1].split()[0].strip(",") in qualification._REQUEST_TYPES


def test_qualify_endpoint_forwards_has_repo_target(monkeypatch):
    import asyncio
    import schemas
    import routes_execute

    calls = []

    async def fake_analyze(user_request, conversation_context, has_repo_target):
        calls.append((user_request, conversation_context, has_repo_target))
        return qualification.QualificationResult(summary="s", request_type="BUGFIX", confidence=0.9, is_clear=True)

    monkeypatch.setattr(routes_execute.crew_instance, "analyze_user_request", fake_analyze)
    monkeypatch.setattr(routes_execute.crew_instance, "save_analysis_report", lambda *a, **k: None)

    asyncio.run(routes_execute.qualify_request(schemas.UserRequestInput(user_request="x", has_repo_target=True), {"id": "u"}))
    asyncio.run(routes_execute.qualify_request(schemas.UserRequestInput(user_request="y"), {"id": "u"}))
    assert [c[2] for c in calls] == [True, False]


def test_qualification_prompt_asks_for_repo_form_not_chat():
    prompt = qualification._build_qualification_prompt("corrige le bug")
    assert "Repository cible" in prompt and "« local »" in prompt


def test_qualification_examples_show_how_scope_is_filled():
    examples = qualification.QUALIFICATION_EXAMPLES
    assert "scope PETIT" in examples and examples.count("scope GRAND") >= 2
