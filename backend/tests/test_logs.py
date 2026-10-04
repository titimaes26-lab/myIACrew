import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import logs  # noqa: E402


@pytest.fixture(autouse=True)
def clean_root():
    root = logging.getLogger(logs.ROOT_NAME)
    saved = (root.level, list(root.handlers))
    yield root
    root.setLevel(saved[0])
    root.handlers[:] = saved[1]


def test_configuration_is_idempotent_and_never_duplicates_the_handler(clean_root):
    clean_root.handlers[:] = []
    logs.configure_logging()
    logs.configure_logging()
    logs.get_logger("main")
    assert len([h for h in clean_root.handlers if getattr(h, "_myiacrew", False)]) == 1


def test_loggers_are_named_after_their_module():
    assert logs.get_logger("main").name == "myiacrew.main"
    assert logs.get_logger("crew").parent.name == "myiacrew"


@pytest.mark.parametrize("raw, expected", [
    ("DEBUG", logging.DEBUG), ("warning", logging.WARNING), ("  error ", logging.ERROR),
    ("n'importe quoi", logging.INFO), ("", logging.INFO),
])
def test_log_level_comes_from_the_environment_with_a_safe_default(monkeypatch, clean_root, raw, expected):
    monkeypatch.setenv("LOG_LEVEL", raw)
    assert logs.configure_logging().level == expected


def test_a_line_carries_time_level_module_and_message_on_stdout_immediately(monkeypatch, clean_root, capsys):
    clean_root.handlers[:] = []
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    logs.get_logger("main").warning("battement impossible")
    out = capsys.readouterr().out
    assert "WARNING myiacrew.main : battement impossible" in out and out.endswith("\n")


def test_third_party_loggers_are_left_alone():
    logs.configure_logging()
    assert logging.getLogger("litellm").level == logging.NOTSET
    assert not logging.getLogger().handlers or all(not getattr(h, "_myiacrew", False) for h in logging.getLogger().handlers)
