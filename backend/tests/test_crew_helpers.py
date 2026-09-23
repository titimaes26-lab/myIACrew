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


def test_local_write_updates_files_but_protects_backend(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/App.tsx").write_text("old\n")
    message, refused = cq._write_files_locally([
        {"path": "src/App.tsx", "content": "export {};\n"},
        {"path": ".env", "content": "X=1\n"},
        {"path": "../escape.py", "content": "x = 1\n"},
    ])
    assert (tmp_path / "src/App.tsx").read_text() == "export {};\n"
    assert not (tmp_path / ".env").exists()
    assert "écrasement refusé" in message and "hors du dossier" in message
    assert set(refused) == {".env", "../escape.py"}


def test_backend_files_are_protected_when_cwd_is_backend(monkeypatch):
    monkeypatch.chdir(cq.BACKEND_DIR)
    assert cq._is_protected_local_target(cq.Path("main.py"))
    assert cq._is_protected_local_target(cq.Path("tests/test_crew_helpers.py"))
    assert not cq._is_protected_local_target(cq.Path("src/App.tsx"))


def test_refused_local_write_is_reported_to_qa(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    crew = cq.AppDevelopmentCrew()
    crew._reset_execution_state()
    crew._analyst_files = [{"path": ".env", "content": "X=1\n"}]
    commit = crew._build_commit_analyst_files_tool()
    assert "REJETÉS" in commit.run(owner="", repo="", branch="b", commit_message="m")
    report = crew._build_qa_verify_tool().run(owner="", repo="", branch="b")
    assert "NON LIVRÉ" in report


def test_is_clear_string_false_is_false():
    report = cq._coerce_analysis_report({"request_type": "FEATURE", "confidence": 0.8, "is_clear": "false"})
    assert report.is_clear is False


def test_commit_tool_forbids_committing_excluded_files():
    crew = cq.AppDevelopmentCrew()
    crew._reset_execution_state()
    crew._excluded_analyst_paths = ["src/App.tsx"]
    tool = crew._build_commit_analyst_files_tool()
    message = tool.run(owner="o", repo="r", branch="b", commit_message="m")
    assert "Ne les committe JAMAIS" in message and "github_write_files" not in message


def test_diagnostic_guardrail_excludes_shortcut_files_on_final_accept():
    crew = cq.AppDevelopmentCrew()
    crew._reset_execution_state()
    output = type("O", (), {"raw": (
        "<<<FICHIER: a.ts>>>\n```ts\n// ... reste du code\n```\n<<<FIN_FICHIER>>>\n"
        "<<<FICHIER: b.ts>>>\n```ts\nexport const b = 1;\n```\n<<<FIN_FICHIER>>>\n"
    )})()
    assert crew._diagnostic_guardrail(output)[0] is False
    ok, out = crew._diagnostic_guardrail(output)
    assert ok and "a.ts : NON réalisé" in out
    assert [f["path"] for f in crew._analyst_files] == ["b.ts"]
