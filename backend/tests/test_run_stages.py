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


@pytest.fixture(autouse=True)
def no_network_snapshot(monkeypatch):
    # L'aperçu du repo lit GitHub : jamais de réseau dans ces tests (les tests dédiés le remplacent).
    monkeypatch.setattr(main, "build_repo_snapshot", lambda *a: "")


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
    assert "(détail : 429 quota" in detail and len(detail) < 800 + 3800
    # Régression : le rapport de l'agent joint à un échec de livraison ne doit pas être coupé à 500 caractères.
    # Même avec un constat très long, la fin du rapport (3000 caractères au plus) reste.
    issue = SimpleNamespace(message="m" * 600, likely_access_problem=False)
    report = "RAPPORT-FINAL " + "r" * 2900 + " FIN-DU-RAPPORT"
    delivery = main.DeliveryError(main._delivery_failure_message(issue, report))
    info = classify_exception(delivery)
    kept = main._failure_detail(delivery, info)
    assert info.code == "DELIVERY_FAILED" and "Rapport de l'agent" in kept and "FIN-DU-RAPPORT" in kept
    assert kept.startswith("Un repository GitHub cible")
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


# --- Orchestration : état d'une tentative, relance, succès, démarrage ------------------------------

from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

from database import Conversation, ExecutionCheckpoint, ExecutionHistory  # noqa: E402


@pytest.fixture()
def engine(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(main, "engine", eng)
    return eng


def _new_execution(engine, workflow="BUGFIX"):
    with Session(engine) as db:
        conversation = Conversation(user_id="u1", title="t")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        entry = ExecutionHistory(user_request="x", workflow=workflow, status="running", user_id="u1", conversation_id=conversation.id)
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry.id, conversation.id


def _fake_crew(monkeypatch, outcome, seen):
    async def run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        seen.append(inputs)
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(raw="résultat")

    class FakeCrew:
        run_dynamic_crew = run

    monkeypatch.setattr(main, "AppDevelopmentCrew", FakeCrew)


def test_analysis_with_a_repo_target_still_learns_that_the_work_branch_exists(engine, monkeypatch):
    # Régression : le SHA n'était capturé que pour les runs qui vérifient la livraison, donc ANALYSE_ONLY
    # disait aux agents que la branche « n'existe pas encore » même quand elle existait.
    seen: list[dict] = []
    _fake_crew(monkeypatch, None, seen)
    monkeypatch.setattr(main, "get_branch_head_sha", lambda *a: "abc123")
    execution_id, conversation_id = _new_execution(engine, "ANALYSE_ONLY")
    data = _data(target_workflow="ANALYSE_ONLY")
    asyncio.run(main._run_crew_and_persist(execution_id, conversation_id, data, True, False, "crewai/b", "main", "p", "c"))
    assert "EXISTE DÉJÀ" in seen[0]["repo_instructions"]
    with Session(engine) as db:
        assert db.get(ExecutionHistory, execution_id).status == "success"


def test_success_persistence_error_never_turns_a_delivered_run_into_a_failure(engine, monkeypatch):
    seen: list[dict] = []
    _fake_crew(monkeypatch, None, seen)
    real = main._persist_success
    calls = []

    async def flaky(session, db_entry, conversation, raw_result, state):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("connexion coupée par le pooler")
        await real(session, db_entry, conversation, raw_result, state)

    monkeypatch.setattr(main, "_persist_success", flaky)
    execution_id, conversation_id = _new_execution(engine)
    asyncio.run(main._run_crew_and_persist(execution_id, conversation_id, _data(repo_owner=None, repo_name=None), False, False, "", None, "p", "c"))
    with Session(engine) as db:
        saved = db.get(ExecutionHistory, execution_id)
    assert len(calls) == 2  # une reprise sur Session neuve
    assert saved.status == "success" and saved.error_code is None


def test_when_success_persistence_fails_twice_the_row_is_not_declared_failed(engine, monkeypatch, capsys):
    seen: list[dict] = []
    _fake_crew(monkeypatch, None, seen)

    async def broken(*args, **kwargs):
        raise RuntimeError("base injoignable")

    monkeypatch.setattr(main, "_persist_success", broken)
    execution_id, conversation_id = _new_execution(engine)
    asyncio.run(main._run_crew_and_persist(execution_id, conversation_id, _data(repo_owner=None, repo_name=None), False, False, "", None, "p", "c"))
    with Session(engine) as db:
        assert db.get(ExecutionHistory, execution_id).status == "running"  # libérée plus tard par le balayage
    assert "succès non enregistré" in capsys.readouterr().out


def test_persist_success_records_result_metrics_and_clears_the_step(engine, monkeypatch):
    execution_id, conversation_id = _new_execution(engine)
    with Session(engine) as db:
        db.add(ExecutionCheckpoint(execution_id=execution_id, step="design", raw="d"))
        db.commit()
        entry, conversation = db.get(ExecutionHistory, execution_id), db.get(Conversation, conversation_id)
        entry.current_step = "qa"
        state = main._RunState(metrics=SimpleNamespace(api_calls_count=7, rate_limit_hits=1, total_wait_time=3.5))
        monkeypatch.setattr(main, "_persist_agent_runs", lambda *a: None)
        asyncio.run(main._persist_success(db, entry, conversation, "texte final", state))
    with Session(engine) as db:
        saved = db.get(ExecutionHistory, execution_id)
        assert (saved.status, saved.result, saved.current_step) == ("success", "texte final", None)
        assert (saved.api_calls_count, saved.rate_limit_hits, saved.total_wait_time_seconds) == (7, 1, 3.5)
        assert not db.exec(select(ExecutionCheckpoint)).all()  # points de reprise purgés après le succès


def test_run_crew_keeps_the_metrics_and_stops_the_memory_ticker_when_the_crew_crashes(monkeypatch):
    state = main._RunState()
    ticker = {"cancelled": False}

    async def endless_ticker(label):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            ticker["cancelled"] = True
            raise

    async def crash(inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        raise RuntimeError("le crew plante en route")

    monkeypatch.setattr(main, "_periodic_memory_logger", endless_ticker)
    crew = SimpleNamespace(run_dynamic_crew=crash)
    with pytest.raises(RuntimeError, match="plante en route"):
        asyncio.run(main._run_crew(crew, state, 1, "BUGFIX", {}, None))
    assert state.metrics is not None  # le chemin d'échec peut encore lire les métriques de la tentative
    assert ticker["cancelled"] is True


def _step_error(index, message="503 UNAVAILABLE"):
    return CrewStepError(index, 5, "Designer", RuntimeError(message))


@pytest.mark.parametrize("scenario, expected", [
    ("transient_early_with_checkpoints", {"design": "d"}),
    ("not_a_step_error", None),
    ("auto_retry_not_allowed", None),
    ("not_retryable", None),
    ("missing_checkpoint_for_a_finished_step", None),
    ("failure_at_development", None),
])
def test_retry_decision(monkeypatch, scenario, expected):
    saved = {"design": "d"}
    error = _step_error(2)
    allowed = True
    if scenario == "not_a_step_error":
        error = RuntimeError("503 UNAVAILABLE")
    elif scenario == "auto_retry_not_allowed":
        allowed = False
    elif scenario == "not_retryable":
        error = _step_error(2, "bug interne")
    elif scenario == "missing_checkpoint_for_a_finished_step":
        error, saved = _step_error(3), {"design": "d"}  # étape 3 en échec mais l'étape 2 n'a pas de sauvegarde
    elif scenario == "failure_at_development":
        error = _step_error(4)
    monkeypatch.setattr(main, "_load_checkpoints_for", lambda execution_id: saved)
    data = _data(target_workflow="DESIGN_AND_DEV")
    result = asyncio.run(main._retry_outputs_if_transient(error, classify_exception(error), 1, data, allowed))
    assert result == expected


def test_mark_startup_failure_only_touches_a_running_row(engine):
    execution_id, _ = _new_execution(engine)
    main._mark_startup_failure(execution_id, RuntimeError("pool épuisé"))
    with Session(engine) as db:
        saved = db.get(ExecutionHistory, execution_id)
        assert (saved.status, saved.error_code, saved.error_retryable) == ("failed", "INTERNAL_ERROR", False)
        assert "pool épuisé" in saved.result and saved.current_step is None
        saved.status, saved.result = "success", "déjà livré"
        db.add(saved)
        db.commit()
    main._mark_startup_failure(execution_id, RuntimeError("autre"))
    with Session(engine) as db:
        assert db.get(ExecutionHistory, execution_id).result == "déjà livré"  # jamais écrasée


def test_crew_inputs_carry_the_repo_snapshot_with_an_empty_default():
    assert main._crew_inputs(_data(), 1, "p", "c", "b", "main", True, False)["repo_snapshot"] == ""
    assert main._crew_inputs(_data(), 1, "p", "c", "b", "main", True, False, "APERÇU")["repo_snapshot"] == "APERÇU"


@pytest.mark.parametrize("workflow, has_repo, branch_exists, expected_branch", [
    ("FEATURE", True, False, "main"),
    ("DESIGN_AND_DEV", True, True, "crewai/b"),
    ("ANALYSE_ONLY", True, False, "main"),
    ("BUGFIX", True, False, "main"),               # le Diagnostic lit aussi l'aperçu
    ("FEATURE", False, False, None),               # pas de repository cible
])
def test_snapshot_is_prefetched_only_with_a_repo_and_an_architecture_step(monkeypatch, workflow, has_repo, branch_exists, expected_branch):
    calls = []
    monkeypatch.setattr(main, "build_repo_snapshot", lambda owner, repo, branch: calls.append((owner, repo, branch)) or "APERÇU")
    snapshot = asyncio.run(main._prefetch_repo_snapshot(
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
    monkeypatch.setattr(main, "build_repo_snapshot", lambda *a: calls.append(a) or "APERÇU")
    asyncio.run(main._prefetch_repo_snapshot(_data(target_workflow="DESIGN_AND_DEV"), "crewai/b", "main", True, False, resumed))
    assert bool(calls) is reads


def test_a_snapshot_failure_never_stops_the_execution(engine, monkeypatch, capsys):
    def boom(*args):
        raise RuntimeError("GitHub en panne")

    monkeypatch.setattr(main, "build_repo_snapshot", boom)
    seen: list[dict] = []
    _fake_crew(monkeypatch, None, seen)
    monkeypatch.setattr(main, "get_branch_head_sha", lambda *a: None)
    execution_id, conversation_id = _new_execution(engine, "FEATURE")
    asyncio.run(main._run_crew_and_persist(execution_id, conversation_id, _data(target_workflow="FEATURE"), True, False, "crewai/b", "main", "p", "c"))
    assert seen[0]["repo_snapshot"] == ""
    assert "aperçu du repository non lu" in capsys.readouterr().out
    with Session(engine) as db:
        assert db.get(ExecutionHistory, execution_id).status == "success"


def test_the_crew_runs_inside_the_read_cache_and_receives_the_snapshot(engine, monkeypatch):
    import github_tools
    monkeypatch.setattr(main, "build_repo_snapshot", lambda *a: "APERÇU")
    seen: list[dict] = []
    in_cache: list[bool] = []

    async def run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        seen.append(inputs)
        in_cache.append(github_tools._read_cache.get() is not None)
        return SimpleNamespace(raw="résultat")

    monkeypatch.setattr(main, "AppDevelopmentCrew", type("C", (), {"run_dynamic_crew": run}))
    monkeypatch.setattr(main, "get_branch_head_sha", lambda *a: None)
    execution_id, conversation_id = _new_execution(engine, "FEATURE")
    asyncio.run(main._run_crew_and_persist(execution_id, conversation_id, _data(target_workflow="FEATURE"), True, False, "crewai/b", "main", "p", "c"))
    assert seen[0]["repo_snapshot"] == "APERÇU" and in_cache == [True]
    assert github_tools._read_cache.get() is None   # le cache ne survit pas à l'exécution


# --- Petite FEATURE : architecture sautée -----------------------------------------------------------

def test_a_small_feature_skips_the_architecture_step_only_for_feature():
    from crewquestion import workflow_step_keys
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
    monkeypatch.setattr(main, "build_repo_snapshot", lambda *a: calls.append(a) or "APERÇU")
    asyncio.run(main._prefetch_repo_snapshot(_data(target_workflow="FEATURE", scope="PETIT"), "b", "main", True, False))
    assert calls


def test_a_small_feature_failure_is_judged_against_its_own_steps():
    # Sans l'architecture, l'étape 1 est le Diagnostic (reprenable) et l'étape 2 le développement (jamais rejoué).
    before_dev = main._failed_before_development(CrewStepError(1, 3, "Analyste", RuntimeError("x")), "FEATURE", "PETIT")
    in_dev = main._failed_before_development(CrewStepError(2, 3, "Développeur", RuntimeError("x")), "FEATURE", "PETIT")
    assert before_dev is True and in_dev is False
    # Même rang d'étape dans le parcours complet : c'est l'architecture (reprenable) pour 1, le diagnostic pour 2.
    assert main._failed_before_development(CrewStepError(2, 4, "Analyste", RuntimeError("x")), "FEATURE") is True


def test_qualification_scope_defaults_to_the_safe_full_path():
    from crewquestion import AnalysisReport, _coerce_analysis_report
    base = dict(summary="s", request_type="FEATURE", confidence=0.9, is_clear=True)
    assert AnalysisReport(**base).scope == "GRAND"
    raw = {"summary": "s", "request_type": "FEATURE", "confidence": 0.9, "is_clear": True}
    assert _coerce_analysis_report({**raw, "scope": "petit"}).scope == "PETIT"
    assert _coerce_analysis_report({**raw, "scope": "?"}).scope == "GRAND"
    assert _coerce_analysis_report(raw).scope == "GRAND"


# --- Plan du tour précédent ---------------------------------------------------------------------------

def _plan_result(plan="## Cible\nUn panier", extra=""):
    return (
        "## Architecte Logiciel React / TypeScript\n<!--agent-duration:12.50-->\n\n" + plan
        + "\n\n---\n\n## Analyste Diagnostic Technique\n\ncode" + extra
    )


def _history(engine, conversation_id, result, status="success", workflow="FEATURE", scope=None, owner="o", repo="r"):
    with Session(engine) as db:
        entry = ExecutionHistory(
            user_request="x", workflow=workflow, status=status, user_id="u1", conversation_id=conversation_id,
            repo_owner=owner, repo_name=repo, result=result, scope=scope,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry.id


def _plan(engine, conversation_id, current_id, data=None, has_repo=True):
    with Session(engine) as db:
        return main._previous_architecture_plan(db, data or _data(target_workflow="FEATURE"), has_repo, "u1", conversation_id, current_id)


def test_previous_plan_is_the_architect_section_of_the_last_successful_turn(engine):
    _, conversation_id = _new_execution(engine, "FEATURE")
    _history(engine, conversation_id, _plan_result("## Cible\nVieux plan"))
    _history(engine, conversation_id, _plan_result("## Cible\nPlan récent"))
    current = _history(engine, conversation_id, None, status="running")
    plan = _plan(engine, conversation_id, current)
    assert plan == "## Cible\nPlan récent" and "agent-duration" not in plan


@pytest.mark.parametrize("kwargs", [
    dict(status="failed"),                        # tour en échec : pas une base fiable
    dict(workflow="BUGFIX"),                       # aucune étape d'architecture
    dict(workflow="FEATURE", scope="PETIT"),       # architecture sautée à ce tour-là
    dict(owner="autre"),                           # autre repository
])
def test_previous_plan_ignores_turns_that_cannot_provide_one(engine, kwargs):
    _, conversation_id = _new_execution(engine, "FEATURE")
    _history(engine, conversation_id, _plan_result(), **kwargs)
    assert _plan(engine, conversation_id, _history(engine, conversation_id, None, status="running")) == ""


def test_previous_plan_is_capped_and_scoped_to_the_conversation(engine):
    _, conversation_id = _new_execution(engine, "FEATURE")
    _, other_conversation = _new_execution(engine, "FEATURE")
    _history(engine, other_conversation, _plan_result("## Cible\nAutre conversation"))
    assert _plan(engine, conversation_id, 0) == ""
    _history(engine, conversation_id, _plan_result("x" * (main.MAX_PREVIOUS_PLAN_CHARS + 500)))
    plan = _plan(engine, conversation_id, 0)
    assert plan.endswith("[… tronqué]") and len(plan) < main.MAX_PREVIOUS_PLAN_CHARS + 30


def test_previous_plan_is_not_loaded_when_the_architecture_will_not_run(engine, monkeypatch):
    _, conversation_id = _new_execution(engine, "FEATURE")
    _history(engine, conversation_id, _plan_result())
    load = lambda data, resumed=None: asyncio.run(main._load_previous_plan(data, True, "u1", conversation_id, 0, resumed))  # noqa: E731
    assert load(_data(target_workflow="FEATURE")) != ""
    assert load(_data(target_workflow="FEATURE", scope="PETIT")) == ""        # architecture sautée
    assert load(_data(target_workflow="BUGFIX")) == ""
    assert load(_data(target_workflow="FEATURE"), {"architecture": "y"}) == ""  # réutilisée par une reprise


def test_crew_inputs_carry_the_previous_plan_with_an_empty_default():
    assert main._crew_inputs(_data(), 1, "p", "c", "b", "main", True, False)["previous_plan"] == ""
    assert main._crew_inputs(_data(), 1, "p", "c", "b", "main", True, False, "", "PLAN")["previous_plan"] == "PLAN"


# --- Périmètre d'écriture GitHub de l'exécution -------------------------------------------------------------------

def test_the_crew_runs_with_its_write_scope_limited_to_the_work_branch(engine, monkeypatch):
    import github_tools
    seen_scopes: list = []

    async def run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None, **kwargs):
        seen_scopes.append(github_tools._write_scope.get())
        return SimpleNamespace(raw="résultat")

    monkeypatch.setattr(main, "AppDevelopmentCrew", type("C", (), {"run_dynamic_crew": run}))
    monkeypatch.setattr(main, "get_branch_head_sha", lambda *a: None)
    execution_id, conversation_id = _new_execution(engine, "FEATURE")
    asyncio.run(main._run_crew_and_persist(execution_id, conversation_id, _data(target_workflow="FEATURE"), True, False, "crewai/feature-ab12cd34", "main", "p", "c"))
    assert seen_scopes == ["crewai/feature-ab12cd34"]
    assert github_tools._write_scope.get() is None   # jamais conservé après l'exécution


def test_work_branches_are_named_with_the_shared_prefix():
    import github_tools
    assert main.WORK_BRANCH_PREFIX == github_tools.WORK_BRANCH_PREFIX == "crewai/"
