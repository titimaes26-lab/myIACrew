"""Écriture d'un lot de fichiers en un seul commit : contrôles avant envoi, collision de dossier, arbre tronqué, course non fast-forward."""
"""Cas limites des modules github_* : 404, dossiers, occurrences, branche absente, syntaxe, cache de lecture."""

import pytest


pytest.importorskip("github_write")
import github_client
import github_batch
import github_edit_failures
from github_edge_support import BRANCH, _http  # noqa: E402


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
