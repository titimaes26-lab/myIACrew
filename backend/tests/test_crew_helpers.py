import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test")

cq = pytest.importorskip("crewquestion")


@pytest.mark.parametrize("text, expected", [
    ("Verdict final (après revue complète) : GO", "GO"),
    ("**Verdict** : NO GO", "NO GO"),
    ("Verdict : ✅ GO", "GO"),
    ("Verdict : GO avec réserves", "GO avec réserves"),
    ("Verdict : `NO_GO`", "NO_GO"),
])
def test_qa_verdict_variants(text, expected):
    assert cq.QA_VERDICT.search(text).group(1) == expected


def test_qa_guardrail_adds_missing_verdict():
    ok, out = cq._qa_verdict_guardrail(type("O", (), {"raw": "rapport sans conclusion"})())
    assert ok and "NON FOURNI" in out


def test_coerce_analysis_report_fixes_common_model_mistakes():
    report = cq._coerce_analysis_report(
        {"request_type": "bugfix", "confidence": 85, "alternative_type": "AUCUNE", "is_clear": True}
    )
    assert report.request_type == "BUGFIX"
    assert report.confidence == pytest.approx(0.85)
    assert report.alternative_type is None
    assert cq._coerce_analysis_report({"summary": "sans type"}) is None


def test_low_confidence_forces_clarification_question():
    report = cq._enforce_confidence_threshold(cq.AnalysisReport(
        summary="s", request_type="FEATURE", alternative_type="BUGFIX", confidence=0.4, is_clear=True,
    ))
    assert not report.is_clear and "correction d'un bug" in report.questions[0]


def test_local_write_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_text("original\n")
    message = cq._write_files_locally([
        {"path": "main.py", "content": "x = 1\n"},
        {"path": "src/new.ts", "content": "export {};\n"},
        {"path": "../escape.py", "content": "x = 1\n"},
    ])
    assert (tmp_path / "main.py").read_text() == "original\n"
    assert (tmp_path / "src/new.ts").exists()
    assert "écrasement refusé" in message and "hors du dossier" in message


def test_diagnostic_guardrail_excludes_shortcut_files_on_final_accept():
    crew = cq.AppDevelopmentCrew()
    crew._reset_execution_state()
    output = type("O", (), {"raw": (
        "### Fichier : a.ts\n```ts\n// ... reste du code\n```\n"
        "### Fichier : b.ts\n```ts\nexport const b = 1;\n```\n"
    )})()
    assert crew._diagnostic_guardrail(output)[0] is False
    ok, out = crew._diagnostic_guardrail(output)
    assert ok and "a.ts : NON réalisé" in out
    assert [f["path"] for f in crew._analyst_files] == ["b.ts"]
