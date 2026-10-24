"""Un secret collé dans la demande ne part JAMAIS chez le fournisseur du modèle, ni à la qualification, ni dans le fichier de rapport."""
import asyncio

import pytest

import crewquestion as cq
import qualification
from input_guards import REDACTED

GITHUB = "ghp_" + "a1B2c3D4e5" * 4
GOOGLE = "AIza" + "x" * 35


class _Result:
    pydantic = qualification.AnalysisReport(summary="Résumé", reasoning="r", request_type="BUGFIX", confidence=0.9, is_clear=True)
    raw = ""


@pytest.fixture()
def sent(monkeypatch):
    """Capture le texte réellement confié à l'agent de qualification (description de sa tâche)."""
    captured = {}

    class FakeCrew:
        def __init__(self, agents, tasks, **kwargs):
            captured["description"] = tasks[0].description

        async def kickoff_async(self, inputs=None):
            return _Result()

    monkeypatch.setattr(cq, "Crew", FakeCrew)
    return captured


def _qualify(prompt, context=""):
    return asyncio.run(cq.AppDevelopmentCrew().analyze_user_request(prompt, context, False))


def test_the_qualification_prompt_never_contains_the_secret_of_the_request(sent):
    _qualify(f"Corrige le bug, ma clé est {GITHUB}")
    assert GITHUB not in sent["description"] and REDACTED in sent["description"] and "Corrige le bug" in sent["description"]


def test_the_qualification_prompt_never_contains_a_secret_of_the_history(sent):
    _qualify("Continue", f"Tour précédent : j'avais collé {GOOGLE}")
    assert GOOGLE not in sent["description"] and REDACTED in sent["description"]


def test_an_ordinary_request_reaches_the_model_unchanged(sent):
    _qualify("Ajoute un bouton de connexion à src/App.tsx", "Tour précédent : header créé")
    assert "Ajoute un bouton de connexion à src/App.tsx" in sent["description"] and "header créé" in sent["description"]
    assert REDACTED not in sent["description"]


def test_the_fallback_summary_never_echoes_a_secret(monkeypatch):
    class Empty:
        pydantic = None
        raw = "pas de JSON"

    class FakeCrew:
        def __init__(self, agents, tasks, **kwargs):
            pass

        async def kickoff_async(self, inputs=None):
            return Empty()

    monkeypatch.setattr(cq, "Crew", FakeCrew)
    result = _qualify(f"Fais quelque chose avec {GITHUB}")
    assert result.fallback and GITHUB not in result.summary and REDACTED in result.summary


def test_the_report_file_never_stores_the_secret(tmp_path):
    path = tmp_path / "docs" / "qualification_report.md"
    cq.AppDevelopmentCrew().save_analysis_report(_Result.pydantic, f"Demande avec {GITHUB}", filepath=str(path))
    text = path.read_text(encoding="utf-8")
    assert GITHUB not in text and REDACTED in text and "Demande avec" in text


def test_the_report_file_is_ignored_by_git():
    import os
    with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".gitignore"), encoding="utf-8") as handle:
        assert "docs/qualification_report.md" in handle.read().splitlines()
