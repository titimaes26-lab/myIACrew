"""Limiteur GLOBAL des requêtes Gemini.

Le quota de l'offre gratuite (15 requêtes par minute et par modèle) est partagé par tout le projet : toutes les
exécutions en parallèle, la qualification, le résumé, et les appels que CrewAI fait lui-même (planificateur, observateur
de chaque étape) qui ne passent pas par le `max_rpm` d'un crew. Le limiteur se place donc sous eux tous, sur les
méthodes de requête du client `google-genai` :

- au plus `GEMINI_MAX_REQUESTS_PER_MINUTE` requêtes (12 par défaut, une marge sous 15) sur une minute glissante ; chaque
  appelant réserve un créneau et attend son tour, sans jamais dépasser le plafond, même à plusieurs threads ;
- sur un 429, le délai demandé par Google (« Please retry in 44s », `retryDelay`) devient une pause COMMUNE : tous les
  appels suivants l'attendent au lieu de réessayer aussitôt et de consommer du quota pour rien.
Le plafond vaut par process ; avec plusieurs instances du backend, chacune a sa propre fenêtre.
"""
import asyncio
import functools
import os
import re
import threading
import time
from typing import Any, Callable, Optional

from logs import get_logger

log = get_logger("llm_limiter")

DEFAULT_MAX_PER_MINUTE = 12
WINDOW_SECONDS = 60.0
# Pause appliquée à un 429 dont le délai n'est pas lisible, et marge ajoutée au délai demandé.
DEFAULT_COOLDOWN_SECONDS = 20.0
COOLDOWN_MARGIN_SECONDS = 1.0
# Une attente plus longue que ceci est tracée (INFO) : l'utilisateur voit son exécution ralentir.
LOG_WAIT_THRESHOLD_SECONDS = 2.0

# Pause commune maximale acceptée : au-delà (quota JOURNALIER épuisé, délai de plusieurs heures), dormir ferait croire à
# une exécution vivante alors qu'elle ne peut plus avancer. Aucune pause n'est alors posée : les appels échouent vite
# avec l'erreur d'origine (« Quota épuisé » côté utilisateur).
MAX_COOLDOWN_SECONDS = 120.0

# Un 429 se reconnaît à son code HTTP (exceptions google-genai : `.code`), sinon à son TEXTE : « 429 » seul est trop
# large (un « 1429 » ou un « quota » cité dans un message n'est pas un 429).
_QUOTA_TEXT = re.compile(
    r"resource_exhausted|exceeded your current quota|\b429\b[^\n]{0,80}(?:quota|rate.?limit|too many requests)",
    re.IGNORECASE,
)
_RETRY_IN = re.compile(r"retry in ([0-9]+(?:\.[0-9]+)?)\s*s", re.IGNORECASE)
_RETRY_DELAY = re.compile(r"retryDelay['\"]?\s*:\s*['\"]([0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE)


def _env_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, ""))
    except ValueError:
        return default
    return value if value >= 1 else default


def is_quota_error(error: BaseException) -> bool:
    if 429 in (getattr(error, "code", None), getattr(error, "status_code", None)):
        return True
    return _QUOTA_TEXT.search(str(error)) is not None


def retry_delay_seconds(error: BaseException) -> Optional[float]:
    """Délai demandé par Google dans le message d'un 429, ou None."""
    text = str(error)
    for pattern in (_RETRY_IN, _RETRY_DELAY):
        match = pattern.search(text)
        if match:
            return float(match.group(1))
    return None


class GeminiRateLimiter:
    """Réservation de créneaux sur une fenêtre glissante, plus une pause commune après un 429. Thread-safe."""

    def __init__(self, max_per_minute: int = DEFAULT_MAX_PER_MINUTE, window_seconds: float = WINDOW_SECONDS):
        self.max_per_minute = max(1, max_per_minute)
        self.window_seconds = window_seconds
        self._slots: list[float] = []   # instants de départ réservés, triés (peuvent être dans le futur)
        self._cooldown_until = 0.0
        self._lock = threading.Lock()

    def reserve(self, now: Optional[float] = None) -> float:
        """Réserve le prochain créneau libre et renvoie le temps à attendre avant d'envoyer la requête."""
        now = time.monotonic() if now is None else now
        with self._lock:
            # Réservations passées oubliées ; les suivantes sont servies dans l'ordre (jamais avant la dernière
            # réservée) : la liste reste triée et chaque fenêtre est vérifiée sur ses seules réservations antérieures.
            self._slots = [slot for slot in self._slots if slot > now - self.window_seconds]
            start = max(now, self._cooldown_until, self._slots[-1] if self._slots else 0.0)
            while True:
                # Epsilon : start = slot + fenêtre peut revenir à slot - 1e-13 en flottants ; sans lui, la boucle ne finirait pas.
                in_window = [slot for slot in self._slots if slot > start - self.window_seconds + 1e-9]
                if len(in_window) < self.max_per_minute:
                    break
                # Fenêtre pleine : le créneau suivant s'ouvre quand la plus ancienne réservation utile en sort.
                start = in_window[len(in_window) - self.max_per_minute] + self.window_seconds
            self._slots.append(start)
            return max(0.0, start - now)

    def note_rate_limited(self, error: BaseException, now: Optional[float] = None) -> float:
        """Pause commune après un 429 (délai demandé par Google, sinon un défaut). Renvoie la durée retenue, 0 quand le
        délai dépasse MAX_COOLDOWN_SECONDS (aucune pause : les appels échouent vite)."""
        now = time.monotonic() if now is None else now
        delay = retry_delay_seconds(error)
        pause = (delay if delay is not None else DEFAULT_COOLDOWN_SECONDS) + COOLDOWN_MARGIN_SECONDS
        if pause > MAX_COOLDOWN_SECONDS:
            return 0.0
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, now + pause)
        return pause


limiter = GeminiRateLimiter(_env_positive_int("GEMINI_MAX_REQUESTS_PER_MINUTE", DEFAULT_MAX_PER_MINUTE))

_installed: set[tuple[int, str]] = set()


def _announce(wait: float, on_wait: Optional[Callable[[float], None]]) -> None:
    if wait >= LOG_WAIT_THRESHOLD_SECONDS:
        log.info(f"requête Gemini retardée de {wait:.0f} s (limiteur global : {limiter.max_per_minute}/min, pause 429 commune)")
    if wait > 0 and on_wait is not None:
        try:
            on_wait(wait)
        except Exception as e:
            log.debug(f"callback d'attente en échec : {type(e).__name__}: {e}")


def _note_error(error: BaseException) -> None:
    if is_quota_error(error):
        pause = limiter.note_rate_limited(error)
        if pause > 0:
            log.warning(f"429 Gemini : pause commune de {pause:.0f} s pour toutes les requêtes")
        else:
            log.warning("429 Gemini avec un délai trop long (quota journalier ?) : aucune pause commune, l'erreur remonte")


def _wrap_sync(function: Callable[..., Any], on_wait: Optional[Callable[[float], None]]) -> Callable[..., Any]:
    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        wait = limiter.reserve()
        _announce(wait, on_wait)
        if wait > 0:
            time.sleep(wait)
        try:
            return function(*args, **kwargs)
        except Exception as error:
            _note_error(error)
            raise
    return wrapper


def _wrap_sync_stream(function: Callable[..., Any], on_wait: Optional[Callable[[float], None]]) -> Callable[..., Any]:
    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        wait = limiter.reserve()
        _announce(wait, on_wait)
        if wait > 0:
            time.sleep(wait)
        try:
            yield from function(*args, **kwargs)
        except Exception as error:
            _note_error(error)
            raise
    return wrapper


def _wrap_async(function: Callable[..., Any], on_wait: Optional[Callable[[float], None]]) -> Callable[..., Any]:
    @functools.wraps(function)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        wait = limiter.reserve()
        _announce(wait, on_wait)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            return await function(*args, **kwargs)
        except Exception as error:
            _note_error(error)
            raise
    return wrapper


def _default_targets() -> list[tuple[Any, str, str]]:
    from google.genai import models
    return [
        (models.Models, "generate_content", "sync"),
        (models.Models, "generate_content_stream", "stream"),
        (models.AsyncModels, "generate_content", "async"),
        (models.AsyncModels, "generate_content_stream", "async"),
    ]


def install(on_wait: Optional[Callable[[float], None]] = None, targets: Optional[list[tuple[Any, str, str]]] = None) -> int:
    """Enveloppe les méthodes de requête du client google-genai (à l'échelle du process). Idempotent : renvoie le nombre
    de méthodes nouvellement enveloppées. `on_wait(secondes)` reçoit chaque attente imposée (mesures d'exécution)."""
    wrappers = {"sync": _wrap_sync, "stream": _wrap_sync_stream, "async": _wrap_async}
    installed = 0
    for cls, name, kind in targets if targets is not None else _default_targets():
        key = (id(cls), name)
        if key in _installed:
            continue
        setattr(cls, name, wrappers[kind](getattr(cls, name), on_wait))
        _installed.add(key)
        installed += 1
    return installed
