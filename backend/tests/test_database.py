import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import database  # noqa: E402

POSTGRES = "postgresql://u:p@host:5432/db"


def test_every_engine_checks_pooled_connections_before_use_and_recycles_old_ones():
    for url in (POSTGRES, "sqlite:///./dev.db", "sqlite://"):
        options = database.engine_options(url)
        # Une connexion coupée par le pooler pendant qu'elle dormait est vérifiée avant usage, puis remplacée.
        assert options["pool_pre_ping"] is True and options["pool_recycle"] == 1800


def test_pool_size_is_explicit_and_adjustable_outside_sqlite(monkeypatch):
    for name in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    options = database.engine_options(POSTGRES)
    assert (options["pool_size"], options["max_overflow"], options["pool_timeout"]) == (10, 10, 30)
    monkeypatch.setenv("DB_POOL_SIZE", "25")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "0")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "5")
    options = database.engine_options(POSTGRES)
    assert (options["pool_size"], options["max_overflow"], options["pool_timeout"]) == (25, 0, 5)


@pytest.mark.parametrize("raw", ["abc", "", "0", "-3"])
def test_unusable_pool_settings_fall_back_to_the_defaults(monkeypatch, raw):
    monkeypatch.setenv("DB_POOL_SIZE", raw)
    monkeypatch.setenv("DB_POOL_TIMEOUT", raw)
    options = database.engine_options(POSTGRES)
    assert options["pool_size"] == 10 and options["pool_timeout"] == 30


def test_sqlite_gets_no_pool_options_its_memory_pool_would_reject():
    options = database.engine_options("sqlite://")
    assert not {"pool_size", "max_overflow", "pool_timeout"} & set(options)
    # Le moteur correspondant se construit bien (sans cela, DATABASE_URL=sqlite:// ferait échouer le démarrage).
    database.create_engine("sqlite://", **options)
