import logging

import pytest


import logs


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
    clean_root.handlers[:] = []   # première configuration : seul moment où le niveau est lu
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


def test_a_level_set_afterwards_is_not_overwritten_by_later_get_logger_calls(monkeypatch, clean_root):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    clean_root.handlers[:] = []
    logs.configure_logging()
    clean_root.setLevel(logging.ERROR)
    logs.get_logger("autre")
    logs.configure_logging()
    assert clean_root.level == logging.ERROR


def test_a_multiline_message_cannot_forge_extra_log_lines(clean_root, capsys):
    clean_root.handlers[:] = []
    logs.get_logger("github").warning("échec : première ligne\n2026-10-04 12:00:00 ERROR myiacrew.main : FAUSSE ALERTE\r\nfin")
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.endswith("\n")   # une seule ligne physique
    assert "première ligne\\n2026-10-04" in out and "FAUSSE ALERTE" in out   # le texte reste lisible, échappé


def test_an_exception_trace_keeps_its_own_lines_after_the_one_line_message(clean_root, capsys):
    clean_root.handlers[:] = []
    try:
        raise ValueError("boum")
    except ValueError as exc:
        logs.get_logger("main").error("erreur\nsuite", exc_info=exc)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].endswith("ERROR myiacrew.main : erreur\\nsuite")
    assert any("Traceback (most recent call last)" in line for line in lines[1:])
    assert lines[-1] == "ValueError: boum"
