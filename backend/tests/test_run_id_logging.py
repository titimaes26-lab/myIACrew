"""Identifiant de corrélation d'une exécution (run_context.py) : présent dans chaque ligne de journal, absent ailleurs, jamais conservé."""
import asyncio
import io
import logging

import pytest

import run_context
from logs import OneLineFormatter, configure_logging, get_logger


def _format(message="bonjour"):
    record = logging.LogRecord("myiacrew.x", logging.INFO, __file__, 1, message, None, None)
    return OneLineFormatter("%(levelname)s %(name)s : %(message)s").format(record)


def test_no_run_id_outside_an_execution():
    assert run_context.current_run_id() is None
    assert _format() == "INFO myiacrew.x : bonjour"


def test_log_lines_carry_the_run_id_inside_and_not_after():
    with run_context.track_run(12) as run_id:
        assert run_id == "run-12" == run_context.current_run_id()
        assert _format() == "INFO myiacrew.x [run-12] : bonjour"
    assert run_context.current_run_id() is None


def test_run_id_is_restored_on_exception():
    with pytest.raises(ValueError):
        with run_context.track_run(3):
            raise ValueError("x")
    assert run_context.current_run_id() is None


def test_message_containing_separator_is_not_altered():
    with run_context.track_run(1):
        assert _format("a : b") == "INFO myiacrew.x [run-1] : a : b"


def test_decorator_binds_by_name_and_reaches_worker_threads():
    seen = {}

    @run_context.tracked("entry_id")
    async def work(other, entry_id):
        seen["task"] = run_context.current_run_id()
        seen["thread"] = await asyncio.to_thread(run_context.current_run_id)

    asyncio.run(work("x", entry_id=7))
    assert seen == {"task": "run-7", "thread": "run-7"}
    asyncio.run(work("x", 8))
    assert seen["task"] == "run-8" and run_context.current_run_id() is None


def test_concurrent_executions_keep_their_own_id():
    seen = []

    @run_context.tracked("n")
    async def work(n):
        await asyncio.sleep(0.01 * (3 - n))
        seen.append((n, run_context.current_run_id()))

    async def main():
        await asyncio.gather(work(1), work(2))

    asyncio.run(main())
    assert sorted(seen) == [(1, "run-1"), (2, "run-2")]


def test_real_logger_output_includes_the_run_id():
    """Le formateur INSTALLÉ par configure_logging (pas un formateur de test) porte l'identifiant ; flux propre au test,
    car le handler d'application est lié à sys.stdout au premier import (capsys ne le voit pas selon l'ordre des tests)."""
    root = configure_logging()
    installed = next(h for h in root.handlers if getattr(h, "_myiacrew", False))
    stream = io.StringIO()
    probe = logging.StreamHandler(stream)
    probe.setFormatter(installed.formatter)
    log = get_logger("run_context_test")
    log.addHandler(probe)
    try:
        with run_context.track_run(5):
            log.warning("dans l'exécution")
        log.warning("hors exécution")
    finally:
        log.removeHandler(probe)
    lines = stream.getvalue().splitlines()
    assert "[run-5] : dans l'exécution" in lines[0] and "[run-" not in lines[1]
