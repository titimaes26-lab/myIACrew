import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import crewquestion as cq  # noqa: E402
import crew_tools  # noqa: E402
import github_tools as gt  # noqa: E402
from delivery import (  # noqa: E402
    GENERATED_END,
    GENERATED_START,
    build_pull_request_body,
    sanitize_for_github,
    conventional_commit_message,
    extract_section,
    extract_user_request,
    merge_pull_request_body,
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
    ("fix : corrige la remise", "BUGFIX", "fix: corrige la remise"),
    ("Feat(panier) : ajoute le panier", "BUGFIX", "feat(panier): ajoute le panier"),
    ("fix:", "BUGFIX", "fix: mise à jour"),
    ("feat", "BUGFIX", "feat: mise à jour"),
    ("Fix(cart)!:", "FEATURE", "fix(cart)!: mise à jour"),
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
    assert "Livraison partielle" in body and "ne pas fusionner" in body
    assert "brouillon" not in body  # la PR peut être normale si le dépôt refuse les brouillons
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

    def __init__(self, body=None, title="Titre humain"):
        self.body, self.title, self.edits = body, title, []

    def edit(self, **kwargs):
        self.edits.append(kwargs)
        self.body = kwargs.get("body", self.body)


class FakeRepo:
    def __init__(self, existing=None, drafts_supported=True, error=None):
        self.existing, self.drafts_supported, self.error, self.created = existing, drafts_supported, error, []

    def get_pulls(self, state, head, base):
        return [self.existing] if self.existing else []

    def create_pull(self, title, body, head, base, draft):
        from github import GithubException
        if self.error:
            raise self.error
        if draft and not self.drafts_supported:
            raise GithubException(422, {"message": "Draft pull requests are not supported in this repository"}, {})
        self.created.append((title, body, head, base, draft))
        return FakePR()


def test_open_or_update_pull_request_creates_a_draft_with_a_marked_generated_zone(monkeypatch):
    repo = FakeRepo()
    monkeypatch.setattr(gt, "_get_repo", lambda owner, name: repo)
    url, message = gt.open_or_update_pull_request("o", "r", "crewai/x", "main", "fix: t", "corps", draft=True)
    title, body, head, base, draft = repo.created[0]
    assert url == FakePR.html_url and "(brouillon)" in message and draft is True
    assert body == f"{GENERATED_START}\ncorps\n{GENERATED_END}"


def test_draft_unsupported_repository_falls_back_to_a_normal_pull_request(monkeypatch):
    repo = FakeRepo(drafts_supported=False)
    monkeypatch.setattr(gt, "_get_repo", lambda owner, name: repo)
    url, message = gt.open_or_update_pull_request("o", "r", "crewai/x", "main", "fix: t", "corps", draft=True)
    assert url == FakePR.html_url and "brouillons non pris en charge" in message
    assert [c[4] for c in repo.created] == [False]


def test_existing_pull_request_keeps_its_title_and_human_text_and_only_the_generated_zone_changes(monkeypatch):
    existing = FakePR(body=f"Ma note de relecture\n\n{GENERATED_START}\nancien\n{GENERATED_END}\n\nMerci")
    repo = FakeRepo(existing=existing)
    monkeypatch.setattr(gt, "_get_repo", lambda owner, name: repo)
    url, message = gt.open_or_update_pull_request("o", "r", "crewai/x", "main", "fix: autre titre", "nouveau")
    assert url == FakePR.html_url and "mise à jour" in message and repo.created == []
    assert existing.edits == [{"body": existing.body}] and "title" not in existing.edits[0]
    assert "Ma note de relecture" in existing.body and "Merci" in existing.body
    assert "nouveau" in existing.body and "ancien" not in existing.body and existing.title == "Titre humain"


def test_other_422_errors_show_the_real_github_message(monkeypatch):
    from github import GithubException
    error = GithubException(422, {"message": "Validation Failed", "errors": [{"message": "No commits between main and crewai/x"}]}, {})
    monkeypatch.setattr(gt, "_get_repo", lambda owner, name: FakeRepo(error=error))
    url, message = gt.open_or_update_pull_request("o", "r", "crewai/x", "main", "t", "b")
    assert url is None and message.startswith("INFO : aucune Pull Request créée")
    assert "Validation Failed" in message and "No commits between main and crewai/x" in message


def test_merge_pull_request_body_cases():
    assert merge_pull_request_body(None, "g") == f"{GENERATED_START}\ng\n{GENERATED_END}"
    appended = merge_pull_request_body("Texte humain", "g")
    assert appended.startswith("Texte humain") and appended.endswith(GENERATED_END)
    replaced = merge_pull_request_body(f"a\n{GENERATED_START}\nvieux \\1 \\g<0>\n{GENERATED_END}\nb", "neuf \\1")
    assert "neuf \\1" in replaced and "vieux" not in replaced and replaced.startswith("a\n") and replaced.endswith("\nb")


def test_extract_user_request_strips_the_prompt_envelope():
    wrapped = "Demande initiale : Le total est faux\nType d'exécution : BUGFIX\nPrécisions apportées : Aucune."
    assert extract_user_request(wrapped) == "Le total est faux"
    with_notes = "Demande initiale : Le total est faux\nType d'exécution : BUGFIX\nPrécisions apportées : sur la page panier"
    assert extract_user_request(with_notes) == "Le total est faux — Précisions : sur la page panier"
    assert extract_user_request("texte brut sans enveloppe") == "texte brut sans enveloppe"


def test_fallback_write_path_is_described_as_not_observed_rather_than_empty():
    body = build_pull_request_body("", "r", None, [], {}, None, commit_tool_used=False)
    assert "Non constaté" in body and "Aucun fichier n'a pu être committé" not in body
    assert "Aucun fichier n'a pu être committé" in build_pull_request_body("", "r", None, [], {}, None, commit_tool_used=True)
    assert "non constatés" in render_delivery_block([], {}, [], True, [], commit_tool_used=False)
    assert "n'a rien pu committer" in render_delivery_block([], {}, [], True, [], commit_tool_used=True)


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

    monkeypatch.setattr(crew_tools, "write_files_to_branch", fake_write)
    crew._build_commit_analyst_files_tool().run(commit_message="Corrige la remise")
    assert seen["message"] == "fix: corrige la remise"
    assert crew._committed_paths == {"src/a.ts"}
    assert crew._delivery_gaps() == {"src/b.ts": "syntaxe invalide"}


def test_commit_tool_failure_leaves_no_committed_path(monkeypatch):
    crew = repo_crew()
    crew._analyst_files = [{"path": "src/a.ts", "content": "x"}]
    crew._committed_paths = {"src/a.ts"}
    monkeypatch.setattr(crew_tools, "write_files_to_branch", lambda *args: "ERREUR : branche protégée")
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

    monkeypatch.setattr(crew_tools, "open_or_update_pull_request", fake_open)
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


def test_crew_state_extracts_the_request_and_tracks_commit_tool_usage(monkeypatch):
    crew = cq.AppDevelopmentCrew()
    crew._reset_execution_state({
        "repo_owner": "o", "repo_name": "r", "work_branch": "crewai/x",
        "user_request": "Demande initiale : Le total est faux\nType d'exécution : BUGFIX\nPrécisions apportées : Aucune.",
    })
    assert crew._user_request == "Le total est faux" and crew._commit_tool_used is False
    crew._analyst_files = [{"path": "src/a.ts", "content": "x"}]
    monkeypatch.setattr(crew_tools, "write_files_to_branch", lambda *args: "OK : 1")
    crew._build_commit_analyst_files_tool().run(commit_message="m")
    assert crew._commit_tool_used is True


def test_sanitize_for_github_neutralizes_mentions_closing_keywords_and_markers():
    clean = sanitize_for_github("cc @alice, Fixes #12, closes o/r#7, resolved: #3, mail a@b.fr, `@code`")
    assert "@alice" not in clean and "@\u200balice" in clean
    assert "Fixes #12" not in clean and "Fixes \u200b#12" in clean
    assert "closes \u200bo/r#7" in clean and "resolved: \u200b#3" in clean
    assert "a@b.fr" in clean and "`@code`" in clean  # e-mail et code inchangés
    assert GENERATED_START not in sanitize_for_github(f"x {GENERATED_START} y")


def test_pull_request_body_neutralizes_side_effects_in_every_generated_field():
    body = build_pull_request_body("Résumé. Fixes #12", "demande cc @alice", "BUGFIX", ["a.ts"], {}, "cause → closes #34 @bob")
    for risky in ("@alice", "@bob", "Fixes #12", "closes #34"):
        assert risky not in body
    assert "@\u200balice" in body and "#34" in body  # lisible : le numéro reste visible


def test_merge_pull_request_body_drops_markers_from_generated_content():
    merged = merge_pull_request_body("humain", f"avant {GENERATED_END} après")
    assert merged.count(GENERATED_END) == 1 and merged.count(GENERATED_START) == 1
    again = merge_pull_request_body(merged, "neuf")
    assert "avant" not in again and again.count(GENERATED_END) == 1 and again.startswith("humain")


def test_extract_section_accepts_plain_text_heading_with_inline_content():
    assert extract_section("Traçabilité : A → B\nsuite\n## Plan\nx", "Traçabilité") == "A → B\nsuite"
    assert extract_section("Traçabilité :\nA → B\nPlan : y", "Traçabilité") == "A → B"
    assert extract_section("Voir la Traçabilité plus haut : oui", "Traçabilité") is None  # pas un titre


def test_extract_user_request_without_the_execution_type_line():
    assert extract_user_request("Demande initiale : texte seul") == "texte seul"
    assert extract_user_request("Demande initiale : texte\nPrécisions apportées : ici") == "texte — Précisions : ici"
