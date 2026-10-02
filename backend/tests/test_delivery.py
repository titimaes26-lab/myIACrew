import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import crewquestion as cq  # noqa: E402
import github_tools as gt  # noqa: E402
from delivery import (  # noqa: E402
    build_pull_request_body,
    conventional_commit_message,
    extract_section,
    render_delivery_block,
    unconfirmed_pr_urls,
)


@pytest.mark.parametrize("message, workflow, expected", [
    ("Corrige le calcul de la remise", "BUGFIX", "fix: corrige le calcul de la remise"),
    ("Ajoute l'export CSV", "FEATURE", "feat: ajoute l'export CSV"),
    ("API publique", "FEATURE", "feat: API publique"),
    ("feat(cart): ajoute le panier", "BUGFIX", "feat(cart): ajoute le panier"),
    ("Mise en place", None, "chore: mise en place"),
    ("", "BUGFIX", "fix: mise à jour"),
])
def test_conventional_commit_message_prefix(message, workflow, expected):
    assert conventional_commit_message(message, workflow) == expected


def test_conventional_commit_message_truncates_subject_and_keeps_body():
    result = conventional_commit_message("a" * 120 + "\n\nDétails du changement", "FEATURE")
    first, _, rest = result.partition("\n")
    assert len(first) == 72 and first.endswith("…") and rest.strip() == "Détails du changement"


def test_extract_section_stops_at_next_heading_or_file_marker():
    raw = "## Hypothèses\nx\n## Traçabilité\nsymptôme → cause\nVérification manuelle : cliquer\n## Plan\nautre\n"
    assert extract_section(raw, "Traçabilité") == "symptôme → cause\nVérification manuelle : cliquer"
    assert extract_section("**Traçabilité**\nA → B\n<<<FICHIER: x>>>\ncode", "Traçabilité") == "A → B"
    assert extract_section("## Traçabilité\n\n## Plan\n", "Traçabilité") is None
    assert extract_section("rien", "Traçabilité") is None


def test_pull_request_body_flags_partial_delivery_and_includes_facts():
    body = build_pull_request_body(
        "Corrige la remise.", "Le total ne prend pas la remise\n" + "x" * 600, "BUGFIX",
        ["src/cart.ts"], {"src/big.ts": "contenu incomplet"}, "symptôme → cause",
    )
    assert "Livraison partielle" in body and "brouillon" in body
    assert "- `src/cart.ts`" in body and "- `src/big.ts` : contenu incomplet" in body
    assert "symptôme → cause" in body and "(BUGFIX)" in body
    assert "x" * 500 not in body


def test_pull_request_body_complete_delivery_has_no_warning():
    body = build_pull_request_body("", "demande", None, ["a.ts"], {}, None)
    assert "Livraison partielle" not in body and "Aucun résumé fourni" in body and "Non livrés" not in body


def test_unconfirmed_pr_urls_and_delivery_block():
    real = "https://github.com/o/r/pull/12"
    invented = "https://github.com/o/r/pull/99"
    assert unconfirmed_pr_urls(f"PR : {real} et {invented}", [real]) == [invented]
    block = render_delivery_block(["a.ts"], {"b.ts": "rejeté"}, [real], True, [invented])
    assert real in block and "`b.ts` (rejeté)" in block and invented in block and "jamais renvoyée" in block
    assert "sans objet" in render_delivery_block([], {}, [], False, [])
    assert "aucune ouverte" in render_delivery_block([], {}, [], True, [])


# --- github_tools.open_or_update_pull_request ------------------------------------------------

class FakePR:
    html_url = "https://github.com/o/r/pull/7"

    def __init__(self):
        self.edited = None

    def edit(self, title, body):
        self.edited = (title, body)


class FakeRepo:
    def __init__(self, existing=None, error=None):
        self.existing, self.error, self.created = existing, error, None

    def get_pulls(self, state, head, base):
        return [self.existing] if self.existing else []

    def create_pull(self, title, body, head, base, draft):
        if self.error:
            raise self.error
        self.created = (title, head, base, draft)
        return FakePR()


def test_open_or_update_pull_request_creates_a_draft(monkeypatch):
    repo = FakeRepo()
    monkeypatch.setattr(gt, "_get_repo", lambda owner, name: repo)
    url, message = gt.open_or_update_pull_request("o", "r", "crewai/x", "main", "fix: t", "b", draft=True)
    assert url == FakePR.html_url and "(brouillon)" in message and repo.created == ("fix: t", "crewai/x", "main", True)


def test_open_or_update_pull_request_updates_the_existing_one(monkeypatch):
    existing = FakePR()
    repo = FakeRepo(existing=existing)
    monkeypatch.setattr(gt, "_get_repo", lambda owner, name: repo)
    url, message = gt.open_or_update_pull_request("o", "r", "crewai/x", "main", "fix: t", "body")
    assert url == FakePR.html_url and "mis à jour" in message
    assert existing.edited == ("fix: t", "body") and repo.created is None


def test_open_or_update_pull_request_422_is_informative_not_an_error(monkeypatch):
    from github import GithubException
    monkeypatch.setattr(gt, "_get_repo", lambda owner, name: FakeRepo(error=GithubException(422, {}, {})))
    url, message = gt.open_or_update_pull_request("o", "r", "crewai/x", "main", "t", "b")
    assert url is None and message.startswith("INFO : aucune Pull Request créée")


# --- Outils et guardrail du crew -------------------------------------------------------------

def repo_crew(request_type="BUGFIX"):
    crew = cq.AppDevelopmentCrew()
    crew._reset_execution_state({"repo_owner": "o", "repo_name": "r", "work_branch": "crewai/x", "user_request": "bug"})
    crew._request_type = request_type
    return crew


def test_commit_tool_normalizes_the_message_and_tracks_committed_paths(monkeypatch):
    crew = repo_crew()
    crew._analyst_files = [{"path": "src/a.ts", "content": "x"}, {"path": "src/b.ts", "content": "y"}]
    seen = {}

    def fake_write(owner, repo, branch, message, files, rejections):
        seen["message"] = message
        rejections["src/b.ts"] = "syntaxe invalide"
        return "OK : 1 fichier(s) committé(s)"

    monkeypatch.setattr(cq, "write_files_to_branch", fake_write)
    crew._build_commit_analyst_files_tool().run(commit_message="Corrige la remise")
    assert seen["message"] == "fix: corrige la remise"
    assert crew._committed_paths == {"src/a.ts"}
    assert crew._delivery_gaps() == {"src/b.ts": "syntaxe invalide"}


def test_commit_tool_failure_leaves_no_committed_path(monkeypatch):
    crew = repo_crew()
    crew._analyst_files = [{"path": "src/a.ts", "content": "x"}]
    crew._committed_paths = {"src/a.ts"}
    monkeypatch.setattr(cq, "write_files_to_branch", lambda *args: "ERREUR : branche protégée")
    crew._build_commit_analyst_files_tool().run(commit_message="m")
    assert crew._committed_paths == set()


def test_pull_request_tool_builds_the_body_from_facts_and_drafts_partial_deliveries(monkeypatch):
    crew = repo_crew()
    crew._committed_paths = {"src/a.ts"}
    crew._not_extracted = {"src/big.ts": "trop volumineux"}
    crew._analyst_raw = "## Traçabilité\nsymptôme → cause\n## Plan\nx\n"
    calls = {}

    def fake_open(owner, repo, branch, base, title, body, draft=False):
        calls.update(branch=branch, base=base, title=title, body=body, draft=draft)
        return "https://github.com/o/r/pull/5", "OK : Pull Request (brouillon) créée"

    monkeypatch.setattr(cq, "open_or_update_pull_request", fake_open)
    message = crew._build_open_pull_request_tool().run(title="Corrige la remise", summary="Résumé court")
    assert message.startswith("OK") and crew._pull_request_urls == ["https://github.com/o/r/pull/5"]
    assert calls["draft"] is True and calls["title"] == "fix: corrige la remise"
    assert "Résumé court" in calls["body"] and "symptôme → cause" in calls["body"] and "`src/big.ts`" in calls["body"]


def test_pull_request_tool_is_a_noop_without_a_repository():
    crew = cq.AppDevelopmentCrew()
    crew._reset_execution_state({})
    assert "aucune Pull Request à ouvrir" in crew._build_open_pull_request_tool().run(title="t", summary="s")


def test_development_report_guardrail_appends_tool_facts_and_flags_invented_urls():
    crew = repo_crew()
    crew._committed_paths = {"src/a.ts"}
    crew._pull_request_urls = ["https://github.com/o/r/pull/5"]
    ok, report = crew._development_report_guardrail(
        type("O", (), {"raw": "Pull Request : https://github.com/o/r/pull/99"})()
    )
    assert ok is True and report.startswith("Pull Request : https://github.com/o/r/pull/99")
    assert "## Livraison constatée par les outils" in report
    assert "https://github.com/o/r/pull/5" in report and "jamais renvoyée" in report and "pull/99" in report


def test_developer_uses_the_templated_pull_request_tool_only():
    crew = repo_crew()
    names = [getattr(t, "name", "") for t in crew.developer_agent().tools]
    assert "github_open_delivery_pull_request" in names and "github_open_pull_request" not in names
    task = crew.development_task()
    assert task.guardrail is not None and task.guardrail_max_retries == 0
