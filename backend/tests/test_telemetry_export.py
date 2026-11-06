"""Traces OpenTelemetry : l'EXPORT est observable (hôte et projet au démarrage, premiers envois, échecs, première erreur de span) et un
vrai BatchSpanProcessor atteint le récepteur quelle que soit la valeur de OTEL_SDK_DISABLED. Suite de test_telemetry.py."""
import logging  # noqa: F401  (utilisé par les tests de journalisation ci-dessous)

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import telemetry


@pytest.fixture()
def spans(monkeypatch):
    for name in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_ENDPOINT", "LANGSMITH_PROJECT", "OTEL_CAPTURE_CONTENT"):
        monkeypatch.delenv(name, raising=False)
    telemetry.reset()
    exporter = InMemorySpanExporter()
    assert telemetry.setup(SimpleSpanProcessor(exporter))
    yield exporter
    telemetry.reset()


# --- l'export est observable (rien dans les journaux = impossible de savoir si les traces partent) ------------------------

class _Result:
    def __init__(self, name):
        self.name = name


class _Inner:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, 0

    def export(self, spans):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return _Result(self.outcome)

    def shutdown(self):
        return "stopped"

    def force_flush(self, timeout_millis=0):
        return True


def test_the_first_exports_are_logged_then_only_failures(caplog):
    exporter = telemetry._LoggingExporter(_Inner("SUCCESS"), "https://eu.example")
    with caplog.at_level(logging.INFO, logger="myiacrew.telemetry"):
        for _ in range(telemetry.LOGGED_EXPORTS + 3):
            exporter.export([1, 2])
    lines = [r.getMessage() for r in caplog.records if "envoyé" in r.getMessage()]
    assert len(lines) == telemetry.LOGGED_EXPORTS and "2 span(s)" in lines[0] and "eu.example" in lines[0]


def test_a_refused_export_is_a_warning_every_time(caplog):
    exporter = telemetry._LoggingExporter(_Inner("FAILURE"), "https://eu.example")
    with caplog.at_level(logging.WARNING, logger="myiacrew.telemetry"):
        exporter.export([1])
        exporter.export([1])
    assert len([r for r in caplog.records if "refusé ou perdu" in r.getMessage()]) == 2


def test_an_export_exception_is_logged_without_its_message_and_still_raised(caplog):
    exporter = telemetry._LoggingExporter(_Inner(RuntimeError("x-api-key: SECRETVALUE")), "https://eu.example")
    with caplog.at_level(logging.WARNING, logger="myiacrew.telemetry"):
        with pytest.raises(RuntimeError):
            exporter.export([1])
    assert "RuntimeError" in caplog.text and "SECRETVALUE" not in caplog.text
    assert exporter.shutdown() == "stopped" and exporter.force_flush() is True


@pytest.mark.parametrize("url, expected", [
    ("https://eu.api.smith.langchain.com", "https://eu.api.smith.langchain.com"),
    ("https://user:pw@host.example:8443/a/b?k=v", "https://host.example"), ("pas une adresse", "adresse illisible"),
])
def test_only_scheme_and_host_of_the_endpoint_are_ever_logged(url, expected):
    assert telemetry._host(url) == expected


def _setup_with_fake_exporter(monkeypatch, endpoint):
    import opentelemetry.exporter.otlp.proto.http.trace_exporter as module
    monkeypatch.setattr(module, "OTLPSpanExporter", lambda **kw: _Inner("SUCCESS"))
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "CLE-SECRETE-123")
    monkeypatch.setenv("LANGSMITH_PROJECT", "mon-projet")
    if endpoint:
        monkeypatch.setenv("LANGSMITH_ENDPOINT", endpoint)
    else:
        monkeypatch.delenv("LANGSMITH_ENDPOINT", raising=False)
    telemetry.reset()
    try:
        assert telemetry.setup() is True
    finally:
        telemetry.reset()


def test_startup_says_where_traces_go_and_never_logs_the_key(monkeypatch, caplog):
    with caplog.at_level(logging.INFO, logger="myiacrew.telemetry"):
        _setup_with_fake_exporter(monkeypatch, "https://eu.api.smith.langchain.com")
    assert "https://eu.api.smith.langchain.com" in caplog.text and "mon-projet" in caplog.text
    assert "CLE-SECRETE" not in caplog.text and "américaine" not in caplog.text


def test_the_default_us_endpoint_is_flagged_as_a_warning(monkeypatch, caplog):
    with caplog.at_level(logging.INFO, logger="myiacrew.telemetry"):
        _setup_with_fake_exporter(monkeypatch, "")
    (warning,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "LANGSMITH_ENDPOINT absent" in warning.getMessage() and "CLE-SECRETE" not in caplog.text


def test_only_the_first_span_failure_is_a_warning(spans, monkeypatch, caplog):
    monkeypatch.setattr(telemetry._tracer, "start_span", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    with caplog.at_level(logging.DEBUG, logger="myiacrew.telemetry"):
        telemetry.record_span("a", 1.0)
        telemetry.record_span("b", 1.0)
    levels = [r.levelno for r in caplog.records if "non enregistré" in r.getMessage()]
    assert levels == [logging.WARNING, logging.DEBUG]


# --- OTEL_SDK_DISABLED ne doit pas rendre nos traces silencieusement nulles ---------------------------------------------

def _fake_receiver():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", 0)))
            hits.append(self.path)
            self.send_response(200)
            self.send_header("content-length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, hits


@pytest.mark.parametrize("disabled", [None, "true", "TRUE", "false"])
def test_a_real_batch_export_reaches_the_receiver_whatever_otel_sdk_disabled_says(monkeypatch, disabled):
    import time
    server, hits = _fake_receiver()
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "k")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    if disabled is not None:
        monkeypatch.setenv("OTEL_SDK_DISABLED", disabled)
    monkeypatch.setenv("OTEL_BSP_SCHEDULE_DELAY", "200")                         # envoi par lots toutes les 200 ms (5 s par défaut)
    telemetry.reset()
    try:
        assert telemetry.setup() is True
        with telemetry.span("crew.run"):
            telemetry.record_span("llm.call", 1.0, model="m")
        deadline = time.time() + 10
        while time.time() < deadline and not hits:
            time.sleep(0.1)
        assert hits == ["/otel/v1/traces"]
        import os
        assert os.environ.get("OTEL_SDK_DISABLED") == disabled                   # la variable est rétablie telle quelle (CrewAI la lit)
    finally:
        telemetry.reset()
        server.shutdown()
