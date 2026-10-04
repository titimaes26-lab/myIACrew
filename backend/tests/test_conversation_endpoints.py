"""Lecture d'une conversation : derniers tours bornés, curseur `before_id`, dépôts récents agrégés en SQL."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import routes_conversations
from database import Conversation, ExecutionHistory

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _conversation(session, user="u1"):
    conversation = Conversation(user_id=user, title="t")
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


def _turn(session, conversation_id, minutes, user="u1", owner=None, name=None, base=None, request="r"):
    entry = ExecutionHistory(
        user_request=request, workflow="BUGFIX", status="success", user_id=user, conversation_id=conversation_id,
        repo_owner=owner, repo_name=name, base_branch=base, created_at=NOW + timedelta(minutes=minutes),
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _messages(session, conversation_id, user="u1", **params):
    return routes_conversations.get_conversation_messages(conversation_id, session=session, user={"id": user}, **params)


def test_messages_default_to_the_whole_conversation_in_chronological_order(session):
    conversation = _conversation(session)
    ids = [_turn(session, conversation.id, minutes).id for minutes in (5, 1, 3)]   # insérés dans le désordre
    assert [m.id for m in _messages(session, conversation.id)] == [ids[1], ids[2], ids[0]]


def test_messages_keep_only_the_most_recent_turns_but_still_in_chronological_order(session):
    conversation = _conversation(session)
    ids = [_turn(session, conversation.id, minutes).id for minutes in range(10)]
    assert [m.id for m in _messages(session, conversation.id, limit=3)] == ids[-3:]


def test_the_message_limit_is_clamped_between_one_and_the_maximum(session, monkeypatch):
    conversation = _conversation(session)
    for minutes in range(5):
        _turn(session, conversation.id, minutes)
    monkeypatch.setattr(routes_conversations, "MESSAGES_MAX_LIMIT", 3)
    assert len(_messages(session, conversation.id, limit=0)) == 1 and len(_messages(session, conversation.id, limit=-5)) == 1
    assert len(_messages(session, conversation.id, limit=10_000)) == 3


def test_before_id_walks_back_through_older_turns(session):
    conversation = _conversation(session)
    ids = [_turn(session, conversation.id, minutes).id for minutes in range(6)]
    older = _messages(session, conversation.id, limit=2, before_id=ids[4])
    assert [m.id for m in older] == [ids[2], ids[3]]
    assert _messages(session, conversation.id, limit=5, before_id=ids[0]) == []


def test_messages_of_another_users_or_unknown_conversation_are_a_404(session):
    conversation = _conversation(session, user="u1")
    for call in (lambda: _messages(session, conversation.id, user="u2"), lambda: _messages(session, 9999)):
        with pytest.raises(HTTPException) as error:
            call()
        assert error.value.status_code == 404


def _targets(session, user="u1"):
    return routes_conversations.list_repo_targets(session=session, user={"id": user})


def test_repo_targets_are_distinct_most_recent_first_and_scoped_to_the_user(session):
    conversation = _conversation(session)
    _turn(session, conversation.id, 1, owner="o", name="a", base="main")
    _turn(session, conversation.id, 5, owner="o", name="b", base="main")
    _turn(session, conversation.id, 9, owner="o", name="a", base="main")        # a redevient la plus récente
    _turn(session, conversation.id, 3, owner="o", name="a", base="dev")         # autre branche de base = autre cible
    _turn(session, conversation.id, 2)                                           # sans dépôt : ignorée
    _turn(session, conversation.id, 4, owner="o", name=None, base="main")        # dépôt incomplet : ignorée
    _turn(session, conversation.id, 8, user="u2", owner="x", name="y", base="main")
    assert _targets(session) == [
        {"repo_owner": "o", "repo_name": "a", "base_branch": "main"},
        {"repo_owner": "o", "repo_name": "b", "base_branch": "main"},
        {"repo_owner": "o", "repo_name": "a", "base_branch": "dev"},
    ]


def test_repo_targets_are_capped(session, monkeypatch):
    conversation = _conversation(session)
    for index in range(5):
        _turn(session, conversation.id, index, owner="o", name=f"r{index}", base="main")
    monkeypatch.setattr(routes_conversations, "REPO_TARGETS_LIMIT", 2)
    assert [t["repo_name"] for t in _targets(session)] == ["r4", "r3"]
