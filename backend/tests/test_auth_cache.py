import asyncio
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import auth  # noqa: E402


def _jwt(exp=None):
    def part(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    payload = {"sub": "u1"} if exp is None else {"sub": "u1", "exp": exp}
    return f"{part({'alg': 'HS256'})}.{part(payload)}.signature"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(auth, "SUPABASE_URL", "http://supabase")
    monkeypatch.setattr(auth, "SUPABASE_ANON_KEY", "anon")
    monkeypatch.setattr(auth, "AUTH_CACHE_TTL_S", 60.0)
    auth.clear_auth_cache()
    yield
    auth.clear_auth_cache()


def _client(monkeypatch, status=200, body=None):
    calls = []

    def handler(request):
        calls.append(request.headers["authorization"])
        return httpx.Response(status, json=body if body is not None else {"id": "u1", "email": "a@b.c"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(auth, "_get_http_client", lambda: client)
    return calls


def _call(token):
    return asyncio.run(auth.get_current_user(f"Bearer {token}"))


def test_a_valid_token_is_validated_once_then_served_from_the_cache(monkeypatch):
    calls = _client(monkeypatch)
    token = _jwt(time.time() + 3600)
    assert _call(token)["id"] == "u1" and _call(token)["id"] == "u1" and _call(token)["id"] == "u1"
    assert len(calls) == 1
    _call(_jwt(time.time() + 3601))                   # un autre jeton est validé à part
    assert len(calls) == 2


def test_the_cache_never_keeps_the_token_in_clear_nor_leaks_mutations(monkeypatch):
    _client(monkeypatch)
    token = _jwt(time.time() + 3600)
    user = _call(token)
    user["id"] = "pirate"                              # le dict rendu est une copie
    assert _call(token)["id"] == "u1"
    assert all(token not in key for key in auth._auth_cache) and all(len(key) == 64 for key in auth._auth_cache)


def test_only_successful_validations_are_cached(monkeypatch):
    calls = _client(monkeypatch, status=401, body={"msg": "invalid"})
    for _ in range(2):
        with pytest.raises(HTTPException) as err:
            _call("bad-token")
        assert err.value.status_code == 401
    assert len(calls) == 2 and auth._auth_cache == {}


def test_the_entry_expires_after_the_ttl():
    token = _jwt(time.time() + 3600)
    auth.cache_user(token, {"id": "u1"}, now=1000.0)
    assert auth.cached_user(token, now=1059.0) == {"id": "u1"}
    assert auth.cached_user(token, now=1061.0) is None and auth._auth_cache == {}


def test_the_ttl_is_capped_by_the_token_own_expiry_and_expired_tokens_are_not_cached():
    soon = _jwt(5000.0)
    auth.cache_user(soon, {"id": "u1"}, now=100.0, epoch_now=4990.0)      # expire dans 10 s
    assert auth.cached_user(soon, now=109.0) == {"id": "u1"} and auth.cached_user(soon, now=111.0) is None
    auth.cache_user(_jwt(4000.0), {"id": "u2"}, now=100.0, epoch_now=4990.0)   # déjà expiré
    auth.cache_user("pas-un-jwt", {"id": "u3"}, now=100.0)                      # exp illisible : TTL normal
    assert auth.cached_user("pas-un-jwt", now=159.0) == {"id": "u3"}
    assert len(auth._auth_cache) == 1


def test_ttl_zero_disables_the_cache(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_CACHE_TTL_S", 0.0)
    calls = _client(monkeypatch)
    token = _jwt(time.time() + 3600)
    _call(token)
    _call(token)
    assert len(calls) == 2


def test_the_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(auth, "_AUTH_CACHE_MAX_ENTRIES", 3)
    for index in range(6):
        auth.cache_user(f"t{index}", {"id": index}, now=100.0 + index)
    assert len(auth._auth_cache) == 3
    assert auth.cached_user("t5", now=110.0) == {"id": 5} and auth.cached_user("t0", now=110.0) is None


def test_missing_header_and_unreachable_supabase_are_unchanged(monkeypatch):
    with pytest.raises(HTTPException) as err:
        asyncio.run(auth.get_current_user(None))
    assert err.value.status_code == 401

    def boom(request):
        raise httpx.ConnectError("down")
    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    monkeypatch.setattr(auth, "_get_http_client", lambda: client)
    with pytest.raises(HTTPException) as err:
        _call("some-token")
    assert err.value.status_code == 503


@pytest.mark.parametrize("value, expected", [("", 60.0), ("30", 30.0), ("0", 0.0), ("-5", 60.0), ("abc", 60.0)])
def test_ttl_environment_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("AUTH_CACHE_TTL_S", value)
    assert auth._env_ttl("AUTH_CACHE_TTL_S", 60.0) == expected
