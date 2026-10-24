"""Entrée de get_current_user : en-tête Bearer, configuration manquante, jeton nettoyé, et issue d'échec jamais « oubliée » par asyncio."""
import asyncio
import gc

import httpx
import pytest
from fastapi import HTTPException

import auth


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(auth, "SUPABASE_URL", "http://supabase")
    monkeypatch.setattr(auth, "SUPABASE_ANON_KEY", "anon")
    auth.clear_auth_cache()
    yield
    auth.clear_auth_cache()


def _client(monkeypatch, status=200):
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return httpx.Response(status, json={"id": "u1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(auth, "_get_http_client", lambda: client)
    return seen


@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer", "bearertoken"])
def test_a_missing_or_non_bearer_header_is_a_401(header):
    with pytest.raises(HTTPException) as caught:
        asyncio.run(auth.get_current_user(header))
    assert caught.value.status_code == 401


@pytest.mark.parametrize("missing", ["SUPABASE_URL", "SUPABASE_ANON_KEY"])
def test_a_backend_without_supabase_settings_answers_500_not_401(monkeypatch, missing):
    monkeypatch.setattr(auth, missing, "")
    with pytest.raises(HTTPException) as caught:
        asyncio.run(auth.get_current_user("Bearer tok"))
    assert caught.value.status_code == 500 and "manquants" in caught.value.detail


def test_the_token_is_stripped_before_it_is_sent_to_supabase(monkeypatch):
    seen = _client(monkeypatch)
    assert asyncio.run(auth.get_current_user("Bearer   tok-123  "))["id"] == "u1"
    assert seen == ["Bearer tok-123"]
    assert asyncio.run(auth.get_current_user("bearer tok-123"))["id"] == "u1"      # même jeton : servi par le cache, schéma insensible à la casse
    assert len(seen) == 1


def test_a_failed_validation_leaves_no_never_retrieved_exception(monkeypatch):
    """Sans attente concurrente, l'issue d'échec publiée n'est lue par personne : elle doit être marquée lue (sinon asyncio la signale)."""
    _client(monkeypatch, status=401)
    reports = []

    async def go():
        asyncio.get_running_loop().set_exception_handler(lambda loop, context: reports.append(context.get("message")))
        with pytest.raises(HTTPException):
            await auth.get_current_user("Bearer bad")
        gc.collect()
    asyncio.run(go())
    assert reports == []
    assert auth._inflight == {}
