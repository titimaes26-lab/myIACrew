import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test")

cq = pytest.importorskip("crewquestion")


def output(raw):
    return type("O", (), {"raw": raw})()


def new_crew(owner="", repo="", branch="feature/x"):
    crew = cq.AppDevelopmentCrew()
    crew._reset_execution_state({"repo_owner": owner, "repo_name": repo, "work_branch": branch})
    return crew


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
])
def test_qa_verdict_variants(text, expected):
    match = cq.QA_VERDICT.search(text)
    assert (match.group("eol") or match.group("upper")) == expected


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
    assert cq._coerce_analysis_report({"request_type": "FEATURE", "confidence": 8}).confidence == pytest.approx(0.8)


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


def test_local_writes_are_confined_to_workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/App.tsx").write_text("old\n")
    rejections = {}
    message = cq._write_files_locally(tmp_path, [
        {"path": "src/App.tsx", "content": "export {};\n"},
        {"path": "main.py", "content": "x = 1\n"},
        {"path": "../escape.py", "content": "x = 1\n"},
    ], rejections)
    assert (tmp_path / "src/App.tsx").read_text() == "export {};\n"
    assert (tmp_path / "main.py").exists()  # dans l'espace de travail, jamais le backend
    assert set(rejections) == {"../escape.py"} and "REJETÉS (1)" in message
    assert cq._read_local_file(tmp_path, "src/App.tsx") == ("export {};\n", None)


def test_each_conversation_has_its_own_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(cq, "LOCAL_WORKSPACE_DIR", tmp_path)
    a, b = cq._conversation_workspace("1"), cq._conversation_workspace("2")
    assert a != b and tmp_path in a.parents and tmp_path in b.parents


def test_local_mode_commit_read_and_qa_share_the_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(cq, "LOCAL_WORKSPACE_DIR", tmp_path)
    crew = new_crew(branch="claude/conv-1")
    crew._analyst_files = [{"path": "src/a.ts", "content": "export {};\n"}, {"path": "conf.json", "content": "{ invalide"}]
    assert "REJETÉS" in crew._build_commit_analyst_files_tool().run(commit_message="m")
    assert crew._build_local_read_tool().run(file_path="src/a.ts") == "export {};\n"
    report = crew._build_qa_verify_tool().run()
    assert "IDENTIQUE" in report and "NON LIVRÉ" in report


def test_empty_owner_from_llm_cannot_divert_a_github_run(monkeypatch):
    crew = new_crew(owner="o", repo="r", branch="feature/x")
    crew._analyst_files = [{"path": "src/a.ts", "content": "export {};\n"}]
    calls = []
    monkeypatch.setattr(cq, "write_files_to_branch", lambda *a, **k: calls.append(a[:3]) or "OK : 1")
    monkeypatch.setattr(cq, "_write_files_locally", lambda *a, **k: pytest.fail("écriture locale en mode GitHub"))
    crew._build_commit_analyst_files_tool().run(commit_message="m")
    assert calls == [("o", "r", "feature/x")]
    assert "github_read_file" in crew._build_local_read_tool().run(file_path="src/a.ts")


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


def test_retry_that_withdraws_a_file_removes_it():
    crew = new_crew()
    first = output(
        "<<<FICHIER: src/old.ts>>>\nexport const o = 1;\n<<<FIN_FICHIER>>>\n"
        "<<<FICHIER: src/b.ts>>>\n// ...\n<<<FIN_FICHIER>>>\n"
    )
    retry = output(
        "Plan : src/old.ts : NON réalisé (remplacé par src/new.ts). Fichiers NON réalisés : aucun autre\n"
        "<<<FICHIER: src/new.ts>>>\nexport const n = 1;\n<<<FIN_FICHIER>>>\n"
        "<<<FICHIER: src/b.ts>>>\nexport const b = 1;\n<<<FIN_FICHIER>>>\n"
    )
    crew._diagnostic_guardrail(first)
    crew._diagnostic_guardrail(retry)
    assert sorted(f["path"] for f in crew._analyst_files) == ["src/b.ts", "src/new.ts"]
    assert "src/old.ts" in crew._not_extracted
    assert not cq._withdrawn_paths("Fichiers src/b.ts — NON réalisés : aucun", ["src/b.ts"])


def test_retry_message_repeats_the_architecture_context():
    crew = new_crew()
    arch = crew.architecture_task()
    arch.output = type("Out", (), {"raw": "- CRÉER src/cart.ts : panier"})()
    crew.diagnostic_task().context = [arch]
    ok, message = crew._diagnostic_guardrail(output("aucun fichier"))
    assert not ok and "CRÉER src/cart.ts" in message


def test_commit_tool_forbids_committing_excluded_files():
    crew = new_crew()
    crew._not_extracted = {"src/App.tsx": "contenu incomplet"}
    message = crew._build_commit_analyst_files_tool().run(commit_message="m")
    assert "Ne les committe JAMAIS" in message and "github_write_files" not in message


def test_github_rejections_are_tracked_per_latest_commit(monkeypatch):
    crew = new_crew(owner="o", repo="r")
    crew._analyst_files = [
        {"path": "package.json", "content": "{ invalide"},
        {"path": "src/a.ts", "content": "export {};\n"},
    ]
    tool = crew._build_commit_analyst_files_tool()

    monkeypatch.setattr(cq, "write_files_to_branch", lambda *a, **k: "ERREUR : non fast-forward")
    tool.run(commit_message="m")
    assert "commit refusé" in crew._write_rejections["src/a.ts"]

    def partial_success(owner, repo, branch, message, files, sink):
        sink["package.json"] = "ERREUR_SYNTAXE : JSON invalide"
        return "OK : 1 fichier(s) écrit(s)"

    monkeypatch.setattr(cq, "write_files_to_branch", partial_success)
    tool.run(commit_message="m")
    assert crew._write_rejections == {"package.json": "ERREUR_SYNTAXE : JSON invalide"}


def test_local_workspace_is_per_conversation_even_without_branch(monkeypatch, tmp_path):
    monkeypatch.setattr(cq, "LOCAL_WORKSPACE_DIR", tmp_path)
    a, b = cq.AppDevelopmentCrew(), cq.AppDevelopmentCrew()
    a._reset_execution_state({"work_branch": "", "conversation_id": "1"})
    b._reset_execution_state({"work_branch": "", "conversation_id": "2"})
    assert a._workspace != b._workspace


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
])
def test_is_withdrawn(text, path, expected):
    assert (path in cq._withdrawn_paths(text, [path])) is expected


@pytest.mark.parametrize("text", [
    "verdict: No go-live possible sans tests",
    "Le verdict ne peut pas être rendu :\nGo figure",
])
def test_incidental_verdict_mentions_are_not_verdicts(text):
    assert cq.QA_VERDICT.search(text) is None
