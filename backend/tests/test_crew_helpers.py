import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test")

cq = pytest.importorskip("crewquestion")


def output(raw):
    return type("O", (), {"raw": raw})()


def new_crew():
    crew = cq.AppDevelopmentCrew()
    crew._reset_execution_state()
    return crew


@pytest.mark.parametrize("text, expected", [
    ("Verdict final (après revue complète) : GO", "GO"),
    ("**Verdict** : NO GO", "NO GO"),
    ("Verdict : ✅ GO", "GO"),
    ("Verdict : GO avec réserves", "GO avec réserves"),
    ("Verdict : `NO_GO`", "NO_GO"),
    ("## Verdict\n\n**GO**", "GO"),
    ("Verdict\nGO_AVEC_RESERVES", "GO_AVEC_RESERVES"),
])
def test_qa_verdict_variants(text, expected):
    assert cq.QA_VERDICT.search(text).group(1) == expected


def test_qa_guardrail_adds_missing_verdict():
    ok, out = cq._qa_verdict_guardrail(output("rapport sans conclusion"))
    assert ok and "NON FOURNI" in out


def test_coerce_analysis_report_fixes_common_model_mistakes():
    report = cq._coerce_analysis_report(
        {"request_type": "bugfix", "confidence": 85, "alternative_type": "AUCUNE", "is_clear": True}
    )
    assert report.request_type == "BUGFIX"
    assert report.confidence == pytest.approx(0.85)
    assert report.alternative_type is None
    assert cq._coerce_analysis_report({"summary": "sans type"}) is None


def test_is_clear_string_false_is_false():
    report = cq._coerce_analysis_report({"request_type": "FEATURE", "confidence": 0.8, "is_clear": "false"})
    assert report.is_clear is False


def test_confidence_is_required_in_structured_output():
    with pytest.raises(Exception):
        cq.AnalysisReport(summary="s", request_type="FEATURE", is_clear=True)


def test_qualification_fallback_is_flagged_outside_llm_schema():
    result = cq.QualificationResult(summary="s", request_type="BUGFIX", confidence=0.0, is_clear=True)
    assert result.fallback is False
    assert "fallback" not in cq.AnalysisReport.model_json_schema()["properties"]


def test_low_confidence_forces_clarification_question():
    report = cq._enforce_confidence_threshold(cq.AnalysisReport(
        summary="s", request_type="FEATURE", alternative_type="BUGFIX", confidence=0.4, is_clear=True,
    ))
    assert not report.is_clear and "correction d'un bug" in report.questions[0]


def test_local_writes_are_confined_to_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(cq, "LOCAL_WORKSPACE_DIR", tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/App.tsx").write_text("old\n")
    rejections = {}
    message = cq._write_files_locally([
        {"path": "src/App.tsx", "content": "export {};\n"},
        {"path": "main.py", "content": "x = 1\n"},
        {"path": "../escape.py", "content": "x = 1\n"},
    ], rejections)
    assert (tmp_path / "src/App.tsx").read_text() == "export {};\n"
    assert (tmp_path / "main.py").exists()  # dans l'espace de travail, jamais le backend
    assert set(rejections) == {"../escape.py"} and "REJETÉS (1)" in message
    assert cq._read_local_file("src/App.tsx") == ("export {};\n", None)


def test_refused_local_write_is_reported_to_qa(tmp_path, monkeypatch):
    monkeypatch.setattr(cq, "LOCAL_WORKSPACE_DIR", tmp_path)
    crew = new_crew()
    crew._analyst_files = [{"path": "conf.json", "content": "{ invalide"}]
    assert "REJETÉS" in crew._build_commit_analyst_files_tool().run(owner="", repo="", branch="b", commit_message="m")
    assert "NON LIVRÉ" in crew._build_qa_verify_tool().run(owner="", repo="", branch="b")


def test_diagnostic_guardrail_excludes_shortcut_files_on_final_accept():
    crew = new_crew()
    raw = output(
        "<<<FICHIER: a.ts>>>\n```ts\n// ... reste du code\n```\n<<<FIN_FICHIER>>>\n"
        "<<<FICHIER: b.ts>>>\n```ts\nexport const b = 1;\n```\n<<<FIN_FICHIER>>>\n"
    )
    assert crew._diagnostic_guardrail(raw)[0] is False
    ok, out = crew._diagnostic_guardrail(raw)
    assert ok and "a.ts : NON réalisé" in out
    assert [f["path"] for f in crew._analyst_files] == ["b.ts"]
    assert "a.ts" in crew._not_extracted


def test_guardrail_retry_keeps_healthy_files_from_first_attempt():
    crew = new_crew()
    first = output(
        "<<<FICHIER: a.ts>>>\n// ... reste du code\n<<<FIN_FICHIER>>>\n"
        "<<<FICHIER: b.ts>>>\nexport const b = 1;\n<<<FIN_FICHIER>>>\n"
    )
    retry = output("<<<FICHIER: a.ts>>>\nexport const a = 1;\n<<<FIN_FICHIER>>>\n")
    assert crew._diagnostic_guardrail(first)[0] is False
    assert crew._diagnostic_guardrail(retry)[0] is True
    assert sorted(f["path"] for f in crew._analyst_files) == ["a.ts", "b.ts"]
    assert crew._not_extracted == {}


def test_commit_tool_forbids_committing_excluded_files():
    crew = new_crew()
    crew._not_extracted = {"src/App.tsx": "contenu incomplet"}
    message = crew._build_commit_analyst_files_tool().run(owner="o", repo="r", branch="b", commit_message="m")
    assert "Ne les committe JAMAIS" in message and "github_write_files" not in message


def test_github_rejections_are_tracked_per_latest_commit(monkeypatch):
    crew = new_crew()
    crew._analyst_files = [
        {"path": "package.json", "content": "{ invalide"},
        {"path": "src/a.ts", "content": "export {};\n"},
    ]
    tool = crew._build_commit_analyst_files_tool()

    monkeypatch.setattr(cq, "write_files_to_branch", lambda *a, **k: "ERREUR : non fast-forward")
    tool.run(owner="o", repo="r", branch="b", commit_message="m")
    assert "commit refusé" in crew._write_rejections["src/a.ts"]

    def partial_success(owner, repo, branch, message, files, sink):
        sink["package.json"] = "ERREUR_SYNTAXE : JSON invalide"
        return "OK : 1 fichier(s) écrit(s)"

    monkeypatch.setattr(cq, "write_files_to_branch", partial_success)
    tool.run(owner="o", repo="r", branch="b", commit_message="m")
    assert crew._write_rejections == {"package.json": "ERREUR_SYNTAXE : JSON invalide"}
