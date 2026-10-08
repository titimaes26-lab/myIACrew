"""Contexte d'une exécution : consignes, aperçu du dépôt, entrées du crew, petite FEATURE sans architecture."""
"""Étapes extraites de _run_crew_and_persist : fonctions pures ou presque, testées une à une."""
import asyncio
from types import SimpleNamespace


import pytest
import github_client

import execution
import execution_context
from github_snapshot import GitHubVerificationUnavailable
from sqlmodel import Session
from database import ExecutionHistory
from run_support import no_network_snapshot  # noqa: E402,F401  (fixture automatique : jamais de réseau pour l'aperçu du dépôt)
from run_support import _data, _new_execution, _fake_crew


def test_repo_instructions_without_repo_target_use_the_local_workspace():
    text = execution_context.repo_instructions(False, _data(repo_owner=None, repo_name=None), "", None, False)
    assert "Aucun repository GitHub cible" in text and "espace de travail local" in text


@pytest.mark.parametrize("exists, expected", [(True, "EXISTE DÉJÀ"), (False, "N'EXISTE PAS ENCORE")])
def test_repo_instructions_tell_agents_which_branch_to_read(exists, expected):
    text = execution_context.repo_instructions(True, _data(), "crewai/b", "main", exists)
    assert "Repository GitHub cible : o/r" in text and "Branche de base : main" in text and expected in text
    assert ("branch=crewai/b" if exists else "branch=main") in text


def test_crew_inputs_default_the_base_branch_and_isolate_the_workspace():
    inputs = execution_context.crew_inputs(_data(repo_owner=None, repo_name=None), 42, "prompt", "ctx", "", None, False, False)
    assert inputs["base_branch"] == "main" and inputs["conversation_id"] == "42"
    assert inputs["repo_owner"] == "" and inputs["user_request"] == "prompt" and inputs["conversation_context"] == "ctx"


def test_capture_branch_sha_is_best_effort(monkeypatch):
    assert asyncio.run(execution_context.capture_branch_sha(_data(), "b", False)) is None  # pas de vérification : aucun appel
    monkeypatch.setattr(execution_context, "get_branch_head_sha", lambda *a: "abc")
    assert asyncio.run(execution_context.capture_branch_sha(_data(), "b", True)) == "abc"

    def unavailable(*args):
        raise GitHubVerificationUnavailable("api down")
    monkeypatch.setattr(execution_context, "get_branch_head_sha", unavailable)
    assert asyncio.run(execution_context.capture_branch_sha(_data(), "b", True)) is None  # panne : n'empêche pas le crew


def test_analysis_with_a_repo_target_still_learns_that_the_work_branch_exists(engine, monkeypatch):
    # Régression : le SHA n'était capturé que pour les runs qui vérifient la livraison, donc ANALYSE_ONLY
    # disait aux agents que la branche « n'existe pas encore » même quand elle existait.
    seen: list[dict] = []
    _fake_crew(monkeypatch, None, seen)
    monkeypatch.setattr(execution_context, "get_branch_head_sha", lambda *a: "abc123")
    execution_id, conversation_id = _new_execution(engine, "ANALYSE_ONLY")
    data = _data(target_workflow="ANALYSE_ONLY")
    asyncio.run(execution.run_crew_and_persist(execution_id, conversation_id, data, True, False, "crewai/b", "main", "p", "c"))
    assert "EXISTE DÉJÀ" in seen[0]["repo_instructions"]
    with Session(engine) as db:
        assert db.get(ExecutionHistory, execution_id).status == "success"


def test_crew_inputs_carry_the_repo_snapshot_with_an_empty_default():
    assert execution_context.crew_inputs(_data(), 1, "p", "c", "b", "main", True, False)["repo_snapshot"] == ""
    assert execution_context.crew_inputs(_data(), 1, "p", "c", "b", "main", True, False, "APERÇU")["repo_snapshot"] == "APERÇU"


@pytest.mark.parametrize("workflow, has_repo, branch_exists, expected_branch", [
    ("FEATURE", True, False, "main"),
    ("DESIGN_AND_DEV", True, True, "crewai/b"),
    ("ANALYSE_ONLY", True, False, "main"),
    ("BUGFIX", True, False, "main"),               # le Diagnostic lit aussi l'aperçu
    ("FEATURE", False, False, None),               # pas de repository cible
])
def test_snapshot_is_prefetched_only_with_a_repo_and_an_architecture_step(monkeypatch, workflow, has_repo, branch_exists, expected_branch):
    calls = []
    monkeypatch.setattr(execution_context, "build_repo_snapshot", lambda owner, repo, branch: calls.append((owner, repo, branch)) or "APERÇU")
    snapshot = asyncio.run(execution_context.prefetch_repo_snapshot(
        _data(target_workflow=workflow), "crewai/b", "main", has_repo, branch_exists))
    assert (calls == [("o", "r", expected_branch)]) if expected_branch else calls == []
    assert snapshot == ("APERÇU" if expected_branch else "")


@pytest.mark.parametrize("resumed, reads", [
    ({"design": "x", "architecture": "y", "diagnostic": "z"}, False),   # plus rien à lire : tout est réutilisé
    ({"design": "x", "architecture": "y"}, True),                        # le Diagnostic va tourner
    ({"design": "x"}, True), (None, True),
])
def test_no_snapshot_is_read_when_a_resume_reuses_every_step_that_reads(monkeypatch, resumed, reads):
    calls = []
    monkeypatch.setattr(execution_context, "build_repo_snapshot", lambda *a: calls.append(a) or "APERÇU")
    asyncio.run(execution_context.prefetch_repo_snapshot(_data(target_workflow="DESIGN_AND_DEV"), "crewai/b", "main", True, False, resumed))
    assert bool(calls) is reads


def test_a_snapshot_failure_never_stops_the_execution(engine, monkeypatch, caplog):
    caplog.set_level("INFO", logger="myiacrew")
    def boom(*args):
        raise RuntimeError("GitHub en panne")

    monkeypatch.setattr(execution_context, "build_repo_snapshot", boom)
    seen: list[dict] = []
    _fake_crew(monkeypatch, None, seen)
    monkeypatch.setattr(execution_context, "get_branch_head_sha", lambda *a: None)
    execution_id, conversation_id = _new_execution(engine, "FEATURE")
    asyncio.run(execution.run_crew_and_persist(execution_id, conversation_id, _data(target_workflow="FEATURE"), True, False, "crewai/b", "main", "p", "c"))
    assert seen[0]["repo_snapshot"] == ""
    assert "aperçu du repository non lu" in caplog.text
    with Session(engine) as db:
        assert db.get(ExecutionHistory, execution_id).status == "success"


def test_the_crew_runs_inside_the_read_cache_and_receives_the_snapshot(engine, monkeypatch):
    monkeypatch.setattr(execution_context, "build_repo_snapshot", lambda *a: "APERÇU")
    seen: list[dict] = []
    in_cache: list[bool] = []

    async def run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        seen.append(inputs)
        in_cache.append(github_client._read_cache.get() is not None)
        return SimpleNamespace(raw="résultat")

    monkeypatch.setattr(execution, "AppDevelopmentCrew", type("C", (), {"run_dynamic_crew": run}))
    monkeypatch.setattr(execution_context, "get_branch_head_sha", lambda *a: None)
    execution_id, conversation_id = _new_execution(engine, "FEATURE")
    asyncio.run(execution.run_crew_and_persist(execution_id, conversation_id, _data(target_workflow="FEATURE"), True, False, "crewai/b", "main", "p", "c"))
    assert seen[0]["repo_snapshot"] == "APERÇU" and in_cache == [True]
    assert github_client._read_cache.get() is None   # le cache ne survit pas à l'exécution


def test_a_small_feature_skips_the_architecture_step_only_for_feature():
    from crew_workflow import workflow_step_keys
    assert workflow_step_keys("FEATURE") == ["architecture", "diagnostic", "development", "qa"]
    assert workflow_step_keys("FEATURE", "GRAND") == ["architecture", "diagnostic", "development", "qa"]
    assert workflow_step_keys("FEATURE", "PETIT") == ["diagnostic", "development", "qa"]
    for workflow in ("BUGFIX", "ANALYSE_ONLY", "DESIGN_AND_DEV"):
        assert workflow_step_keys(workflow, "PETIT") == workflow_step_keys(workflow)


def test_scope_is_kept_for_a_feature_and_dropped_for_any_other_workflow():
    assert _data(target_workflow="FEATURE", scope="PETIT").scope == "PETIT"
    assert _data(target_workflow="BUGFIX", scope="PETIT").scope is None
    assert _data(target_workflow="FEATURE").scope is None
    with pytest.raises(Exception):
        _data(target_workflow="FEATURE", scope="ENORME")


def test_snapshot_is_still_read_for_a_small_feature_because_the_diagnostic_runs(monkeypatch):
    calls = []
    monkeypatch.setattr(execution_context, "build_repo_snapshot", lambda *a: calls.append(a) or "APERÇU")
    asyncio.run(execution_context.prefetch_repo_snapshot(_data(target_workflow="FEATURE", scope="PETIT"), "b", "main", True, False))
    assert calls


def test_qualification_scope_defaults_to_the_safe_full_path():
    from qualification import AnalysisReport, _coerce_analysis_report
    base = dict(summary="s", request_type="FEATURE", confidence=0.9, is_clear=True)
    assert AnalysisReport(**base).scope == "GRAND"
    raw = {"summary": "s", "request_type": "FEATURE", "confidence": 0.9, "is_clear": True}
    assert _coerce_analysis_report({**raw, "scope": "petit"}).scope == "PETIT"
    assert _coerce_analysis_report({**raw, "scope": "?"}).scope == "GRAND"
    assert _coerce_analysis_report(raw).scope == "GRAND"


def test_crew_inputs_carry_the_previous_plan_with_an_empty_default():
    assert execution_context.crew_inputs(_data(), 1, "p", "c", "b", "main", True, False)["previous_plan"] == ""
    assert execution_context.crew_inputs(_data(), 1, "p", "c", "b", "main", True, False, "", "PLAN")["previous_plan"] == "PLAN"
