import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

import crewquestion as cq  # noqa: E402
import main  # noqa: E402
from database import Conversation, ExecutionCheckpoint, ExecutionHistory  # noqa: E402


def test_workflow_step_keys_are_the_single_source_of_the_pipeline():
    assert cq.workflow_step_keys("BUGFIX") == ["diagnostic", "development", "qa"]
    assert cq.workflow_step_keys("ANALYSE_ONLY") == ["design", "architecture"]
    assert cq.workflow_step_keys("INCONNU") == cq.WORKFLOW_STEP_KEYS["DESIGN_AND_DEV"]


@pytest.mark.parametrize("saved, expected", [
    ({"design": "d", "architecture": "a", "diagnostic": "x"}, ["design", "architecture", "diagnostic"]),
    ({"design": "d", "diagnostic": "x"}, ["design"]),  # trou : architecture manque, diagnostic non réutilisable
    ({"architecture": "a"}, []),                       # ne commence pas par la première étape
    ({"design": "  ", "architecture": "a"}, []),       # sortie vide ignorée
    ({"design": "d", "architecture": "a", "development": "dev"}, ["design", "architecture"]),  # development jamais reprise
])
def test_resumable_prefix_is_a_contiguous_prefix_of_resumable_steps(saved, expected):
    assert cq.resumable_prefix(cq.workflow_step_keys("DESIGN_AND_DEV"), saved) == expected


class _FakeCrew:
    """Remplace crewai.Crew : exécute chaque tâche reçue en appelant task_callback, sans LLM."""
    received: list[str] = []

    def __init__(self, agents, tasks, task_callback=None, **kwargs):
        self.tasks, self.task_callback = tasks, task_callback

    async def kickoff_async(self, inputs=None):
        for task in self.tasks:
            type(self).received.append(task.agent.role.strip())
            task.output = TaskOutputFactory(f"sortie de {task.agent.role.strip()}")
            self.task_callback(task.output)


def TaskOutputFactory(raw):
    from crewai.tasks.task_output import TaskOutput
    return TaskOutput(description="d", raw=raw, agent="x")


def _run(monkeypatch, resume_outputs, request_type="DESIGN_AND_DEV"):
    _FakeCrew.received = []
    monkeypatch.setattr(cq, "Crew", _FakeCrew)
    monkeypatch.setattr(cq.quota_mgr, "adaptive_pause", lambda *a, **k: None)
    persisted, steps = [], []

    async def go():
        crew = cq.AppDevelopmentCrew()

        async def fake_summary(*args, **kwargs):
            return "résumé"
        monkeypatch.setattr(crew, "_generate_summary", fake_summary, raising=False)
        try:
            await crew.run_dynamic_crew(
                inputs={"user_request": "x"}, request_type=request_type,
                on_step_change=steps.append,
                on_task_output_complete=lambda role, raw, duration: persisted.append((role, raw)),
                resume_outputs=resume_outputs,
            )
        except Exception as e:  # la fin (résumé, mise en forme) n'est pas l'objet de ce test
            return e
    asyncio.run(go())
    return persisted, steps


def test_resumed_steps_are_not_run_again_and_are_reported_as_persisted(monkeypatch):
    saved = {"design": "conception sauvegardée", "architecture": "architecture sauvegardée"}
    persisted, steps = _run(monkeypatch, saved)
    # Seules les étapes restantes passent par le crew…
    assert len(_FakeCrew.received) == 3 and not any("Designer" in r or "Architecte" in r for r in _FakeCrew.received)
    # …les étapes reprises sont déclarées terminées avec leur sortie d'origine, dans l'ordre…
    assert [raw for _, raw in persisted[:2]] == ["conception sauvegardée", "architecture sauvegardée"]
    # …et la progression repart à la première étape RESTANTE (pas à la première du workflow).
    assert steps[0] == "diagnostic"


def test_without_resume_outputs_every_step_runs(monkeypatch):
    persisted, steps = _run(monkeypatch, None)
    assert len(_FakeCrew.received) == 5 and steps[0] == "design"


def test_reused_output_becomes_the_context_of_the_next_step(monkeypatch):
    seen = {}

    class _Spy(_FakeCrew):
        async def kickoff_async(self, inputs=None):
            first = self.tasks[0]
            seen["context_raw"] = [getattr(t.output, "raw", None) for t in (first.context or [])]
            await super().kickoff_async(inputs)

    monkeypatch.setattr(cq, "Crew", _Spy)
    monkeypatch.setattr(cq.quota_mgr, "adaptive_pause", lambda *a, **k: None)

    async def go():
        crew = cq.AppDevelopmentCrew()
        try:
            await crew.run_dynamic_crew(
                inputs={"user_request": "x"}, request_type="FEATURE",
                resume_outputs={"architecture": "plan d'architecture sauvegardé"},
            )
        except Exception:
            pass
    asyncio.run(go())
    # diagnostic (1re étape restante) reçoit en contexte la sortie réutilisée de l'architecture.
    assert "plan d'architecture sauvegardé" in seen["context_raw"]


# --- Serveur : points de reprise et conditions de reprise ----------------------------------------

@pytest.fixture()
def engine(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(main, "engine", eng)
    return eng


def _failed_with_checkpoints(db, workflow="DESIGN_AND_DEV", user="u1", steps=("design", "architecture"), **kwargs):
    conversation = Conversation(user_id=user, title="t")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    entry = ExecutionHistory(user_request="x", workflow=workflow, status="failed", user_id=user, conversation_id=conversation.id, **kwargs)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    for step in steps:
        db.add(ExecutionCheckpoint(execution_id=entry.id, step=step, raw=f"sortie {step}"))
    db.commit()
    return conversation, entry


def _data(entry, **kwargs):
    params = dict(user_request="x", target_workflow="DESIGN_AND_DEV", resume_from_execution_id=entry.id)
    params.update(kwargs)
    return main.WorkflowExecutionInput(**params)


def test_completed_agent_persists_a_checkpoint_only_for_resumable_steps(engine):
    with Session(engine) as db:
        _, entry = _failed_with_checkpoints(db, steps=())
        entry.status = "running"
        db.add(entry)
        db.commit()
        entry_id = entry.id
    main._persist_completed_agent(entry_id, "Analyste Diagnostic Technique", "diag", 1.0)
    main._persist_completed_agent(entry_id, "Développeur", "dev", 1.0)
    with Session(engine) as db:
        steps = [c.step for c in db.exec(select(ExecutionCheckpoint)).all()]
    assert steps == ["diagnostic"]


def test_resumable_outputs_returns_the_reusable_prefix(engine):
    with Session(engine) as db:
        conversation, entry = _failed_with_checkpoints(db)
        assert main._resumable_outputs(db, _data(entry), "u1", conversation.id) == {
            "design": "sortie design", "architecture": "sortie architecture"}


@pytest.mark.parametrize("change", ["other_user", "other_workflow", "other_conversation", "still_running", "other_repo"])
def test_resume_is_refused_when_the_previous_execution_does_not_match(engine, change):
    with Session(engine) as db:
        conversation, entry = _failed_with_checkpoints(db)
        user, conv_id, data_kwargs = "u1", conversation.id, {}
        if change == "other_user":
            user = "u2"
        elif change == "other_workflow":
            data_kwargs["target_workflow"] = "FEATURE"
        elif change == "other_conversation":
            conv_id = conversation.id + 99
        elif change == "still_running":
            entry.status = "running"
            db.add(entry)
            db.commit()
        elif change == "other_repo":
            data_kwargs.update(repo_owner="o", repo_name="r")
        assert main._resumable_outputs(db, _data(entry, **data_kwargs), user, conv_id) == {}


def test_no_resume_id_means_no_resume(engine):
    with Session(engine) as db:
        conversation, entry = _failed_with_checkpoints(db)
        data = _data(entry, resume_from_execution_id=None)
        assert main._resumable_outputs(db, data, "u1", conversation.id) == {}


def test_deleting_an_execution_deletes_its_checkpoints(engine):
    with Session(engine) as db:
        conversation, entry = _failed_with_checkpoints(db)
        asyncio.run(main.delete_history_entry(execution_id=entry.id, session=db, user={"id": "u1"}))
        assert not db.exec(select(ExecutionCheckpoint)).all()


# --- Seconde tentative automatique ----------------------------------------------------------------

DESIGNER = "Lead Product / Game Designer"


def _step_error(index, role, message="503 UNAVAILABLE: high demand", total=5):
    return cq.CrewStepError(index, total, role, RuntimeError(message))


def _launch_with(engine, monkeypatch, fake_run, request_type="DESIGN_AND_DEV", resume_outputs=None):
    class FakeCrew:
        run_dynamic_crew = fake_run

    monkeypatch.setattr(main, "AppDevelopmentCrew", FakeCrew)
    with Session(engine) as db:
        conversation = Conversation(user_id="u1", title="t")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        entry = ExecutionHistory(user_request="x", workflow=request_type, status="running", user_id="u1", conversation_id=conversation.id)
        db.add(entry)
        db.commit()
        db.refresh(entry)
        ids = (entry.id, conversation.id)
    data = main.WorkflowExecutionInput(user_request="x", target_workflow=request_type)
    asyncio.run(main._execute_crew_and_persist(
        ids[0], ids[1], data, False, False, "", None, "prompt", "ctx", resume_outputs))
    return ids[0]


def _saved(engine, execution_id):
    with Session(engine) as db:
        return db.get(ExecutionHistory, execution_id)


def test_transient_failure_in_an_early_step_is_retried_once_reusing_saved_steps(engine, monkeypatch):
    calls = []

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        calls.append(dict(resume_outputs or {}))
        if len(calls) == 1:
            on_task_output_complete(DESIGNER, "conception", 1.0)
            raise _step_error(2, "Architecte Logiciel React / TypeScript")
        return type("R", (), {"raw": "résultat final"})()

    saved = _saved(engine, _launch_with(engine, monkeypatch, fake_run))
    assert len(calls) == 2
    assert calls[0] == {}
    assert calls[1] == {"design": "conception"}  # l'étape réussie est reprise, pas rejouée
    assert saved.status == "success" and saved.error_code is None


def test_second_failure_is_final_and_never_retried_again(engine, monkeypatch):
    calls = []

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        calls.append(1)
        raise _step_error(1, DESIGNER)

    saved = _saved(engine, _launch_with(engine, monkeypatch, fake_run))
    assert len(calls) == 2 and saved.status == "failed" and saved.error_code == "LLM_UNAVAILABLE"


@pytest.mark.parametrize("error", [
    _step_error(4, "Développeur Fullstack React / TypeScript"),        # développement : écrit sur GitHub
    _step_error(5, "QA Engineer / Automated Tester"),                  # QA : dépend du développement
    _step_error(5, "finalisation du résultat"),                        # après toutes les étapes
    RuntimeError("503 UNAVAILABLE après le crew (vérification GitHub)"),  # hors étape
])
def test_failures_from_development_on_are_never_retried_automatically(engine, monkeypatch, error):
    calls = []

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        calls.append(1)
        raise error

    saved = _saved(engine, _launch_with(engine, monkeypatch, fake_run))
    assert len(calls) == 1 and saved.status == "failed"


def test_non_transient_failure_is_not_retried(engine, monkeypatch):
    calls = []

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        calls.append(1)
        raise _step_error(1, DESIGNER, "bug interne inattendu")

    saved = _saved(engine, _launch_with(engine, monkeypatch, fake_run))
    assert len(calls) == 1 and saved.status == "failed" and saved.error_code == "INTERNAL_ERROR"


def test_the_wait_before_the_second_attempt_does_not_hold_an_execution_slot(engine, monkeypatch):
    seen = []
    original = main._persist_current_step

    def spy(execution_id, step_key):
        if step_key == "queued":
            seen.append(main._execution_semaphore._value)
        return original(execution_id, step_key)

    monkeypatch.setattr(main, "_persist_current_step", spy)
    calls = []

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        calls.append(1)
        if len(calls) == 1:
            raise _step_error(1, DESIGNER)
        return type("R", (), {"raw": "ok"})()

    _launch_with(engine, monkeypatch, fake_run)
    assert len(calls) == 2
    # "queued" est écrit avant la 1re acquisition ET avant la 2de : à ces deux instants, aucun emplacement pris.
    assert seen == [main._MAX_CONCURRENT_EXECUTIONS, main._MAX_CONCURRENT_EXECUTIONS]


def test_checkpoints_are_deleted_when_the_execution_succeeds(engine, monkeypatch):
    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        on_task_output_complete(DESIGNER, "conception", 1.0)
        return type("R", (), {"raw": "ok"})()

    execution_id = _launch_with(engine, monkeypatch, fake_run)
    with Session(engine) as db:
        assert db.get(ExecutionHistory, execution_id).status == "success"
        assert not db.exec(select(ExecutionCheckpoint)).all()


def test_resume_is_refused_when_the_request_text_differs(engine):
    with Session(engine) as db:
        conversation, entry = _failed_with_checkpoints(db)
        assert main._resumable_outputs(db, _data(entry, user_request="une AUTRE demande"), "u1", conversation.id) == {}
        assert main._resumable_outputs(db, _data(entry, user_request="  x  "), "u1", conversation.id) != {}
