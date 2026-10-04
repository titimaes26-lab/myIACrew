"""Reprise côté serveur : points de reprise enregistrés, conditions pour qu'une reprise soit acceptée ou refusée."""
# ruff: noqa: F811  (les fixtures importées de resume_support sont reprises comme arguments des tests)
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

import routes_history  # noqa: E402
import execution_resume  # noqa: E402
import execution_persistence  # noqa: E402
from database import ExecutionCheckpoint  # noqa: E402
from resume_support import engine, _data, _failed_with_checkpoints  # noqa: E402,F401,F811  (fixtures reprises par nom)


def test_completed_agent_persists_a_checkpoint_only_for_resumable_steps(engine):
    with Session(engine) as db:
        _, entry = _failed_with_checkpoints(db, steps=())
        entry.status = "running"
        db.add(entry)
        db.commit()
        entry_id = entry.id
    execution_persistence.persist_completed_agent(entry_id, "Analyste Diagnostic Technique", "diag", 1.0)
    execution_persistence.persist_completed_agent(entry_id, "Développeur", "dev", 1.0)
    with Session(engine) as db:
        steps = [c.step for c in db.exec(select(ExecutionCheckpoint)).all()]
    assert steps == ["diagnostic"]


def test_resumable_outputs_returns_the_reusable_prefix(engine):
    with Session(engine) as db:
        conversation, entry = _failed_with_checkpoints(db)
        assert execution_resume.resumable_outputs(db, _data(entry), "u1", conversation.id) == {
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
        assert execution_resume.resumable_outputs(db, _data(entry, **data_kwargs), user, conv_id) == {}


def test_resume_is_refused_when_the_scope_differs(engine):
    with Session(engine) as db:
        conversation, entry = _failed_with_checkpoints(db, workflow="FEATURE", steps=("diagnostic",), scope="PETIT")
        same = _data(entry, target_workflow="FEATURE", scope="PETIT")
        other = _data(entry, target_workflow="FEATURE", scope="GRAND")
        assert execution_resume.resumable_outputs(db, same, "u1", conversation.id) == {"diagnostic": "sortie diagnostic"}
        assert execution_resume.resumable_outputs(db, other, "u1", conversation.id) == {}


@pytest.mark.parametrize("previous_scope, new_scope, allowed", [
    (None, "GRAND", True),      # ancienne ligne ou clarification, relancée en détection automatique
    ("GRAND", None, True),
    (None, None, True),
    ("PETIT", "PETIT", True),
    ("PETIT", "GRAND", False),  # les étapes diffèrent : rien à réutiliser
    (None, "PETIT", False),
])
def test_resume_only_distinguishes_a_small_feature_from_the_full_path(engine, previous_scope, new_scope, allowed):
    with Session(engine) as db:
        conversation, entry = _failed_with_checkpoints(db, workflow="FEATURE", steps=("architecture", "diagnostic"), scope=previous_scope)
        data = _data(entry, target_workflow="FEATURE", scope=new_scope)
        saved = execution_resume.resumable_outputs(db, data, "u1", conversation.id)
    assert bool(saved) is allowed


def test_no_resume_id_means_no_resume(engine):
    with Session(engine) as db:
        conversation, entry = _failed_with_checkpoints(db)
        data = _data(entry, resume_from_execution_id=None)
        assert execution_resume.resumable_outputs(db, data, "u1", conversation.id) == {}


def test_deleting_an_execution_deletes_its_checkpoints(engine):
    with Session(engine) as db:
        conversation, entry = _failed_with_checkpoints(db)
        routes_history.delete_history_entry(execution_id=entry.id, session=db, user={"id": "u1"})
        assert not db.exec(select(ExecutionCheckpoint)).all()


def test_resume_is_refused_when_the_request_text_differs(engine):
    with Session(engine) as db:
        conversation, entry = _failed_with_checkpoints(db)
        assert execution_resume.resumable_outputs(db, _data(entry, user_request="une AUTRE demande"), "u1", conversation.id) == {}
        assert execution_resume.resumable_outputs(db, _data(entry, user_request="  x  "), "u1", conversation.id) != {}


def test_resume_is_refused_when_the_clarifications_differ(engine):
    with Session(engine) as db:
        conversation, entry = _failed_with_checkpoints(db, clarifications="en mode sombre")
        assert execution_resume.resumable_outputs(db, _data(entry, clarifications="en mode clair"), "u1", conversation.id) == {}
        assert execution_resume.resumable_outputs(db, _data(entry, clarifications="en mode sombre"), "u1", conversation.id) != {}
