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


# --- lot de fichiers en un seul commit ------------------------------------------------------------------

import github_batch  # noqa: E402
import github_edit_failures  # noqa: E402


class _GitRepo:
    """Faux dépôt git bas niveau : enregistre l'arbre construit, le commit créé et l'avance de la référence."""

    def __init__(self, dirs=(), truncated=False, edit_error=None):
        self.dirs, self.truncated, self.edit_error = set(dirs), truncated, edit_error
        self.trees, self.commits, self.moved_to, self.blobs = [], [], [], []

    def get_git_ref(self, name):
        repo = self

        class Ref:
            object = type("O", (), {"sha": "head0"})()

            def edit(self, sha):
                if repo.edit_error:
                    raise repo.edit_error
                repo.moved_to.append(sha)
        return Ref()

    def get_git_commit(self, sha):
        return type("C", (), {"tree": type("T", (), {"sha": "tree0"})()})()

    def get_git_tree(self, sha, recursive=False):
        items = [type("I", (), {"path": p, "type": "tree"})() for p in sorted(self.dirs)] + [type("I", (), {"path": "a.ts", "type": "blob"})()]
        return type("Tr", (), {"truncated": self.truncated, "tree": items})()

    def create_git_blob(self, content, encoding):
        self.blobs.append(content)
        return type("B", (), {"sha": f"blob{len(self.blobs)}"})()

    def create_git_tree(self, elements, base_tree):
        self.trees.append(elements)
        return type("N", (), {"sha": "tree1"})()

    def create_git_commit(self, message, tree, parents):
        self.commits.append((message, tree.sha, len(parents)))
        return type("K", (), {"sha": "abcdef1234"})()


@pytest.fixture
def git(monkeypatch):
    fake = _GitRepo()
    monkeypatch.setattr(github_client, "_get_repo", lambda owner, name: fake)
    return fake


GOOD = {"path": "src/a.ts", "content": "export const a = 1;\n"}


def _write(files, sink=None, branch=BRANCH):
    return github_batch.write_files_to_branch("o", "r", branch, "feat: x", files, sink)


def test_a_batch_commits_every_valid_file_once_and_moves_the_branch(git):
    other = {"path": "src/b.ts", "content": "export const b = 2;\n"}
    result = _write([GOOD, other])
    assert result == f"OK : 2 fichier(s) écrit(s) en un seul commit (abcdef12) sur la branche '{BRANCH}'."
    assert git.blobs == [GOOD["content"], other["content"]] and git.moved_to == ["abcdef1234"]
    assert git.commits == [("feat: x", "tree1", 1)]
    assert [(e._identity["path"], e._identity["mode"], e._identity["type"]) for e in git.trees[0]] == [("src/a.ts", "100644", "blob"), ("src/b.ts", "100644", "blob")]


@pytest.mark.parametrize("files, fragment", [
    ([], "non vide"), ("pas une liste", "non vide"),
    ([{"path": "a.ts"}], '"path" (str) et "content" (str)'), (["a.ts"], '"path" (str) et "content" (str)'),
    ([GOOD, dict(GOOD)], "apparaît plusieurs fois"),
])
def test_a_malformed_batch_is_refused_before_any_network_call(git, files, fragment):
    assert fragment in _write(files)
    assert git.blobs == [] and git.moved_to == []


def test_a_protected_branch_is_refused_before_any_network_call(git):
    assert "interdite" in _write([GOOD], branch="main") and git.blobs == []


def test_a_path_matching_an_existing_folder_is_refused_without_committing(git):
    git.dirs = {"src/a.ts"}
    result = _write([GOOD])
    assert "DOSSIER existant" in result and "src/a.ts" in result
    assert git.blobs == [] and git.moved_to == []


def test_a_truncated_tree_refuses_the_batch_rather_than_risking_a_silent_overwrite(git):
    git.truncated = True
    assert "trop volumineuse" in _write([GOOD]) and git.moved_to == []


def test_rejected_files_are_reported_but_the_valid_ones_are_still_committed(git):
    broken = {"path": "src/broken.json", "content": '{"a": '}
    sink = {}
    result = _write([GOOD, broken], sink)
    assert result.startswith("OK : 1 fichier(s) écrit(s)") and "REJETÉS (1)" in result and "src/broken.json" in result
    assert git.blobs == [GOOD["content"]] and list(sink) == ["src/broken.json"]


def test_a_batch_where_everything_is_rejected_writes_nothing(git):
    sink = {}
    result = _write([{"path": "src/broken.json", "content": '{"a": '}], sink)
    assert "TOUS été refusés" in result and git.blobs == [] and "src/broken.json" in sink


def test_a_successful_batch_clears_the_edit_failure_count_of_each_file(git):
    with github_edit_failures.track_edit_failures():
        github_edit_failures._record_edit_failure("o", "r", "src/a.ts", BRANCH, "raison.", "relis.")
        _write([GOOD])
        assert "ÉCHEC RÉPÉTÉ" not in github_edit_failures._record_edit_failure("o", "r", "src/a.ts", BRANCH, "raison.", "relis.")


def test_a_non_fast_forward_race_is_explained_as_safe_to_retry_while_other_errors_pass_through(git):
    git.edit_error = _http(422, "Update is not a fast forward")
    raced = _write([GOOD])
    assert "Retente ce même appel tel quel" in raced and "aucun fichier de CET appel n'a été perdu" in raced
    git.edit_error = _http(422, "Reference already exists, fast")   # « fast » seul ne suffit pas
    assert "Retente" not in _write([GOOD])
    git.edit_error = _http(500, "panne forward")
    assert _write([GOOD]).startswith("ERREUR_GITHUB") and "Retente" not in _write([GOOD])
