"""Contexte tiré de la conversation : plan du tour précédent, tours précédents lus à la demande, branche de travail réutilisée."""
# ruff: noqa: F811  (les fixtures importées de run_support sont reprises comme arguments des tests)
"""Étapes extraites de _run_crew_and_persist : fonctions pures ou presque, testées une à une."""
import asyncio
from datetime import datetime, timedelta, timezone
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402

import execution_context  # noqa: E402
from sqlmodel import Session, select  # noqa: E402
from database import Conversation, ExecutionHistory  # noqa: E402
from run_support import no_network_snapshot, _data, engine, _new_execution  # noqa: E402,F401,F811  (fixtures reprises par nom)


def _plan_result(plan="## Cible\nUn panier", extra=""):
    return (
        "## Architecte Logiciel React / TypeScript\n<!--agent-duration:12.50-->\n\n" + plan
        + "\n\n---\n\n## Analyste Diagnostic Technique\n\ncode" + extra
    )


def _history(engine, conversation_id, result, status="success", workflow="FEATURE", scope=None, owner="o", repo="r"):
    with Session(engine) as db:
        if status == "running":
            # Une seule exécution « running » par conversation (index unique partiel) : celle créée par
            # _new_execution laisse la place au tour courant simulé ici.
            for earlier in db.exec(select(ExecutionHistory).where(
                ExecutionHistory.conversation_id == conversation_id, ExecutionHistory.status == "running",
            )).all():
                earlier.status = "success"
            db.commit()
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
        return execution_context.previous_architecture_plan(db, data or _data(target_workflow="FEATURE"), has_repo, "u1", conversation_id, current_id)


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
    _history(engine, conversation_id, _plan_result("x" * (execution_context.MAX_PREVIOUS_PLAN_CHARS + 500)))
    plan = _plan(engine, conversation_id, 0)
    assert plan.endswith("[… tronqué]") and len(plan) < execution_context.MAX_PREVIOUS_PLAN_CHARS + 30


def test_previous_plan_is_not_loaded_when_the_architecture_will_not_run(engine, monkeypatch):
    _, conversation_id = _new_execution(engine, "FEATURE")
    _history(engine, conversation_id, _plan_result())
    load = lambda data, resumed=None: asyncio.run(execution_context.load_previous_plan(data, True, "u1", conversation_id, 0, resumed))  # noqa: E731
    assert load(_data(target_workflow="FEATURE")) != ""
    assert load(_data(target_workflow="FEATURE", scope="PETIT")) == ""        # architecture sautée
    assert load(_data(target_workflow="BUGFIX")) == ""
    assert load(_data(target_workflow="FEATURE"), {"architecture": "y"}) == ""  # réutilisée par une reprise


def _turns(engine, count, **overrides):
    """`count` tours d'une même conversation, du plus ancien au plus récent ; renvoie (conversation_id, ids)."""
    with Session(engine) as db:
        conversation = Conversation(user_id="u1", title="t")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        ids = []
        for index in range(count):
            fields = dict(
                user_request=f"demande {index}", workflow="FEATURE", status="success", user_id="u1",
                conversation_id=conversation.id, result=f"## QA\n\nrésumé {index}",
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index),
            )
            fields.update(overrides)
            entry = ExecutionHistory(**fields)
            db.add(entry)
            db.commit()
            db.refresh(entry)
            ids.append(entry.id)
        return conversation.id, ids


def test_prior_turns_reads_only_the_recent_turns_in_full_and_reports_the_omitted_ones(engine, monkeypatch):
    conversation_id, _ = _turns(engine, 14)
    seen = {}
    real = execution_context.build_conversation_context
    monkeypatch.setattr(execution_context, "build_conversation_context", lambda entries, total_count=None: (
        seen.update(count=len(entries), total=total_count, first=entries[0].user_request, last=entries[-1].user_request)
        or real(entries, total_count=total_count)))
    with Session(engine) as db:
        running, context = execution_context.prior_turns(db, conversation_id)
    assert running == []
    assert seen == {"count": execution_context.MAX_PRIOR_TURNS_IN_CONTEXT, "total": 14, "first": "demande 4", "last": "demande 13"}
    assert "[4 tour(s) plus ancien(s) omis" in context and "demande 13" in context and "demande 3" not in context


def test_prior_turns_context_is_identical_to_reading_the_whole_conversation(engine):
    conversation_id, _ = _turns(engine, 14)
    with Session(engine) as db:
        _, context = execution_context.prior_turns(db, conversation_id)
        everything = db.exec(select(ExecutionHistory).where(ExecutionHistory.conversation_id == conversation_id)
                             .order_by(ExecutionHistory.created_at)).all()
        assert context == execution_context.build_conversation_context(everything)


def test_prior_turns_lists_the_running_ones_and_handles_an_empty_conversation(engine):
    conversation_id, ids = _turns(engine, 3)
    with Session(engine) as db:
        entry = db.get(ExecutionHistory, ids[1])
        entry.status = "running"
        db.add(entry)
        db.commit()
        assert execution_context.prior_turns(db, conversation_id)[0] == [ids[1]]
        assert execution_context.prior_turns(db, 99999) == ([], "Aucun échange précédent dans cette conversation.")


def test_previous_work_branch_is_the_latest_one_for_the_same_repository_and_base(engine):
    conversation_id, ids = _turns(engine, 4, repo_owner="o", repo_name="r", base_branch="main")
    with Session(engine) as db:
        for entry_id, branch in zip(ids, ["crewai/a", "crewai/b", None, ""]):
            entry = db.get(ExecutionHistory, entry_id)
            entry.work_branch = branch
            db.add(entry)
        db.commit()
        assert execution_context.previous_work_branch(db, conversation_id, "o", "r", "main") == "crewai/b"
        assert execution_context.previous_work_branch(db, conversation_id, "autre", "r", "main") == ""
        assert execution_context.previous_work_branch(db, conversation_id, "o", "r", "develop") == ""
        assert execution_context.previous_work_branch(db, conversation_id, None, None, None) == ""
