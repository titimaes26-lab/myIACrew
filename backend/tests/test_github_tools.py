import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

gt = pytest.importorskip("github_tools")
from github import GithubException  # noqa: E402


class FakeContentFile:
    """Reproduit le comportement de PyGithub : `decoded_content` lève au-delà de 1 Mo."""

    def __init__(self, decoded=None, sha="deadbeef"):
        self._decoded = decoded
        self.sha = sha

    @property
    def decoded_content(self):
        if self._decoded is None:
            raise AssertionError("contenu trop volumineux (simulé, comme le fait PyGithub)")
        return self._decoded


class FakeBlob:
    def __init__(self, content: str):
        self.content = content


class FakeRepo:
    def __init__(self, blob_content: bytes | None = None, blob_error: Exception | None = None):
        self._blob_content = blob_content
        self._blob_error = blob_error

    def get_git_blob(self, sha):
        if self._blob_error:
            raise self._blob_error
        import base64

        return FakeBlob(base64.b64encode(self._blob_content).decode("ascii"))


def test_decode_content_file_uses_direct_content_when_available():
    content, error = gt._decode_content_file(FakeRepo(), FakeContentFile(decoded=b"hello\n"), "a.txt")
    assert content == "hello\n" and error is None


def test_decode_content_file_falls_back_to_blob_above_1mb():
    repo = FakeRepo(blob_content="grand fichier\n".encode("utf-8"))
    content, error = gt._decode_content_file(repo, FakeContentFile(decoded=None), "big.txt")
    assert content == "grand fichier\n" and error is None


def test_decode_content_file_reports_non_utf8_as_present_unreadable():
    from analyst_output import PRESENT_UNREADABLE

    bad = FakeContentFile(decoded=b"\xff\xfe\x00\x01")
    content, error = gt._decode_content_file(FakeRepo(), bad, "bin.dat")
    assert content is None and error.startswith(PRESENT_UNREADABLE)


def test_decode_content_file_blob_fetch_error_is_not_reported_as_unreadable():
    from analyst_output import PRESENT_UNREADABLE

    repo = FakeRepo(blob_error=RuntimeError("panne réseau"))
    content, error = gt._decode_content_file(repo, FakeContentFile(decoded=None), "big.txt")
    assert content is None and not error.startswith(PRESENT_UNREADABLE)


def test_github_read_file_uses_blob_fallback_for_large_files(monkeypatch):
    repo = FakeRepo(blob_content="grand fichier\n".encode("utf-8"))
    monkeypatch.setattr(gt, "_get_repo", lambda owner, r: repo)
    monkeypatch.setattr(repo, "get_contents", lambda path, ref: FakeContentFile(decoded=None), raising=False)
    result = gt.github_read_file.run(owner="o", repo="r", path="big.txt", branch="main")
    assert result == "grand fichier\n"


def test_github_read_file_does_not_double_prefix_blob_fallback_errors(monkeypatch):
    repo = FakeRepo(blob_error=GithubException(403, {"message": "API rate limit exceeded"}, None))
    monkeypatch.setattr(gt, "_get_repo", lambda owner, r: repo)
    monkeypatch.setattr(repo, "get_contents", lambda path, ref: FakeContentFile(decoded=None), raising=False)
    result = gt.github_read_file.run(owner="o", repo="r", path="big.txt", branch="main")
    assert result.count("ERREUR") == 1 and "ERREUR : ERREUR" not in result


# --- Cache de lecture par exécution et aperçu du repo ------------------------------------------------

class _Entry:
    def __init__(self, path, kind="file"):
        self.path, self.type = path, kind


class _CountingRepo:
    """Faux repo : compte les lectures et sert un petit projet. `files` : {(branche, chemin): bytes}."""

    def __init__(self, files, dirs):
        self.files, self.dirs, self.reads = files, dirs, 0

    def get_contents(self, path, ref="main"):
        self.reads += 1
        if (ref, path) in self.dirs:
            return [_Entry(p, kind) for p, kind in self.dirs[(ref, path)]]
        if (ref, path) in self.files:
            return FakeContentFile(self.files[(ref, path)])
        raise GithubException(404, {"message": "Not Found"}, None)


@pytest.fixture()
def counting(monkeypatch):
    repo = _CountingRepo(
        files={("main", "package.json"): b'{"name": "app"}', ("main", "tsconfig.json"): b'{"strict": true}'},
        dirs={("main", ""): [("package.json", "file"), ("tsconfig.json", "file"), ("src", "dir")],
              ("main", "src"): [("src/App.tsx", "file")]},
    )
    created = []

    class FakeGithub:
        def __init__(self, auth=None):
            pass

        def get_repo(self, name):
            created.append(name)
            return repo

    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(gt, "Github", FakeGithub)
    repo.created = created
    return repo


def test_without_a_cache_every_read_goes_to_github(counting):
    gt.github_read_file.func("o", "r", "package.json", "main")
    gt.github_read_file.func("o", "r", "package.json", "main")
    assert counting.reads == 2 and len(counting.created) == 2


def test_cache_serves_repeated_reads_and_builds_the_repo_once(counting):
    with gt.track_read_cache():
        first = gt.github_read_file.func("o", "r", "package.json", "main")
        second = gt.github_read_file.func("o", "r", "package.json", "main")
        gt.github_list_directory.func("o", "r", "", "main")
        gt.github_list_directory.func("o", "r", "", "main")
    assert first == second == '{"name": "app"}'
    assert counting.reads == 2          # un fichier + un dossier, une fois chacun
    assert len(counting.created) == 1   # get_repo une seule fois pour toute l'exécution


def test_cache_never_keeps_errors_and_is_isolated_per_execution(counting):
    with gt.track_read_cache():
        assert gt.github_read_file.func("o", "r", "absent.ts", "main").startswith("ERREUR_FICHIER_INEXISTANT")
        gt.github_read_file.func("o", "r", "absent.ts", "main")
        assert counting.reads == 2      # l'erreur n'est pas mémorisée
        gt.github_read_file.func("o", "r", "package.json", "main")
    with gt.track_read_cache():
        gt.github_read_file.func("o", "r", "package.json", "main")
    assert counting.reads == 4          # un nouveau contexte repart d'un cache vide


def test_a_write_invalidates_only_the_written_branch(counting):
    counting.files[("work", "package.json")] = b'{"name": "work"}'
    with gt.track_read_cache():
        gt.github_read_file.func("o", "r", "package.json", "main")
        gt.github_read_file.func("o", "r", "package.json", "work")
        reads = counting.reads
        gt.invalidate_read_cache("o", "r", "work")
        gt.github_read_file.func("o", "r", "package.json", "main")   # toujours en cache
        assert counting.reads == reads
        gt.github_read_file.func("o", "r", "package.json", "work")   # relu après l'écriture
        assert counting.reads == reads + 1


def test_delivery_checks_never_read_from_or_fill_the_file_cache(counting):
    with gt.track_read_cache():
        gt.make_file_fetcher("o", "r", "main")("package.json")
        gt.make_dir_lister("o", "r", "main")
        cache = gt._read_cache.get()
        assert cache["files"] == {} and cache["dirs"] == {}


def test_repo_snapshot_lists_the_root_reads_the_config_files_and_src(counting):
    snapshot = gt.build_repo_snapshot("o", "r", "main")
    assert "branche lue : main" in snapshot
    assert "## Racine" in snapshot and "## package.json" in snapshot and '{"name": "app"}' in snapshot
    assert "## tsconfig.json" in snapshot and "## src" in snapshot and "src/App.tsx" in snapshot


def test_repo_snapshot_skips_missing_files_truncates_and_degrades_to_empty(counting):
    del counting.files[("main", "tsconfig.json")]
    counting.dirs[("main", "")] = [("package.json", "file"), ("src", "dir")]
    counting.files[("main", "package.json")] = b"x" * 7000
    snapshot = gt.build_repo_snapshot("o", "r", "main")
    assert "## tsconfig.json" not in snapshot and "[… tronqué]" in snapshot and "x" * 6001 not in snapshot
    assert gt.build_repo_snapshot("o", "r", "inconnue") == ""   # racine illisible : l'agent lira lui-même


def test_repo_snapshot_caps_long_listings(counting):
    counting.dirs[("main", "")] = [("package.json", "file"), ("src", "dir")]
    counting.dirs[("main", "src")] = [(f"src/f{i}.ts", "file") for i in range(400)]
    snapshot = gt.build_repo_snapshot("o", "r", "main")
    assert "src/f149.ts" in snapshot and "src/f150.ts" not in snapshot
    assert "… 250 autres entrées" in snapshot


def test_repo_snapshot_of_an_empty_repository_says_so(counting):
    counting.dirs[("main", "")] = []
    snapshot = gt.build_repo_snapshot("o", "r", "main")
    assert "dépôt vide" in snapshot and "branche lue : main" in snapshot


def test_read_cache_counts_hits_and_reads_and_logs_them(counting, capsys):
    with gt.track_read_cache():
        gt.github_read_file.func("o", "r", "package.json", "main")
        gt.github_read_file.func("o", "r", "package.json", "main")
        stats = dict(gt._read_cache.get()["stats"])
    assert stats == {"hits": 1, "reads": 1}
    assert "[CACHE LECTURE] hits=1 lectures=1" in capsys.readouterr().out


def test_read_cache_logs_nothing_when_no_read_happened(capsys):
    with gt.track_read_cache():
        pass
    assert "CACHE LECTURE" not in capsys.readouterr().out
