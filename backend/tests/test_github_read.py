"""Lecture GitHub : décodage des contenus, repli sur le blob au-delà de 1 Mo, cache de lecture, aperçu du dépôt."""

import pytest


pytest.importorskip("github_write")  # saute le fichier si PyGithub/crewai ne sont pas installés
import github_snapshot
import github_read
import github_client
from github import GithubException
from github_support import FakeContentFile, FakeRepo  # noqa: E402


def test_decode_content_file_uses_direct_content_when_available():
    content, error = github_read._decode_content_file(FakeRepo(), FakeContentFile(decoded=b"hello\n"), "a.txt")
    assert content == "hello\n" and error is None


def test_decode_content_file_falls_back_to_blob_above_1mb():
    repo = FakeRepo(blob_content="grand fichier\n".encode("utf-8"))
    content, error = github_read._decode_content_file(repo, FakeContentFile(decoded=None), "big.txt")
    assert content == "grand fichier\n" and error is None


def test_decode_content_file_reports_non_utf8_as_present_unreadable():
    from analyst_blocks import PRESENT_UNREADABLE

    bad = FakeContentFile(decoded=b"\xff\xfe\x00\x01")
    content, error = github_read._decode_content_file(FakeRepo(), bad, "bin.dat")
    assert content is None and error.startswith(PRESENT_UNREADABLE)


def test_decode_content_file_blob_fetch_error_is_not_reported_as_unreadable():
    from analyst_blocks import PRESENT_UNREADABLE

    repo = FakeRepo(blob_error=RuntimeError("panne réseau"))
    content, error = github_read._decode_content_file(repo, FakeContentFile(decoded=None), "big.txt")
    assert content is None and not error.startswith(PRESENT_UNREADABLE)


def test_github_read_file_uses_blob_fallback_for_large_files(monkeypatch):
    repo = FakeRepo(blob_content="grand fichier\n".encode("utf-8"))
    monkeypatch.setattr(github_client, "_get_repo", lambda owner, r: repo)
    monkeypatch.setattr(repo, "get_contents", lambda path, ref: FakeContentFile(decoded=None), raising=False)
    result = github_read.github_read_file.run(owner="o", repo="r", path="big.txt", branch="main")
    assert result == "grand fichier\n"


def test_github_read_file_does_not_double_prefix_blob_fallback_errors(monkeypatch):
    repo = FakeRepo(blob_error=GithubException(403, {"message": "API rate limit exceeded"}, None))
    monkeypatch.setattr(github_client, "_get_repo", lambda owner, r: repo)
    monkeypatch.setattr(repo, "get_contents", lambda path, ref: FakeContentFile(decoded=None), raising=False)
    result = github_read.github_read_file.run(owner="o", repo="r", path="big.txt", branch="main")
    assert result.count("ERREUR") == 1 and "ERREUR : ERREUR" not in result


def test_without_a_cache_every_read_goes_to_github(counting):
    github_read.github_read_file.func("o", "r", "package.json", "main")
    github_read.github_read_file.func("o", "r", "package.json", "main")
    assert counting.reads == 2 and len(counting.created) == 2


def test_cache_serves_repeated_reads_and_builds_the_repo_once(counting):
    with github_client.track_read_cache():
        first = github_read.github_read_file.func("o", "r", "package.json", "main")
        second = github_read.github_read_file.func("o", "r", "package.json", "main")
        github_read.github_list_directory.func("o", "r", "", "main")
        github_read.github_list_directory.func("o", "r", "", "main")
    assert first == second == '{"name": "app"}'
    assert counting.reads == 2          # un fichier + un dossier, une fois chacun
    assert len(counting.created) == 1   # get_repo une seule fois pour toute l'exécution


def test_cache_never_keeps_errors_and_is_isolated_per_execution(counting):
    with github_client.track_read_cache():
        assert github_read.github_read_file.func("o", "r", "absent.ts", "main").startswith("ERREUR_FICHIER_INEXISTANT")
        github_read.github_read_file.func("o", "r", "absent.ts", "main")
        assert counting.reads == 2      # l'erreur n'est pas mémorisée
        github_read.github_read_file.func("o", "r", "package.json", "main")
    with github_client.track_read_cache():
        github_read.github_read_file.func("o", "r", "package.json", "main")
    assert counting.reads == 4          # un nouveau contexte repart d'un cache vide


def test_a_write_invalidates_only_the_written_branch(counting):
    counting.files[("work", "package.json")] = b'{"name": "work"}'
    with github_client.track_read_cache():
        github_read.github_read_file.func("o", "r", "package.json", "main")
        github_read.github_read_file.func("o", "r", "package.json", "work")
        reads = counting.reads
        github_client.invalidate_read_cache("o", "r", "work")
        github_read.github_read_file.func("o", "r", "package.json", "main")   # toujours en cache
        assert counting.reads == reads
        github_read.github_read_file.func("o", "r", "package.json", "work")   # relu après l'écriture
        assert counting.reads == reads + 1


def test_delivery_checks_never_read_from_or_fill_the_file_cache(counting):
    with github_client.track_read_cache():
        github_read.make_file_fetcher("o", "r", "main")("package.json")
        github_read.make_dir_lister("o", "r", "main")
        cache = github_client._read_cache.get()
        assert cache["files"] == {} and cache["dirs"] == {}


def test_repo_snapshot_lists_the_root_reads_the_config_files_and_src(counting):
    snapshot = github_snapshot.build_repo_snapshot("o", "r", "main")
    assert "branche lue : main" in snapshot
    assert "## Racine" in snapshot and "## Résumé du projet" in snapshot and "- Nom : app" in snapshot
    assert "mode strict activé" in snapshot and "## src" in snapshot and "src/App.tsx" in snapshot
    assert '{"name": "app"}' not in snapshot   # le JSON brut est remplacé par son résumé


def test_repo_snapshot_skips_missing_files_truncates_and_degrades_to_empty(counting):
    del counting.files[("main", "tsconfig.json")]
    counting.dirs[("main", "")] = [("package.json", "file"), ("src", "dir")]
    counting.files[("main", "package.json")] = b"x" * 7000
    snapshot = github_snapshot.build_repo_snapshot("o", "r", "main")
    assert "## tsconfig.json" not in snapshot and "[… tronqué]" in snapshot and "x" * 6001 not in snapshot
    assert github_snapshot.build_repo_snapshot("o", "r", "inconnue") == ""   # racine illisible : l'agent lira lui-même


def test_repo_snapshot_caps_long_listings(counting):
    counting.dirs[("main", "")] = [("package.json", "file"), ("src", "dir")]
    counting.dirs[("main", "src")] = [(f"src/f{i}.ts", "file") for i in range(400)]
    snapshot = github_snapshot.build_repo_snapshot("o", "r", "main")
    assert "src/f149.ts" in snapshot and "src/f150.ts" not in snapshot
    assert "… 250 autres entrées" in snapshot


def test_repo_snapshot_of_an_empty_repository_says_so(counting):
    counting.dirs[("main", "")] = []
    snapshot = github_snapshot.build_repo_snapshot("o", "r", "main")
    assert "dépôt vide" in snapshot and "branche lue : main" in snapshot


def test_read_cache_counts_hits_and_reads_and_logs_them(counting, caplog):
    caplog.set_level("INFO", logger="myiacrew")
    with github_client.track_read_cache():
        github_read.github_read_file.func("o", "r", "package.json", "main")
        github_read.github_read_file.func("o", "r", "package.json", "main")
        stats = dict(github_client._read_cache.get()["stats"])
    assert stats == {"hits": 1, "reads": 1}
    assert "[CACHE LECTURE] hits=1 lectures=1" in caplog.text


def test_read_cache_logs_nothing_when_no_read_happened(caplog):
    caplog.set_level("INFO", logger="myiacrew")
    with github_client.track_read_cache():
        pass
    assert "CACHE LECTURE" not in caplog.text


def test_repo_snapshot_falls_back_to_the_raw_files_when_package_json_is_not_json(counting):
    counting.files[("main", "package.json")] = b"{ ceci n'est pas du json"
    snapshot = github_snapshot.build_repo_snapshot("o", "r", "main")
    assert "## package.json" in snapshot and "ceci n'est pas du json" in snapshot
    assert "## Résumé du projet" not in snapshot and "## tsconfig.json" in snapshot
