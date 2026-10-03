"""Étapes extraites de _run_crew_and_persist : fonctions pures ou presque, testées une à une."""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402

import main  # noqa: E402
from crewquestion import CrewStepError  # noqa: E402
from errors import classify_exception  # noqa: E402
from github_tools import GitHubVerificationUnavailable  # noqa: E402


def _data(**kwargs):
    params = dict(user_request="x", target_workflow="BUGFIX", repo_owner="o", repo_name="r")
    params.update(kwargs)
    return main.WorkflowExecutionInput(**params)


def test_repo_instructions_without_repo_target_use_the_local_workspace():
    text = main._repo_instructions(False, _data(repo_owner=None, repo_name=None), "", None, False)
    assert "Aucun repository GitHub cible" in text and "espace de travail local" in text


@pytest.mark.parametrize("exists, expected", [(True, "EXISTE DÉJÀ"), (False, "N'EXISTE PAS ENCORE")])
def test_repo_instructions_tell_agents_which_branch_to_read(exists, expected):
    text = main._repo_instructions(True, _data(), "crewai/b", "main", exists)
    assert "Repository GitHub cible : o/r" in text and "Branche de base : main" in text and expected in text
    assert ("branch=crewai/b" if exists else "branch=main") in text


def test_crew_inputs_default_the_base_branch_and_isolate_the_workspace():
    inputs = main._crew_inputs(_data(repo_owner=None, repo_name=None), 42, "prompt", "ctx", "", None, False, False)
    assert inputs["base_branch"] == "main" and inputs["conversation_id"] == "42"
    assert inputs["repo_owner"] == "" and inputs["user_request"] == "prompt" and inputs["conversation_context"] == "ctx"


def test_capture_branch_sha_is_best_effort(monkeypatch):
    assert asyncio.run(main._capture_branch_sha(_data(), "b", False)) is None  # pas de vérification : aucun appel
    monkeypatch.setattr(main, "get_branch_head_sha", lambda *a: "abc")
    assert asyncio.run(main._capture_branch_sha(_data(), "b", True)) == "abc"

    def unavailable(*args):
        raise GitHubVerificationUnavailable("api down")
    monkeypatch.setattr(main, "get_branch_head_sha", unavailable)
    assert asyncio.run(main._capture_branch_sha(_data(), "b", True)) is None  # panne : n'empêche pas le crew


def test_delivery_failure_message_depends_on_the_kind_of_problem():
    access = main._delivery_failure_message(SimpleNamespace(message="branche introuvable", likely_access_problem=True), "rapport")
    other = main._delivery_failure_message(SimpleNamespace(message="PR manquante", likely_access_problem=False), "rapport")
    assert "Vérifie la configuration GITHUB_TOKEN" in access and "branche introuvable" in access
    assert "n'a pas terminé sa procédure" in other and "PR manquante" in other
    assert "--- Rapport de l'agent (non vérifié sur GitHub) ---\nrapport" in access
    long = main._delivery_failure_message(SimpleNamespace(message="m", likely_access_problem=True), "x" * 5000)
    assert long.endswith("x" * 3000) and "x" * 3001 not in long


def test_pull_request_line_is_appended_on_its_own_lines_without_a_section_separator():
    merged = SimpleNamespace(merged=True, html_url="https://github.com/o/r/pull/1")
    opened = SimpleNamespace(merged=False, html_url="https://github.com/o/r/pull/2")
    out = main._with_pull_request_line("## Résumé\n\ntexte", merged)
    assert out == "## Résumé\n\ntexte\n\n**Pull Request fusionnée :** https://github.com/o/r/pull/1"
    assert "**Pull Request ouverte :**" in main._with_pull_request_line("x", opened)
    assert "\n\n---\n\n## " not in out and "\\n" not in out
    assert main._with_pull_request_line("x", None) == "x"


def test_failure_detail_names_the_step_and_keeps_the_technical_text():
    error = CrewStepError(2, 5, "Architecte", RuntimeError("429 quota " + "z" * 600))
    detail = main._failure_detail(error, classify_exception(error))
    assert detail.startswith("Échec à l'étape 2/5 (Architecte) : Le quota du modèle IA")
    assert "(détail : 429 quota" in detail and len(detail) < 800
    plain = RuntimeError("bug interne")
    assert main._failure_detail(plain, classify_exception(plain)) == "bug interne"


def test_failure_report_separates_blocks_with_real_blank_lines(monkeypatch):
    # Régression : un « \\n » littéral (au lieu d'un saut de ligne) collait le bloc GitHub au message.
    async def fake_block(*args):
        return "--- Travail déjà présent sur GitHub ---\n- Rien n'a été poussé"
    monkeypatch.setattr(main, "_partial_delivery_block", fake_block)

    class Session:
        def add(self, *a):
            pass

        def commit(self):
            pass

        def refresh(self, *a):
            pass

        def exec(self, *a, **k):
            raise AssertionError("pas de lecture attendue")

    entry = SimpleNamespace(id=1, result=None, status="running", current_step="qa", error_code=None,
                            error_retryable=None, updated_at=None, api_calls_count=None)
    conversation = SimpleNamespace(updated_at=None)
    monkeypatch.setattr(main, "_safe_refresh", lambda *a, **k: None)
    monkeypatch.setattr(main, "_cleanup_persisted_agents", lambda *a: None)
    error = RuntimeError("boum")
    asyncio.run(main._persist_failure(
        Session(), entry, conversation, error, classify_exception(error), _data(), "crewai/b", "main", True, main._RunState(),
    ))
    assert entry.status == "failed" and entry.current_step is None
    assert entry.result == "boum\n\n--- Travail déjà présent sur GitHub ---\n- Rien n'a été poussé"
