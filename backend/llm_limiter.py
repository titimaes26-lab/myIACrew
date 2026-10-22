"""Limiteurs de débit des requêtes Gemini, UN PAR MODÈLE.

Chaque modèle a ses propres plafonds (voir llm_models : requêtes par minute RPM, tokens par minute TPM, requêtes par jour
RPD). Le quota est partagé par tout le projet : toutes les exécutions en parallèle, la qualification, le résumé, et les
appels que CrewAI fait lui-même (planificateur, observateur de chaque étape) qui ne passent pas par le `max_rpm` d'un
crew. Les limiteurs se placent donc sous eux tous, sur les méthodes de requête du client `google-genai`, et choisissent le
limiteur d'après l'argument `model` de chaque appel :

- RPM : au plus `rpm` requêtes sur une minute glissante ; chaque appelant réserve un créneau et attend son tour, sans
  jamais dépasser le plafond, même à plusieurs threads ;
- TPM : les tokens de l'appel (estimation de l'entrée avant l'envoi, remplacée par le total réel dès la réponse) comptent
  sur la même fenêtre glissante ; l'appel attend que assez de tokens en sortent. La sortie n'étant pas connue d'avance, elle
  n'est comptée qu'après coup : l'appel SUIVANT attend si elle a creusé le budget ;
- RPD : au-delà du plafond journalier (remise à zéro à minuit, heure du Pacifique), aucune attente n'a de sens : l'appel
  échoue tout de suite avec `DailyQuotaExceeded` (jamais retentée par les reprises) ;
- sur un 429, le délai demandé par Google (« Please retry in 44s », `retryDelay`) devient une pause COMMUNE à CE modèle :
  les appels suivants vers lui l'attendent au lieu de réessayer aussitôt et de consommer du quota pour rien.
Un modèle sans entrée dans llm_models tombe sur le limiteur par défaut (`GEMINI_MAX_REQUESTS_PER_MINUTE`, 12 par défaut,
RPM seul). Les plafonds valent par process ; avec plusieurs instances du backend, chacune a ses propres fenêtres et
compteurs (le compteur journalier repart aussi de zéro au redémarrage).
"""
import asyncio
import functools
import os
import threading
import time
from typing import Any, Callable, Optional

import llm_models
import llm_usage
import run_budget
import telemetry
from llm_quota_errors import is_quota_error, retry_delay_seconds  # noqa: F401  (retry_delay_seconds réexporté)
from llm_rate_window import (  # noqa: F401  (réexportés : API historique du module)
    COOLDOWN_MARGIN_SECONDS, DEFAULT_COOLDOWN_SECONDS, DEFAULT_MAX_PER_MINUTE, MAX_COOLDOWN_SECONDS, WINDOW_SECONDS,
    DailyQuotaExceeded, GeminiRateLimiter,
)
from logs import get_logger

log = get_logger("llm_limiter")

# Une attente plus longue que ceci est tracée (INFO) : l'utilisateur voit son exécution ralentir.
LOG_WAIT_THRESHOLD_SECONDS = 2.0

def _env_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, ""))
    except ValueError:
        return default
    return value if value >= 1 else default


# Limiteur par défaut : modèles absents de llm_models (RPM seul).
limiter = GeminiRateLimiter(_env_positive_int("GEMINI_MAX_REQUESTS_PER_MINUTE", DEFAULT_MAX_PER_MINUTE))

_model_limiters: dict[str, GeminiRateLimiter] = {}
_registry_lock = threading.Lock()
_installed: set[tuple[int, str]] = set()
_served_hook: Optional[Callable[[str], None]] = None   # reçoit le modèle réellement servi par chaque réponse (voir install)


def _report_served(response: Any) -> None:
    """Signale le modèle que l'API déclare avoir servi (`model_version`) ; best-effort, une mesure ne casse jamais un appel."""
    model = llm_usage.served_model(response)
    if model and _served_hook is not None:
        try:
            _served_hook(model)
        except Exception as e:
            log.debug(f"suivi du modèle servi en échec : {type(e).__name__}: {e}")


def limiter_for(model: Optional[str]) -> GeminiRateLimiter:
    """Limiteur du modèle (créé au premier appel, avec ses plafonds effectifs de llm_models), ou le limiteur par défaut."""
    key = llm_models.model_key(model)
    with _registry_lock:
        existing = _model_limiters.get(key)
    if existing is not None:
        return existing
    limits = llm_models.limits_for(model)
    if limits is None:
        return limiter
    with _registry_lock:
        return _model_limiters.setdefault(key, GeminiRateLimiter(
            limits.rpm, max_tokens_per_minute=limits.tpm, max_per_day=limits.rpd, name=key,
        ))


def reset_model_limiters() -> None:
    """Oublie les limiteurs par modèle (fenêtres et compteurs journaliers) ; les plafonds sont relus au prochain appel."""
    with _registry_lock:
        _model_limiters.clear()


def _announce(wait: float, on_wait: Optional[Callable[[float], None]], lim: GeminiRateLimiter, reason: str = "") -> None:
    if wait >= LOG_WAIT_THRESHOLD_SECONDS:
        log.info(f"requête Gemini retardée de {wait:.0f} s (modèle {lim.name} : {reason or 'limiteur'})")
    if wait > 0 and on_wait is not None:
        try:
            on_wait(wait)
        except Exception as e:
            log.debug(f"callback d'attente en échec : {type(e).__name__}: {e}")


def _note_error(error: BaseException, lim: GeminiRateLimiter) -> None:
    if is_quota_error(error):
        pause = lim.note_rate_limited(error)
        if pause > 0:
            log.warning(f"429 Gemini ({lim.name}) : pause commune de {pause:.0f} s pour les requêtes vers ce modèle")
        else:
            log.warning(f"429 Gemini ({lim.name}) avec un délai trop long (quota journalier ?) : aucune pause commune, l'erreur remonte")


def _reserve_for(kwargs: dict) -> tuple[GeminiRateLimiter, float, list]:
    """Limiteur du modèle de l'appel, et réservation de son créneau (tokens d'entrée estimés seulement si le modèle a un TPM).
    Budget de tokens de l'exécution atteint : l'appel est refusé AVANT toute réservation."""
    run_budget.enforce()
    lim = limiter_for(kwargs.get("model"))
    tokens = llm_usage.estimate_prompt_tokens(kwargs.get("contents"), kwargs.get("config")) if lim.max_tokens_per_minute else 0
    wait, ticket = lim.reserve_ticket(tokens=tokens)
    return lim, wait, ticket


def _after_call(lim: GeminiRateLimiter, started: float, wait: float, ticket: list, tokens: Optional[int], error: Optional[BaseException] = None) -> None:
    """Fin d'un appel : tokens ajoutés au budget de l'exécution (réels, sinon l'estimation d'entrée ; rien pour un appel en erreur),
    puis span « appel LLM » (modèle, tokens, attente imposée par le limiteur, classe d'erreur) si les traces sont actives."""
    run_budget.add(tokens if tokens is not None else (0 if error else ticket[1]))
    telemetry.record_span(
        "llm.call", time.monotonic() - started, model=lim.name, tokens=tokens, wait_s=round(wait, 1), wait_reason=ticket[2] or "",
        error=type(error).__name__ if error else None,
    )


def _wrap_sync(function: Callable[..., Any], on_wait: Optional[Callable[[float], None]]) -> Callable[..., Any]:
    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        lim, wait, ticket = _reserve_for(kwargs)
        _announce(wait, on_wait, lim, ticket[2])
        if wait > 0:
            time.sleep(wait)
        started = time.monotonic()
        try:
            response = function(*args, **kwargs)
        except Exception as error:
            _note_error(error, lim)
            _after_call(lim, started, wait, ticket, None, error)
            raise
        lim.settle(ticket, llm_usage.total_tokens(response))
        _report_served(response)
        _after_call(lim, started, wait, ticket, llm_usage.total_tokens(response))
        return response
    return wrapper


def _wrap_sync_stream(function: Callable[..., Any], on_wait: Optional[Callable[[float], None]]) -> Callable[..., Any]:
    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        lim, wait, ticket = _reserve_for(kwargs)
        _announce(wait, on_wait, lim, ticket[2])
        if wait > 0:
            time.sleep(wait)
        actual: Optional[int] = None
        last = None
        started = time.monotonic()
        try:
            for chunk in function(*args, **kwargs):
                actual = llm_usage.total_tokens(chunk) or actual   # le décompte cumulé arrive sur les derniers morceaux
                last = chunk if llm_usage.served_model(chunk) else last
                yield chunk
        except Exception as error:
            _note_error(error, lim)
            _after_call(lim, started, wait, ticket, actual, error)
            raise
        lim.settle(ticket, actual)
        _report_served(last)
        _after_call(lim, started, wait, ticket, actual)
    return wrapper


def _wrap_async(function: Callable[..., Any], on_wait: Optional[Callable[[float], None]]) -> Callable[..., Any]:
    @functools.wraps(function)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        lim, wait, ticket = _reserve_for(kwargs)
        _announce(wait, on_wait, lim, ticket[2])
        if wait > 0:
            await asyncio.sleep(wait)
        started = time.monotonic()
        try:
            response = await function(*args, **kwargs)
        except Exception as error:
            _note_error(error, lim)
            _after_call(lim, started, wait, ticket, None, error)
            raise
        lim.settle(ticket, llm_usage.total_tokens(response))
        _report_served(response)
        _after_call(lim, started, wait, ticket, llm_usage.total_tokens(response))
        return response
    return wrapper


async def _settled_stream(stream: Any, lim: GeminiRateLimiter, ticket: list, wait: float = 0.0) -> Any:
    """Relaie les morceaux d'un flux asynchrone et remplace l'estimation par le dernier total de tokens rapporté."""
    actual: Optional[int] = None
    last = None
    started = time.monotonic()
    async for chunk in stream:
        actual = llm_usage.total_tokens(chunk) or actual
        last = chunk if llm_usage.served_model(chunk) else last
        yield chunk
    lim.settle(ticket, actual)
    _report_served(last)
    _after_call(lim, started, wait, ticket, actual)


def _wrap_async_stream(function: Callable[..., Any], on_wait: Optional[Callable[[float], None]]) -> Callable[..., Any]:
    @functools.wraps(function)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        lim, wait, ticket = _reserve_for(kwargs)
        _announce(wait, on_wait, lim, ticket[2])
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            stream = await function(*args, **kwargs)
        except Exception as error:
            _note_error(error, lim)
            raise
        return _settled_stream(stream, lim, ticket, wait)
    return wrapper


def _default_targets() -> list[tuple[Any, str, str]]:
    from google.genai import models
    return [
        (models.Models, "generate_content", "sync"),
        (models.Models, "generate_content_stream", "stream"),
        (models.AsyncModels, "generate_content", "async"),
        (models.AsyncModels, "generate_content_stream", "astream"),
    ]


def install(
    on_wait: Optional[Callable[[float], None]] = None, targets: Optional[list[tuple[Any, str, str]]] = None,
    on_served: Optional[Callable[[str], None]] = None,
) -> int:
    """Enveloppe les méthodes de requête du client google-genai (à l'échelle du process). Idempotent : renvoie le nombre
    de méthodes nouvellement enveloppées. `on_wait(secondes)` reçoit chaque attente imposée (mesures d'exécution) ;
    `on_served(modèle)` le modèle que l'API déclare avoir servi pour chaque réponse (rapport de chaque agent)."""
    global _served_hook
    if on_served is not None:
        _served_hook = on_served
    wrappers = {"sync": _wrap_sync, "stream": _wrap_sync_stream, "async": _wrap_async, "astream": _wrap_async_stream}
    installed = 0
    for cls, name, kind in targets if targets is not None else _default_targets():
        key = (id(cls), name)
        if key in _installed:
            continue
        setattr(cls, name, wrappers[kind](getattr(cls, name), on_wait))
        _installed.add(key)
        installed += 1
    return installed
