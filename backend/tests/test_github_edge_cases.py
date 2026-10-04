"""Cas limites des modules github_* : 404, dossiers, occurrences, branche absente, syntaxe, cache de lecture."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("github_write")
import github_client  # noqa: E402
import github_delivery  # noqa: E402
import github_edit  # noqa: E402
import github_guards  # noqa: E402
import github_read  # noqa: E402
import github_snapshot  # noqa: E402
import github_write  # noqa: E402
from analyst_output import PRESENT_UNREADABLE  # noqa: E402
from github import GithubException  # noqa: E402

BRANCH = "crewai/a"


class _Content:
    def __init__(self, text: bytes = b"", sha="sha1"):
        self.decoded_content, self.sha = text, sha


class _Item:
    def __init__(self, name, path=None, kind="file"):
        self.name, self.path, self.type = name, path or name, kind


class _Repo:
    """Faux dépôt : `contents` = {chemin: objet | liste | exception} ; enregistre les écritures."""

    def __init__(self, contents=None):
        self.contents, self.calls, self.created, self.updated = contents or {}, [], [], []

    def get_contents(self, path, ref="main"):
        self.calls.append((path, ref))
        value = self.contents.get(path, GithubException(404, {"message": "Not Found"}, None))
        if isinstance(value, Exception):
            raise value
        return value

    def create_file(self, path, message, content, branch):
        self.created.append((path, message, content, branch))

    def update_file(self, path, message, content, sha, branch):
        self.updated.append((path, message, content, sha, branch))


@pytest.fixture
def repo(monkeypatch):
    fake = _Repo()
    monkeypatch.setattr(github_client, "_get_repo", lambda owner, name: fake)
    return fake


def _http(status, message="x"):
    return GithubException(status, {"message": message}, None)


# --- lecteur de dossiers de vérification ------------------------------------------------------------

def test_the_directory_lister_reads_the_root_names_and_distinguishes_missing_from_unknown(repo):
    repo.contents = {"": [_Item("a.ts"), _Item("src", kind="dir")], "vide": [], "fichier": _Content(b"x"),
                     "interdit": _http(403), "absent": _http(404)}
    lister = github_read.make_dir_lister("o", "r", "main")
    assert lister("") == {"a.ts", "src"} and lister(None) == {"a.ts", "src"}   # la racine se lit avec ""
    assert repo.calls[0] == ("", "main")
    assert lister("vide") == set()
    assert lister("fichier") == set()          # un fichier n'a pas d'entrées
    assert lister("absent") == set()           # 404 : le dossier n'existe pas, ce n'est pas « inconnu »
    assert lister("interdit") is None          # autre erreur : inconnu


def test_the_directory_lister_is_unknown_when_the_repository_is_unreachable(monkeypatch):
    def boom(owner, name):
        raise RuntimeError("pas de jeton")
    monkeypatch.setattr(github_client, "_get_repo", boom)
    assert github_read.make_dir_lister("o", "r", "main")("src") is None


# --- outils de lecture --------------------------------------------------------------------------------

def test_list_directory_reports_missing_folders_files_empty_folders_and_api_errors(repo):
    repo.contents = {"src": [_Item("a.ts", "src/a.ts"), _Item("lib", "src/lib", "dir")], "vide": [],
                     "fichier": _Content(b"x"), "interdit": _http(403, "interdit")}
    assert github_read.github_list_directory.run(owner="o", repo="r", path="src", branch="main") == "file: src/a.ts\ndir: src/lib"
    assert github_read.github_list_directory.run(owner="o", repo="r", path="vide", branch="main") == "INFO : dossier vide."
    assert "est un fichier" in github_read.github_list_directory.run(owner="o", repo="r", path="fichier", branch="main")
    missing = github_read.github_list_directory.run(owner="o", repo="r", path="nope", branch="dev")
    assert missing.startswith("ERREUR_DOSSIER_INEXISTANT") and "'nope'" in missing and "'dev'" in missing
    assert github_read.github_list_directory.run(owner="o", repo="r", path="interdit", branch="main").startswith("ERREUR_GITHUB")


def test_the_read_cache_shares_the_root_between_an_empty_and_a_missing_path(repo):
    repo.contents = {"": [_Item("a.ts")]}
    with github_client.track_read_cache():
        github_read.list_directory_cached("o", "r", "", "main")
        github_read.list_directory_cached("o", "r", None, "main")
    assert [c for c in repo.calls if c[0] == ""] == [("", "main")]   # une seule lecture réseau


def test_reading_an_unreadable_file_returns_one_error_prefix_with_the_reason(repo):
    repo.contents = {"bin.dat": _Content(b"\xff\xfe\x00")}
    result = github_read.github_read_file.run(owner="o", repo="r", path="bin.dat", branch="main")
    assert result.startswith(f"ERREUR : {PRESENT_UNREADABLE}") and "bin.dat" in result and "ERREUR : ERREUR" not in result


# --- écriture ---------------------------------------------------------------------------------------------

def test_write_file_creates_a_missing_file_and_updates_an_existing_one(repo):
    repo.contents = {"src/old.ts": _Content(b"ancien", sha="s42")}
    with github_guards.track_write_scope(BRANCH):
        created = github_write.github_write_file.run(owner="o", repo="r", path="src/new.ts", content="export const a = 1;\n", commit_message="m", branch=BRANCH)
        updated = github_write.github_write_file.run(owner="o", repo="r", path="src/old.ts", content="export const b = 2;\n", commit_message="m2", branch=BRANCH)
    assert created.startswith("OK : fichier 'src/new.ts' créé") and repo.created == [("src/new.ts", "m", "export const a = 1;\n", BRANCH)]
    assert updated.startswith("OK") and repo.updated == [("src/old.ts", "m2", "export const b = 2;\n", "s42", BRANCH)]


def test_write_file_does_not_create_anything_on_another_api_error_or_on_a_folder(repo):
    repo.contents = {"src/boom.ts": _http(500, "panne"), "src": [_Item("a.ts")]}
    with github_guards.track_write_scope(BRANCH):
        failed = github_write.github_write_file.run(owner="o", repo="r", path="src/boom.ts", content="export const a = 1;\n", commit_message="m", branch=BRANCH)
        folder = github_write.github_write_file.run(owner="o", repo="r", path="src", content="export const a = 1;\n", commit_message="m", branch=BRANCH)
    assert failed.startswith("ERREUR_GITHUB") and "dossier" in folder
    assert repo.created == [] and repo.updated == []


# --- modification ciblée -------------------------------------------------------------------------------

def _edit(old, new="Z"):
    with github_guards.track_write_scope(BRANCH), github_edit_failures_scope():
        return github_edit.github_edit_file.run(owner="o", repo="r", path="src/a.ts", branch=BRANCH, old_string=old, new_string=new, commit_message="m")


def github_edit_failures_scope():
    import github_edit_failures
    return github_edit_failures.track_edit_failures()


def test_edit_replaces_only_a_unique_occurrence(repo):
    repo.contents = {"src/a.ts": _Content(b"const a = 1;\nconst b = 2;\n", sha="s9")}
    assert _edit("const b = 2;").startswith("OK")
    assert repo.updated == [("src/a.ts", "m", "const a = 1;\nZ\n", "s9", BRANCH)]


def test_edit_refuses_a_missing_or_ambiguous_block_without_writing(repo):
    repo.contents = {"src/a.ts": _Content(b"x = 1;\nx = 1;\n")}
    absent, ambiguous = _edit("introuvable"), _edit("x = 1;")
    assert "introuvable tel quel" in absent and "apparaît 2 fois" in ambiguous
    assert repo.updated == []


def test_edit_reports_a_missing_file_and_a_folder_and_passes_other_api_errors_through(repo):
    repo.contents = {"src/a.ts": [_Item("b.ts")]}
    assert "'src/a.ts' est un dossier" in _edit("a")
    repo.contents = {"src/a.ts": _http(403, "interdit")}
    assert _edit("a").startswith("ERREUR_GITHUB") and "interdit" in _edit("a")
    repo.contents = {}
    missing = _edit("a")
    assert missing.startswith("ERREUR_FICHIER_INEXISTANT") and "github_write_file" in missing


# --- branche de tête, vérification, syntaxe ---------------------------------------------------------------

class _Branch:
    def __init__(self, sha):
        self.commit = type("C", (), {"sha": sha})()


def test_head_sha_is_none_for_a_missing_branch_and_raises_when_verification_is_impossible(monkeypatch):
    class _R:
        def __init__(self, result):
            self.result = result

        def get_branch(self, name):
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

    def with_result(result):
        monkeypatch.setattr(github_client, "_get_repo", lambda o, r: _R(result))

    with_result(_Branch("abc"))
    assert github_snapshot.get_branch_head_sha("o", "r", "b") == "abc"
    with_result(_http(404))
    assert github_snapshot.get_branch_head_sha("o", "r", "b") is None
    with_result(_http(403, "limite"))
    with pytest.raises(github_snapshot.GitHubVerificationUnavailable, match="limite"):
        github_snapshot.get_branch_head_sha("o", "r", "b")
    with_result(RuntimeError("réseau"))
    with pytest.raises(github_snapshot.GitHubVerificationUnavailable, match="réseau"):
        github_snapshot.get_branch_head_sha("o", "r", "b")


def test_a_missing_work_branch_is_reported_as_a_probable_access_problem(monkeypatch):
    monkeypatch.setattr(github_snapshot, "get_branch_head_sha", lambda o, r, b: None)
    pr, issue = github_delivery.verify_github_delivery("o", "r", "crewai/a", "main", "oldsha")
    assert pr is None and issue.likely_access_problem is True and "aucune branche 'crewai/a'" in issue.message


def test_an_unverifiable_branch_is_retried_once_then_reported(monkeypatch):
    calls = []

    def unavailable(owner, name, branch):
        calls.append(branch)
        raise github_snapshot.GitHubVerificationUnavailable("api HS")
    monkeypatch.setattr(github_snapshot, "get_branch_head_sha", unavailable)
    monkeypatch.setattr(github_delivery.time, "sleep", lambda s: None)
    pr, issue = github_delivery.verify_github_delivery("o", "r", "crewai/a", "main", None)
    assert len(calls) == 2 and pr is None and "impossible de vérifier la branche" in issue.message and issue.likely_access_problem


def test_only_a_syntax_error_report_rejects_the_content(monkeypatch):
    for report, rejected in (("ERREUR_SYNTAXE : ligne 3", True), ("OK : syntaxe valide", False), ("note : ERREUR_SYNTAXE plus loin", False)):
        monkeypatch.setattr(github_guards, "check_syntax_content", lambda content, path, r=report: r)
        assert (github_guards._reject_invalid_syntax("a.ts", "x") == report) is rejected
