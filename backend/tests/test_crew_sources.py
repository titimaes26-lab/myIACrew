"""Lecture du code d'origine et espace de travail local : branche de travail, repli, confinement, dossiers."""

import pytest
import github_read
import github_client


cq = pytest.importorskip("crewquestion")
import crew_tools
import crew_workspace
from crew_support import output, new_crew, _edit_block, _fake_fetcher, _repo_crew


def test_base_source_uses_the_work_branch_when_it_exists(monkeypatch):
    crew = _repo_crew()
    made = {}

    def fake_fetcher(owner, repo, branch):
        made[branch] = True
        return _fake_fetcher(content=f"version de {branch}")

    monkeypatch.setattr(crew_tools, "make_file_fetcher", fake_fetcher)
    monkeypatch.setattr(crew_tools, "make_dir_lister", lambda owner, repo, branch: (lambda d: {branch}))
    assert crew._read_base_file("src/x.ts") == ("version de crewai/x", None)
    assert crew._list_base_dir("src") == {"crewai/x"} and "main" not in made


def test_base_source_falls_back_to_main_only_when_the_work_branch_does_not_exist(monkeypatch):
    crew = _repo_crew()
    monkeypatch.setattr(
        crew_tools, "make_file_fetcher",
        lambda owner, repo, branch: _fake_fetcher(error="ERREUR : branche introuvable", branch_missing=True)
        if branch == "crewai/x" else _fake_fetcher(content="version de main"),
    )
    monkeypatch.setattr(crew_tools, "make_dir_lister", lambda owner, repo, branch: (lambda d: {branch}))
    assert crew._read_base_file("src/x.ts") == ("version de main", None)
    assert crew._list_base_dir("src") == {"main"}


def test_transient_error_on_the_work_branch_never_falls_back_to_main(monkeypatch):
    crew = _repo_crew()
    monkeypatch.setattr(
        crew_tools, "make_file_fetcher",
        lambda owner, repo, branch: _fake_fetcher(error="ERREUR_GITHUB : rate limit exceeded")
        if branch == "crewai/x" else _fake_fetcher(content="ANCIENNE version de main"),
    )
    monkeypatch.setattr(crew_tools, "make_dir_lister", lambda owner, repo, branch: (lambda d: None))
    assert crew._read_base_file("src/x.ts") == (None, "ERREUR_GITHUB : rate limit exceeded")
    ok, message = crew._diagnostic_guardrail(output(_edit_block("src/x.ts", "a", "b")))
    assert not ok and "modification impossible" in message


def test_local_dir_listing_distinguishes_absent_directory_from_unknown(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/App.tsx").write_text("x")
    assert crew_workspace._list_local_dir(tmp_path, "src") == {"App.tsx"}
    assert crew_workspace._list_local_dir(tmp_path, "src/components/Header") == set()
    assert crew_workspace._list_local_dir(tmp_path, "src/App.tsx") == set()
    assert crew_workspace._list_local_dir(tmp_path, "../dehors") is None


def test_github_dir_lister_maps_404_to_empty_and_other_errors_to_unknown(monkeypatch):
    from github import GithubException

    class Item:
        def __init__(self, name):
            self.name = name

    class FakeRepo:
        def get_contents(self, directory, ref=None):
            if directory == "src":
                return [Item("App.tsx")]
            if directory == "src/Header":
                raise GithubException(404, {}, {})
            if directory == "file.txt":
                return Item("file.txt")
            raise GithubException(403, {}, {})

    monkeypatch.setattr(github_client, "_get_repo", lambda owner, repo: FakeRepo())
    list_dir = github_read.make_dir_lister("o", "r", "main")
    assert list_dir("src") == {"App.tsx"}
    assert list_dir("src/Header") == set() and list_dir("file.txt") == set()
    assert list_dir("quota") is None


def test_local_writes_are_confined_to_workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/App.tsx").write_text("old\n")
    rejections = {}
    message = crew_workspace._write_files_locally(tmp_path, [
        {"path": "src/App.tsx", "content": "export {};\n"},
        {"path": "main.py", "content": "x = 1\n"},
        {"path": "../escape.py", "content": "x = 1\n"},
    ], rejections)
    assert (tmp_path / "src/App.tsx").read_text() == "export {};\n"
    assert (tmp_path / "main.py").exists()  # dans l'espace de travail, jamais le backend
    assert set(rejections) == {"../escape.py"} and "REJETÉS (1)" in message
    assert crew_workspace._read_local_file(tmp_path, "src/App.tsx") == ("export {};\n", None)


def test_each_conversation_has_its_own_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(crew_workspace, "LOCAL_WORKSPACE_DIR", tmp_path)
    a, b = crew_workspace._conversation_workspace("1"), crew_workspace._conversation_workspace("2")
    assert a != b and tmp_path in a.parents and tmp_path in b.parents


def test_local_mode_commit_read_and_qa_share_the_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(crew_workspace, "LOCAL_WORKSPACE_DIR", tmp_path)
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
    monkeypatch.setattr(crew_tools, "write_files_to_branch", lambda *a, **k: calls.append(a[:3]) or "OK : 1")
    monkeypatch.setattr(crew_workspace, "_write_files_locally", lambda *a, **k: pytest.fail("écriture locale en mode GitHub"))
    crew._build_commit_analyst_files_tool().run(commit_message="m")
    assert calls == [("o", "r", "feature/x")]
    assert "github_read_file" in crew._build_local_read_tool().run(file_path="src/a.ts")


def test_local_workspace_is_per_conversation_even_without_branch(monkeypatch, tmp_path):
    monkeypatch.setattr(crew_workspace, "LOCAL_WORKSPACE_DIR", tmp_path)
    a, b = cq.AppDevelopmentCrew(), cq.AppDevelopmentCrew()
    a._reset_execution_state({"work_branch": "", "conversation_id": "1"})
    b._reset_execution_state({"work_branch": "", "conversation_id": "2"})
    assert a._workspace != b._workspace


def test_local_reads_normalize_dot_slash_but_stay_confined(monkeypatch, tmp_path):
    monkeypatch.setattr(crew_workspace, "LOCAL_WORKSPACE_DIR", tmp_path)
    crew = new_crew()
    (crew._workspace / "src").mkdir(parents=True)
    (crew._workspace / "src/a.ts").write_text("export {};\n")
    assert crew_workspace._read_local_file(crew._workspace, "./src/a.ts") == ("export {};\n", None)
    assert crew._build_local_read_tool().run(file_path="./src/a.ts") == "export {};\n"
    assert crew_workspace._read_local_file(crew._workspace, "../x")[0] is None


def test_local_mode_without_extracted_files_never_suggests_github():
    message = new_crew()._build_commit_analyst_files_tool().run(commit_message="m")
    assert "github_write_files" not in message and "espace de travail local" in message
    github_message = new_crew(owner="o", repo="r")._build_commit_analyst_files_tool().run(commit_message="m")
    assert "github_write_files" in github_message


def test_local_write_with_every_file_rejected_is_an_error(tmp_path):
    message = crew_workspace._write_files_locally(tmp_path, [{"path": "a.json", "content": "{ invalide"}], {})
    assert message.startswith("ERREUR")
