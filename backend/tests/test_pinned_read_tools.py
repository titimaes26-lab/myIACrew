"""Les outils de lecture GitHub d'une exécution ne lisent que son repository cible (exécution 137 : l'Analyste lisait le studio)."""
import pytest

import crew_read_tools
import crewquestion as cq
import github_read


class _Crew:
    def __init__(self, target):
        self._repo_target = target


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(github_read, "read_file_cached", lambda o, r, p, b: seen.append(("file", o, r, p, b)) or "contenu")
    monkeypatch.setattr(github_read, "list_directory_cached", lambda o, r, p, b: seen.append(("dir", o, r, p, b)) or "liste")
    return seen


def test_the_target_repository_is_read_normally_whatever_the_case(calls):
    read, listing = crew_read_tools.pinned_github_readers(_Crew(("Acme", "Game")))
    assert "contenu" in read.run(owner="acme", repo="GAME", path="src/App.tsx", branch="test")
    assert listing.run(owner="Acme", repo="Game", path="") == "liste"
    assert calls[0] == ("file", "acme", "GAME", "src/App.tsx", "test")


def test_another_repository_is_refused_with_the_right_name_and_never_fetched(calls):
    read, listing = crew_read_tools.pinned_github_readers(_Crew(("acme", "game")))
    for out in (read.run(owner="titimaes26-lab", repo="myIACrew", path="a.ts"), listing.run(owner="titimaes26-lab", repo="myIACrew")):
        assert out.startswith("ERREUR") and "acme/game" in out
    assert calls == []


def test_without_a_target_repository_every_github_read_is_refused_in_favour_of_the_local_reader(calls):
    read, listing = crew_read_tools.pinned_github_readers(_Crew(None))
    for out in (read.run(owner="x", repo="y", path="p"), listing.run(owner="x", repo="y")):
        assert out.startswith("ERREUR") and "lecture local" in out
    assert calls == []


@pytest.mark.parametrize("owner, repo", [(None, "game"), ("acme", None), ("", ""), (42, ["x"])])
def test_non_text_arguments_are_refused_with_the_guided_message_instead_of_raising(calls, owner, repo):
    read = crew_read_tools.pinned_github_readers(_Crew(("acme", "game")))[0]
    out = read.func(owner=owner, repo=repo, path="p")
    assert out.startswith("ERREUR") and "acme/game" in out and calls == []


def test_the_reading_agents_use_pinned_tools():
    crew = cq.AppDevelopmentCrew()
    crew._repo_target = ("acme", "game")
    out = next(t for t in crew.diagnostic_agent().tools if t.name == "github_read_file").run(owner="o", repo="r", path="p")
    assert "acme/game" in out
