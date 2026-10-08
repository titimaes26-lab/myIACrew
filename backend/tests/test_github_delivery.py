"""verify_github_delivery : branche, recherche de la Pull Request à jour, réessais et messages de l'issue."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import github_client
import github_delivery
import github_snapshot

SHA = "sha-after"
MERGED = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _pr(sha=SHA, state="open", merged_at=None, url="https://github.com/o/r/pull/7"):
    return SimpleNamespace(head=SimpleNamespace(sha=sha), state=state, merged_at=merged_at, html_url=url)


class _Env:
    """Faux GitHub : `heads` = réponses successives de get_branch_head_sha, `pulls` = réponses successives de
    get_pulls (liste de PR ou exception) ; `sleeps` note les attentes demandées."""

    def __init__(self, heads=(SHA,), pulls=([],)):
        self.heads, self.pulls, self.sleeps, self.pull_calls = list(heads), list(pulls), [], []

    def head(self, owner, repo, branch):
        value = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
        if isinstance(value, Exception):
            raise value
        return value

    def get_pulls(self, **kwargs):
        self.pull_calls.append(kwargs)
        value = self.pulls.pop(0) if len(self.pulls) > 1 else self.pulls[0]
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def env(monkeypatch):
    holder = _Env()

    def configure(**kwargs):
        holder.__init__(**kwargs)
        return holder
    monkeypatch.setattr(github_snapshot, "get_branch_head_sha", lambda o, r, b: holder.head(o, r, b))
    monkeypatch.setattr(github_client, "_get_repo", lambda o, r: SimpleNamespace(get_pulls=holder.get_pulls))
    monkeypatch.setattr(github_delivery.time, "sleep", lambda seconds: holder.sleeps.append(seconds))
    configure.holder = holder
    return configure


def _verify(sha_before="sha-before", base="main"):
    return github_delivery.verify_github_delivery("o", "r", "crewai/x", base, sha_before)


# --- branche ----------------------------------------------------------------------------------------------

def test_a_missing_branch_is_a_probable_access_problem(env):
    env(heads=[None])
    pr, issue = _verify()
    assert pr is None and issue.likely_access_problem and "aucune branche 'crewai/x'" in issue.message


def test_an_unreachable_branch_is_retried_once_after_a_pause_then_reported(env):
    unavailable = github_snapshot.GitHubVerificationUnavailable("api HS")
    e = env(heads=[unavailable])
    pr, issue = _verify()
    assert pr is None and issue.likely_access_problem and "impossible de vérifier la branche 'crewai/x' (api HS)" in issue.message
    assert e.sleeps == [2]


def test_a_transient_branch_error_followed_by_success_goes_on_to_the_pull_request(env):
    e = env(heads=[github_snapshot.GitHubVerificationUnavailable("blip"), SHA], pulls=[[_pr()]])
    pr, issue = _verify()
    assert issue is None and pr.html_url.endswith("/pull/7") and e.sleeps == [2]


# --- Pull Request à jour ------------------------------------------------------------------------------------

def test_the_pull_request_is_looked_up_for_the_work_branch_and_the_base(env):
    e = env(pulls=[[_pr()]])
    _verify(base="develop")
    assert e.pull_calls == [{"state": "all", "head": "o:crewai/x", "base": "develop"}]


def test_an_open_pull_request_on_the_current_commit_confirms_the_delivery_without_any_pause(env):
    e = env(pulls=[[_pr()]])
    pr, issue = _verify()
    assert issue is None and pr == github_delivery.DeliveredPullRequest("https://github.com/o/r/pull/7", False)
    assert e.sleeps == [] and len(e.pull_calls) == 1


def test_a_merged_pull_request_confirms_the_delivery_and_is_flagged_merged(env):
    env(pulls=[[_pr(state="closed", merged_at=MERGED)]])
    pr, issue = _verify()
    assert issue is None and pr.merged is True


@pytest.mark.parametrize("pull_requests", [
    [_pr(state="closed", merged_at=None)],        # fermée sans fusion : proposition refusée, jamais « livrée »
    [_pr(sha="autre-commit")],                    # PR à jour d'un autre commit (tour précédent)
    [_pr(sha="autre-commit", state="closed", merged_at=MERGED)],
])
def test_a_rejected_or_stale_pull_request_never_counts_as_delivered(env, pull_requests):
    env(pulls=[pull_requests])
    pr, issue = _verify()
    assert pr is None and "aucune Pull Request à jour vers 'main'" in issue.message


def test_the_matching_pull_request_is_found_among_several(env):
    env(pulls=[[_pr(sha="autre"), _pr(state="closed"), _pr(url="https://github.com/o/r/pull/9")]])
    pr, _ = _verify()
    assert pr.html_url.endswith("/pull/9")


def test_a_pull_request_missing_at_first_is_searched_again_after_a_pause(env):
    e = env(pulls=[[], [_pr()]])      # cohérence éventuelle de la liste : la PR n'apparaît qu'au second essai
    pr, issue = _verify()
    assert issue is None and pr is not None and e.sleeps == [2] and len(e.pull_calls) == 2


# --- recherche qui échoue : « absent » ou « non vérifié » ------------------------------------------------------

def test_an_absence_confirmed_by_the_first_search_survives_a_failure_of_the_second(env):
    env(pulls=[[], RuntimeError("blip")])
    pr, issue = _verify(sha_before=None)
    assert pr is None and not issue.likely_access_problem and "aucune Pull Request à jour" in issue.message


def test_two_failed_searches_are_reported_as_unverified_and_point_to_the_access(env):
    env(pulls=[RuntimeError("a"), RuntimeError("b")])
    pr, issue = _verify(sha_before=None)
    assert pr is None and issue.likely_access_problem and "n'a pas pu être vérifiée" in issue.message


def test_a_failed_first_search_followed_by_an_empty_second_one_is_a_confirmed_absence(env):
    env(pulls=[RuntimeError("a"), []])
    _, issue = _verify(sha_before=None)
    assert not issue.likely_access_problem and "aucune Pull Request à jour" in issue.message


# --- message selon le commit d'avant l'exécution ---------------------------------------------------------------

def test_an_unchanged_branch_without_pull_request_says_nothing_was_delivered(env):
    env(pulls=[[]])
    _, issue = _verify(sha_before=SHA)
    assert "pointe toujours sur le même commit" in issue.message and "aucun changement n'a été livré" in issue.message
    assert not issue.likely_access_problem


def test_an_unchanged_branch_with_an_unverifiable_pull_request_stays_cautious(env):
    env(pulls=[RuntimeError("a"), RuntimeError("b")])
    _, issue = _verify(sha_before=SHA)
    assert "pointe toujours sur le même commit" in issue.message
    assert "il est possible qu'aucun changement n'ait été livré" in issue.message and "aucun changement n'a été livré pendant" not in issue.message
    assert issue.likely_access_problem


def test_new_commits_without_pull_request_are_reported_as_confirmed(env):
    env(pulls=[[]])
    _, issue = _verify(sha_before="sha-before")
    assert "(nouveaux commits confirmés)" in issue.message and "pointe toujours" not in issue.message


def test_a_branch_new_since_the_launch_does_not_claim_new_commits(env):
    env(pulls=[[]])
    _, issue = _verify(sha_before=None)
    assert "existe bien sur o/r mais" in issue.message and "nouveaux commits" not in issue.message
