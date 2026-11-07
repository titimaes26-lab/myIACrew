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


def test_without_a_target_repository_nothing_is_restricted(calls):
    read, _ = crew_read_tools.pinned_github_readers(_Crew(None))
    read.run(owner="x", repo="y", path="p")
    assert calls and calls[0][1:3] == ("x", "y")


def test_the_reading_agents_use_pinned_tools():
    crew = cq.AppDevelopmentCrew()
    crew._repo_target = ("acme", "game")
    out = next(t for t in crew.diagnostic_agent().tools if t.name == "github_read_file").run(owner="o", repo="r", path="p")
    assert "acme/game" in out
