"""Garde-fous d'écriture : branches de travail, fichiers sensibles, exécution arrêtée."""

import pytest


pytest.importorskip("github_write")  # saute le fichier si PyGithub/crewai ne sont pas installés
import github_pull_request
import github_batch
import github_write
import github_edit
import github_guards
import github_client


BLOCKED = [
    "main", "master", "test", "develop", "production", "feature/x", "crewai/", "crewai/../main", "", "crewai",
    "crewai/a b", "crewai/x\nautre", "crewai/x;rm", "crewai//x", "crewai/x/", "crewai/x.", "crewai/-x", "CREWAI/x", " crewai/x",
]


def _write_calls(branch):
    return [
        github_write.github_write_file.func("o", "r", "src/a.ts", "export const a = 1;", branch, "msg"),
        github_write.github_write_files.func("o", "r", branch, "msg", '[{"path": "src/a.ts", "content": "export const a = 1;"}]'),
        github_edit.github_edit_file.func("o", "r", "src/a.ts", branch, "a", "b", "msg"),
        github_write.github_create_branch.func("o", "r", branch, "main"),
    ]


@pytest.mark.parametrize("branch", BLOCKED)
def test_agents_cannot_write_or_create_anything_outside_work_branches(counting, branch):
    for result in _write_calls(branch):
        assert result.startswith("ERREUR"), result
    assert counting.created == []   # refusé avant tout appel à GitHub


def test_refusal_names_the_rule_so_the_agent_can_correct_itself(counting):
    refused = github_write.github_write_file.func("o", "r", "a.ts", "x", "test", "msg")
    assert "'crewai/…'" in refused and "contexte repository" in refused
    assert "principale" in github_write.github_write_file.func("o", "r", "a.ts", "x", "main", "msg")


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
    result = github_batch.write_files_to_branch("o", "r", "test", "msg", [{"path": "src/a.ts", "content": "export const a = 1;"}])
    assert result.startswith("ERREUR") and counting.created == []


def test_a_refused_write_is_logged_without_file_content(counting, caplog):
    caplog.set_level("INFO", logger="myiacrew")
    github_write.github_write_file.func("o", "r", "src/secret.ts", "CONTENU-SECRET", "test", "msg")
    with github_guards.track_write_scope("crewai/a"):
        github_write.github_write_file.func("o", "r", "src/secret.ts", "CONTENU-SECRET", "crewai/b", "msg")
    github_write.github_write_file.func("o", "r", "src/x.ts", "x", "crewai/x\nFAUSSE LIGNE", "msg")
    refusals = [record for record in caplog.records if "écriture GitHub refusée" in record.getMessage()]
    assert len(refusals) == 3 and {record.levelname for record in refusals} == {"WARNING"}
    out = caplog.text
    assert "'test'" in out and "'crewai/b'" in out and "'crewai/a'" in out
    assert "CONTENU-SECRET" not in out and "src/secret.ts" not in out
    assert "\nFAUSSE LIGNE" not in out   # le nom piégé est échappé par repr()


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
    assert github_write.github_write_file.func("o", "r", path, "x", "crewai/a", "msg").startswith("ERREUR")
    assert github_edit.github_edit_file.func("o", "r", path, "crewai/a", "a", "b", "msg").startswith("ERREUR")
    batch = github_batch.write_files_to_branch("o", "r", "crewai/a", "msg", [{"path": path, "content": "x"}])
    assert batch.startswith("ERREUR") and "refusés" in batch
    assert counting.created == []


@pytest.mark.parametrize("path", ALLOWED)
def test_ordinary_files_are_not_mistaken_for_sensitive_ones(path):
    assert github_guards._reject_sensitive_path(path) is None


def test_the_commit_helper_reports_sensitive_files_in_the_rejection_sink(counting):
    sink = {}
    github_batch.write_files_to_branch(
        "o", "r", "crewai/a", "msg",
        [{"path": ".github/workflows/ci.yml", "content": "on: push"}, {"path": "src/a.ts", "content": "export const a = 1;"}],
        rejected_sink=sink,
    )
    assert list(sink) == [".github/workflows/ci.yml"] and "fichiers de CI" in sink[".github/workflows/ci.yml"]


def test_a_refused_sensitive_write_is_logged_without_content(counting, caplog):
    caplog.set_level("INFO", logger="myiacrew")
    github_write.github_write_file.func("o", "r", ".env", "CLE-SECRETE", "crewai/a", "msg")
    assert "fichier sensible '.env'" in caplog.text and "CLE-SECRETE" not in caplog.text


def test_a_stopped_execution_can_no_longer_write_or_open_a_pull_request(counting, monkeypatch):
    import threading
    event = threading.Event()
    with github_guards.track_write_scope("crewai/a", event):
        assert github_guards._reject_protected_branch("crewai/a") is None          # avant l'arrêt : écriture permise
        event.set()                                                     # posé par un autre thread (délai dépassé)
        refused = github_guards._reject_protected_branch("crewai/a")
        assert refused == github_guards.STOPPED_EXECUTION_MESSAGE and "arrêtée" in refused
        assert github_write.github_write_file.func("o", "r", "src/a.ts", "x", "crewai/a", "msg").startswith("ERREUR")
        assert github_batch.write_files_to_branch("o", "r", "crewai/a", "msg", [{"path": "src/a.ts", "content": "x"}]).startswith("ERREUR")
        assert github_write.github_create_branch.func("o", "r", "crewai/a", "main").startswith("ERREUR")
        monkeypatch.setattr(github_client, "_get_repo", lambda *a: (_ for _ in ()).throw(AssertionError("aucun appel réseau attendu")))
        assert github_pull_request.open_or_update_pull_request("o", "r", "crewai/a", "main", "t", "b") == (None, github_guards.STOPPED_EXECUTION_MESSAGE)
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
