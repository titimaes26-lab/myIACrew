import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

gt = pytest.importorskip("github_tools")
import github_guards  # noqa: E402
import github_client  # noqa: E402
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
    monkeypatch.setattr(github_client, "_get_repo", lambda owner, r: repo)
    monkeypatch.setattr(repo, "get_contents", lambda path, ref: FakeContentFile(decoded=None), raising=False)
    result = gt.github_read_file.run(owner="o", repo="r", path="big.txt", branch="main")
    assert result == "grand fichier\n"


def test_github_read_file_does_not_double_prefix_blob_fallback_errors(monkeypatch):
    repo = FakeRepo(blob_error=GithubException(403, {"message": "API rate limit exceeded"}, None))
    monkeypatch.setattr(github_client, "_get_repo", lambda owner, r: repo)
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
        files={("main", "package.json"): b'{"name": "app"}', ("main", "tsconfig.json"): b'{"compilerOptions": {"strict": true}}'},
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
    monkeypatch.setattr(github_client, "Github", FakeGithub)
    repo.created = created
    return repo


def test_without_a_cache_every_read_goes_to_github(counting):
    gt.github_read_file.func("o", "r", "package.json", "main")
    gt.github_read_file.func("o", "r", "package.json", "main")
    assert counting.reads == 2 and len(counting.created) == 2


def test_cache_serves_repeated_reads_and_builds_the_repo_once(counting):
    with github_client.track_read_cache():
        first = gt.github_read_file.func("o", "r", "package.json", "main")
        second = gt.github_read_file.func("o", "r", "package.json", "main")
        gt.github_list_directory.func("o", "r", "", "main")
        gt.github_list_directory.func("o", "r", "", "main")
    assert first == second == '{"name": "app"}'
    assert counting.reads == 2          # un fichier + un dossier, une fois chacun
    assert len(counting.created) == 1   # get_repo une seule fois pour toute l'exécution


def test_cache_never_keeps_errors_and_is_isolated_per_execution(counting):
    with github_client.track_read_cache():
        assert gt.github_read_file.func("o", "r", "absent.ts", "main").startswith("ERREUR_FICHIER_INEXISTANT")
        gt.github_read_file.func("o", "r", "absent.ts", "main")
        assert counting.reads == 2      # l'erreur n'est pas mémorisée
        gt.github_read_file.func("o", "r", "package.json", "main")
    with github_client.track_read_cache():
        gt.github_read_file.func("o", "r", "package.json", "main")
    assert counting.reads == 4          # un nouveau contexte repart d'un cache vide


def test_a_write_invalidates_only_the_written_branch(counting):
    counting.files[("work", "package.json")] = b'{"name": "work"}'
    with github_client.track_read_cache():
        gt.github_read_file.func("o", "r", "package.json", "main")
        gt.github_read_file.func("o", "r", "package.json", "work")
        reads = counting.reads
        github_client.invalidate_read_cache("o", "r", "work")
        gt.github_read_file.func("o", "r", "package.json", "main")   # toujours en cache
        assert counting.reads == reads
        gt.github_read_file.func("o", "r", "package.json", "work")   # relu après l'écriture
        assert counting.reads == reads + 1


def test_delivery_checks_never_read_from_or_fill_the_file_cache(counting):
    with github_client.track_read_cache():
        gt.make_file_fetcher("o", "r", "main")("package.json")
        gt.make_dir_lister("o", "r", "main")
        cache = github_client._read_cache.get()
        assert cache["files"] == {} and cache["dirs"] == {}


def test_repo_snapshot_lists_the_root_reads_the_config_files_and_src(counting):
    snapshot = gt.build_repo_snapshot("o", "r", "main")
    assert "branche lue : main" in snapshot
    assert "## Racine" in snapshot and "## Résumé du projet" in snapshot and "- Nom : app" in snapshot
    assert "mode strict activé" in snapshot and "## src" in snapshot and "src/App.tsx" in snapshot
    assert '{"name": "app"}' not in snapshot   # le JSON brut est remplacé par son résumé


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


def test_read_cache_counts_hits_and_reads_and_logs_them(counting, caplog):
    caplog.set_level("INFO", logger="myiacrew")
    with github_client.track_read_cache():
        gt.github_read_file.func("o", "r", "package.json", "main")
        gt.github_read_file.func("o", "r", "package.json", "main")
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
    snapshot = gt.build_repo_snapshot("o", "r", "main")
    assert "## package.json" in snapshot and "ceci n'est pas du json" in snapshot
    assert "## Résumé du projet" not in snapshot and "## tsconfig.json" in snapshot


# --- Écriture limitée aux branches de travail `crewai/…` ----------------------------------------------------------

BLOCKED = [
    "main", "master", "test", "develop", "production", "feature/x", "crewai/", "crewai/../main", "", "crewai",
    "crewai/a b", "crewai/x\nautre", "crewai/x;rm", "crewai//x", "crewai/x/", "crewai/x.", "crewai/-x", "CREWAI/x", " crewai/x",
]


def _write_calls(branch):
    return [
        gt.github_write_file.func("o", "r", "src/a.ts", "export const a = 1;", branch, "msg"),
        gt.github_write_files.func("o", "r", branch, "msg", '[{"path": "src/a.ts", "content": "export const a = 1;"}]'),
        gt.github_edit_file.func("o", "r", "src/a.ts", branch, "a", "b", "msg"),
        gt.github_create_branch.func("o", "r", branch, "main"),
    ]


@pytest.mark.parametrize("branch", BLOCKED)
def test_agents_cannot_write_or_create_anything_outside_work_branches(counting, branch):
    for result in _write_calls(branch):
        assert result.startswith("ERREUR"), result
    assert counting.created == []   # refusé avant tout appel à GitHub


def test_refusal_names_the_rule_so_the_agent_can_correct_itself(counting):
    refused = gt.github_write_file.func("o", "r", "a.ts", "x", "test", "msg")
    assert "'crewai/…'" in refused and "contexte repository" in refused
    assert "principale" in gt.github_write_file.func("o", "r", "a.ts", "x", "main", "msg")


@pytest.mark.parametrize("branch", [
    "crewai/feature-ab12cd34", "crewai/bugfix-00000000", "crewai/x", "crewai/design_and_dev-ab12cd34", "crewai/analyse_only-5e3940d9",
    "crewai/sous/dossier-1", "crewai/v1.2-x",
])
def test_work_branches_are_accepted(branch):
    assert github_guards._reject_protected_branch(branch) is None


def test_write_scope_restricts_the_execution_to_its_own_work_branch():
    assert github_guards._reject_protected_branch("crewai/b") is None   # sans périmètre : la règle du préfixe seule
    with github_guards.track_write_scope("crewai/a"):
        assert github_guards._reject_protected_branch("crewai/a") is None
        refused = github_guards._reject_protected_branch("crewai/b")
        assert refused and "crewai/a" in refused
        assert github_guards._reject_protected_branch("test")          # le préfixe reste exigé
    assert github_guards._reject_protected_branch("crewai/b") is None   # le périmètre disparaît avec le contexte


def test_the_commit_helper_used_by_the_crew_refuses_other_branches(counting):
    result = gt.write_files_to_branch("o", "r", "test", "msg", [{"path": "src/a.ts", "content": "export const a = 1;"}])
    assert result.startswith("ERREUR") and counting.created == []


def test_a_refused_write_is_logged_without_file_content(counting, caplog):
    caplog.set_level("INFO", logger="myiacrew")
    gt.github_write_file.func("o", "r", "src/secret.ts", "CONTENU-SECRET", "test", "msg")
    with github_guards.track_write_scope("crewai/a"):
        gt.github_write_file.func("o", "r", "src/secret.ts", "CONTENU-SECRET", "crewai/b", "msg")
    gt.github_write_file.func("o", "r", "src/x.ts", "x", "crewai/x\nFAUSSE LIGNE", "msg")
    refusals = [record for record in caplog.records if "écriture GitHub refusée" in record.getMessage()]
    assert len(refusals) == 3 and {record.levelname for record in refusals} == {"WARNING"}
    out = caplog.text
    assert "'test'" in out and "'crewai/b'" in out and "'crewai/a'" in out
    assert "CONTENU-SECRET" not in out and "src/secret.ts" not in out
    assert "\nFAUSSE LIGNE" not in out   # le nom piégé est échappé par repr()


# --- Fichiers sensibles (CI, environnement, déploiement) : jamais écrits par un agent ------------------------------

SENSITIVE = [
    ".github/workflows/ci.yml", "./.github/workflows/deploy.yml", "/.github/CODEOWNERS", ".GITHUB/workflows/x.yml",
    "sub/.github/workflows/x.yml", ".git/hooks/pre-commit", ".circleci/config.yml", ".husky/pre-push",
    ".env", ".env.production", "app/.env.local", "Dockerfile", "docker/Dockerfile", "docker-compose.yml",
    "vercel.json", "render.yaml", "netlify.toml", ".gitlab-ci.yml", "Jenkinsfile", ".npmrc", "src\\..\\x.ts", "a/../b.ts", "",
    "Dockerfile.dev", "app.Dockerfile", "docker-compose.override.yml", ".pre-commit-config.yaml", ".gitmodules", ".gitattributes",
]
ALLOWED = [".env.example", ".env.sample", "app/.env.template", "src/docker-utils.ts", "docs/Dockerfile-notes.md", "src/a.ts", "src/github/api.ts", "docs/.github-notes.md", "environment.ts", "src/env.ts", "README.md", "package.json"]


@pytest.mark.parametrize("path", SENSITIVE)
def test_sensitive_files_are_refused_without_any_network_call(counting, path):
    refused = github_guards._reject_sensitive_path(path)
    assert refused and refused.startswith("ERREUR") and "non livré" in refused
    assert gt.github_write_file.func("o", "r", path, "x", "crewai/a", "msg").startswith("ERREUR")
    assert gt.github_edit_file.func("o", "r", path, "crewai/a", "a", "b", "msg").startswith("ERREUR")
    batch = gt.write_files_to_branch("o", "r", "crewai/a", "msg", [{"path": path, "content": "x"}])
    assert batch.startswith("ERREUR") and "refusés" in batch
    assert counting.created == []


@pytest.mark.parametrize("path", ALLOWED)
def test_ordinary_files_are_not_mistaken_for_sensitive_ones(path):
    assert github_guards._reject_sensitive_path(path) is None


def test_the_commit_helper_reports_sensitive_files_in_the_rejection_sink(counting):
    sink = {}
    gt.write_files_to_branch(
        "o", "r", "crewai/a", "msg",
        [{"path": ".github/workflows/ci.yml", "content": "on: push"}, {"path": "src/a.ts", "content": "export const a = 1;"}],
        rejected_sink=sink,
    )
    assert list(sink) == [".github/workflows/ci.yml"] and "fichiers de CI" in sink[".github/workflows/ci.yml"]


def test_a_refused_sensitive_write_is_logged_without_content(counting, caplog):
    caplog.set_level("INFO", logger="myiacrew")
    gt.github_write_file.func("o", "r", ".env", "CLE-SECRETE", "crewai/a", "msg")
    assert "fichier sensible '.env'" in caplog.text and "CLE-SECRETE" not in caplog.text


# --- Exécution arrêtée (durée maximale) : le thread survivant n'écrit plus -----------------------------------------

def test_a_stopped_execution_can_no_longer_write_or_open_a_pull_request(counting, monkeypatch):
    import threading
    event = threading.Event()
    with github_guards.track_write_scope("crewai/a", event):
        assert github_guards._reject_protected_branch("crewai/a") is None          # avant l'arrêt : écriture permise
        event.set()                                                     # posé par un autre thread (délai dépassé)
        refused = github_guards._reject_protected_branch("crewai/a")
        assert refused == github_guards.STOPPED_EXECUTION_MESSAGE and "arrêtée" in refused
        assert gt.github_write_file.func("o", "r", "src/a.ts", "x", "crewai/a", "msg").startswith("ERREUR")
        assert gt.write_files_to_branch("o", "r", "crewai/a", "msg", [{"path": "src/a.ts", "content": "x"}]).startswith("ERREUR")
        assert gt.github_create_branch.func("o", "r", "crewai/a", "main").startswith("ERREUR")
        monkeypatch.setattr(github_client, "_get_repo", lambda *a: (_ for _ in ()).throw(AssertionError("aucun appel réseau attendu")))
        assert gt.open_or_update_pull_request("o", "r", "crewai/a", "main", "t", "b") == (None, github_guards.STOPPED_EXECUTION_MESSAGE)
    assert counting.created == []
    assert github_guards._reject_protected_branch("crewai/a") is None              # hors du contexte : plus de signal


def test_the_stop_signal_is_seen_by_a_thread_that_copied_the_context():
    import contextvars
    import threading
    event = threading.Event()
    seen = []
    with github_guards.track_write_scope("crewai/a", event):
        context = contextvars.copy_context()
        worker = threading.Thread(target=lambda: context.run(lambda: seen.append(github_guards.writes_cancelled())))
        event.set()
        worker.start()
        worker.join()
    assert seen == [True]
