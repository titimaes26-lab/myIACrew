import os

import pytest


import database

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


def test_a_failing_migration_is_logged_as_a_one_line_warning_and_the_next_ones_still_run(monkeypatch, caplog):
    from sqlalchemy import create_engine
    engine = create_engine("sqlite://")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "_MIGRATION_STATEMENTS", ["ALTER TABLE absente ADD COLUMN x INTEGER", "SELECT 1"])
    caplog.set_level("INFO", logger="myiacrew")
    database._run_lightweight_migrations()
    warnings = [record for record in caplog.records if "migration ignorée" in record.getMessage()]
    assert len(warnings) == 1 and warnings[0].levelname == "WARNING" and "absente" in warnings[0].getMessage()
    assert warnings[0].name == "myiacrew.database"


def test_the_application_modules_no_longer_use_print():
    # Tout passe par logs.py (niveau, une ligne par événement, échappement des retours à la ligne).
    import glob
    import re
    root = os.path.dirname(os.path.dirname(__file__))
    offenders = []
    for path in glob.glob(os.path.join(root, "*.py")):
        if os.path.basename(path).startswith("test_") or os.path.basename(path) == "logs.py":
            continue
        for number, line in enumerate(open(path, encoding="utf-8"), 1):
            if re.search(r"(?<![\w.'\"])print\(", line) and not line.strip().startswith(("#", '"', "'")):
                offenders.append(f"{os.path.basename(path)}:{number}")
    assert offenders == []


# --- rattrapage des colonnes manquantes (SQLite) --------------------------------------------------------------------

def _old_sqlite(tmp_path, table_sql):
    from sqlalchemy import create_engine, text
    old = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with old.begin() as conn:
        conn.execute(text(table_sql))
    return old


OLD_CHECKPOINT = ("CREATE TABLE executioncheckpoint (id INTEGER PRIMARY KEY, execution_id INTEGER NOT NULL, step VARCHAR NOT NULL, "
                  "raw VARCHAR NOT NULL, created_at DATETIME NOT NULL)")


def test_an_existing_sqlite_table_gets_the_columns_added_since_its_creation(tmp_path):
    from sqlmodel import Session, select
    old = _old_sqlite(tmp_path, OLD_CHECKPOINT)
    with pytest.raises(Exception, match="no column named model"):                  # le défaut constaté avant le rattrapage
        with Session(old) as session:
            session.add(database.ExecutionCheckpoint(execution_id=1, step="design", raw="x", model="m"))
            session.commit()
    assert database._add_missing_columns(old) == ["executioncheckpoint.model"]
    with Session(old) as session:
        session.add(database.ExecutionCheckpoint(execution_id=1, step="design", raw="x", model="m"))
        session.commit()
        assert session.exec(select(database.ExecutionCheckpoint)).one().model == "m"


def test_the_catch_up_is_idempotent_and_leaves_existing_rows_alone(tmp_path, caplog):
    from sqlalchemy import text
    old = _old_sqlite(tmp_path, OLD_CHECKPOINT)
    with old.begin() as conn:
        conn.execute(text("INSERT INTO executioncheckpoint (execution_id, step, raw, created_at) VALUES (1, 'design', 'ancien', '2026-01-01')"))
    assert database._add_missing_columns(old) == ["executioncheckpoint.model"]
    caplog.clear()
    caplog.set_level("INFO", logger="myiacrew")
    assert database._add_missing_columns(old) == []
    assert caplog.text == ""                                  # rien n'est retenté sur une colonne déjà présente (pas d'erreur avalée)
    with old.connect() as conn:
        assert conn.execute(text("SELECT raw, model FROM executioncheckpoint")).all() == [("ancien", None)]


def test_missing_tables_are_skipped_and_a_required_column_is_reported_not_added(tmp_path, caplog):
    from sqlalchemy import text
    old = _old_sqlite(tmp_path, "CREATE TABLE executioncheckpoint (id INTEGER PRIMARY KEY)")   # les autres tables n'existent pas
    caplog.set_level("WARNING", logger="myiacrew")
    assert database._add_missing_columns(old) == ["executioncheckpoint.model"]                  # la seule colonne facultative
    assert "executioncheckpoint.raw absente et obligatoire" in caplog.text
    with old.connect() as conn:
        assert [row[1] for row in conn.execute(text("PRAGMA table_info(executioncheckpoint)")).all()] == ["id", "model"]


def test_the_catch_up_does_nothing_outside_sqlite():
    from types import SimpleNamespace
    postgres = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    assert database._add_missing_columns(postgres) == []                                    # Postgres : instructions IF NOT EXISTS


def test_create_db_and_tables_applies_the_catch_up(tmp_path, monkeypatch):
    from sqlalchemy import text
    old = _old_sqlite(tmp_path, OLD_CHECKPOINT)
    monkeypatch.setattr(database, "engine", old)
    database.create_db_and_tables()
    with old.connect() as conn:
        assert "model" in [row[1] for row in conn.execute(text("PRAGMA table_info(executioncheckpoint)")).all()]
