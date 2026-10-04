"""Garde-fou du diagnostic et commit des fichiers de l'Analyste : modifications ciblées, imports, reprise, fichiers retirés."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test")

cq = pytest.importorskip("crewquestion")
import crew_tools  # noqa: E402
import crew_guardrails  # noqa: E402
import crew_workspace  # noqa: E402
from crew_support import output, new_crew, _edit_block  # noqa: E402


def test_guardrail_resolves_a_targeted_edit_into_a_complete_committable_file():
    crew = new_crew()
    crew._base_readers = (lambda path: ("const a = 1;\nconst b = 2;\n", None), lambda d: None)
    ok, _ = crew._diagnostic_guardrail(output(_edit_block("src/x.ts", "const b = 2;", "const b = 3;")))
    assert ok and crew._analyst_files == [{"path": "src/x.ts", "content": "const a = 1;\nconst b = 3;\n"}]
    assert not crew._not_extracted


def test_guardrail_refuses_once_when_the_search_text_is_not_in_the_original():
    crew = new_crew()
    crew._base_readers = (lambda path: ("const a = 1;\n", None), lambda d: None)
    ok, message = crew._diagnostic_guardrail(output(_edit_block("src/x.ts", "inexistant", "x")))
    assert not ok and "introuvable" in message
    ok, _ = crew._diagnostic_guardrail(output(_edit_block("src/x.ts", "inexistant", "x")))
    assert ok and not crew._analyst_files and "src/x.ts" in crew._not_extracted


def test_unresolved_import_is_flagged_with_real_listing_semantics(tmp_path):
    from analyst_imports import find_import_problems
    (tmp_path / "src").mkdir()
    (tmp_path / "src/App.tsx").write_text("x")
    files = [{"path": "src/Main.tsx", "content": "import Header from './components/Header';\n"}]
    problems = find_import_problems(files, list_dir=lambda d: crew_workspace._list_local_dir(tmp_path, d))
    assert len(problems) == 1 and "./components/Header" in problems[0]


def test_guardrail_refuses_once_on_inconsistent_imports_then_accepts_with_a_note():
    crew = new_crew()
    crew._base_readers = (lambda path: (None, "ABSENT"), lambda d: None)
    bad = (
        "<<<FICHIER: src/App.tsx>>>\n```tsx\nimport { total } from './cart';\nexport default 1;\n```\n<<<FIN_FICHIER>>>\n"
        "<<<FICHIER: src/cart.ts>>>\n```ts\nexport const sum = 1;\n```\n<<<FIN_FICHIER>>>\n"
    )
    ok, message = crew._diagnostic_guardrail(output(bad))
    assert not ok and "imports" in message and "'total'" in message
    ok, result = crew._diagnostic_guardrail(output(bad))
    assert ok and "Incohérences entre fichiers" in result
    assert {f["path"] for f in crew._analyst_files} == {"src/App.tsx", "src/cart.ts"}


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
    assert not crew_guardrails._withdrawn_paths("Fichiers src/b.ts — NON réalisés : aucun", ["src/b.ts"])


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

    monkeypatch.setattr(crew_tools, "write_files_to_branch", lambda *a, **k: "ERREUR : non fast-forward")
    tool.run(commit_message="m")
    assert "commit refusé" in crew._write_rejections["src/a.ts"]

    def partial_success(owner, repo, branch, message, files, sink):
        sink["package.json"] = "ERREUR_SYNTAXE : JSON invalide"
        return "OK : 1 fichier(s) écrit(s)"

    monkeypatch.setattr(crew_tools, "write_files_to_branch", partial_success)
    tool.run(commit_message="m")
    assert crew._write_rejections == {"package.json": "ERREUR_SYNTAXE : JSON invalide"}


def test_only_successful_commits_are_cached_and_errors_explain_how_to_retry(monkeypatch):
    crew = new_crew(owner="o", repo="r")
    crew._analyst_files = [{"path": "src/a.ts", "content": "export {};\n"}]
    commit, qa = crew._build_commit_analyst_files_tool(), crew._build_qa_verify_tool()
    assert commit.cache_function(None, "OK : 1 fichier") is True
    assert commit.cache_function(None, "ERREUR : non fast-forward") is False
    assert qa.cache_function() is False
    monkeypatch.setattr(crew_tools, "write_files_to_branch", lambda *a, **k: "ERREUR : non fast-forward")
    assert "commit_message légèrement différent" in commit.run(commit_message="m")
    monkeypatch.setattr(crew_tools, "write_files_to_branch", lambda *a, **k: "OK : 1 fichier(s)")
    assert "légèrement différent" not in commit.run(commit_message="m (2e essai)")


def test_file_both_delivered_and_withdrawn_is_sent_back_then_delivered_content_wins():
    crew = new_crew()
    raw = output(
        "- src/App.tsx : ajout du bouton d'export qui était NON réalisé dans la version précédente\n"
        "<<<FICHIER: src/App.tsx>>>\nexport const App = 1;\n<<<FIN_FICHIER>>>\n"
    )
    ok, message = crew._diagnostic_guardrail(raw)
    assert not ok and "src/App.tsx" in message
    ok, _ = crew._diagnostic_guardrail(raw)
    # Ambiguïté non levée : exclu et signalé, jamais committé en silence ni perdu sans trace.
    assert ok and crew._analyst_files == []
    assert "ambiguïté" in crew._not_extracted["src/App.tsx"]


def test_clarified_retry_commits_the_delivered_file():
    crew = new_crew()
    crew._diagnostic_guardrail(output(
        "- src/App.tsx : ajout du bouton d'export qui était NON réalisé avant\n"
        "<<<FICHIER: src/App.tsx>>>\nexport const App = 1;\n<<<FIN_FICHIER>>>\n"
    ))
    ok, _ = crew._diagnostic_guardrail(output(
        "- src/App.tsx : ajout du bouton d'export\n"
        "<<<FICHIER: src/App.tsx>>>\nexport const App = 1;\n<<<FIN_FICHIER>>>\n"
    ))
    assert ok and [f["path"] for f in crew._analyst_files] == ["src/App.tsx"]


def test_file_withdrawn_in_the_same_response_is_not_committed():
    crew = new_crew()
    raw = output(
        "- src/App.tsx — NON réalisé (trop volumineux)\n"
        "<<<FICHIER: src/App.tsx>>>\nexport const partial = 1;\n<<<FIN_FICHIER>>>\n"
        "<<<FICHIER: src/b.ts>>>\nexport const b = 1;\n<<<FIN_FICHIER>>>\n"
    )
    crew._diagnostic_guardrail(raw)
    ok, out = crew._diagnostic_guardrail(raw)
    assert ok and [f["path"] for f in crew._analyst_files] == ["src/b.ts"]
    assert "src/App.tsx" in crew._not_extracted and "src/App.tsx" in out
