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
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

import llm_models
import llm_usage
from llm_quota_errors import is_quota_error, retry_delay_seconds
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

def _env_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, ""))
    except ValueError:
        return default
    return value if value >= 1 else default


class DailyQuotaExceeded(RuntimeError):
    """Plafond journalier de requêtes (RPD) d'un modèle atteint : inutile d'attendre, la remise à zéro est à minuit (heure du
    Pacifique). Le texte évite volontairement les mots-clés de quota/surcharge : les reprises automatiques ne la retentent pas
    (voir errors.classify_exception, qui la classe QUOTA_EXHAUSTED non réessayable)."""


def _pacific_day() -> date:
    """Jour courant à l'heure du Pacifique (celle de la remise à zéro des quotas Google) ; UTC-8 si la base de fuseaux manque."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Los_Angeles")).date()
    except Exception:
        return datetime.now(timezone(timedelta(hours=-8))).date()


class GeminiRateLimiter:
    """Réservation de créneaux sur une fenêtre glissante (requêtes et tokens), compteur journalier et pause commune après un
    429, pour UN modèle. Thread-safe."""

    def __init__(
        self, max_per_minute: int = DEFAULT_MAX_PER_MINUTE, window_seconds: float = WINDOW_SECONDS, *,
        max_tokens_per_minute: Optional[int] = None, max_per_day: Optional[int] = None, name: str = "défaut",
        day_fn: Callable[[], date] = _pacific_day,
    ):
        self.max_per_minute = max(1, max_per_minute)
        self.window_seconds = window_seconds
        self.max_tokens_per_minute = max_tokens_per_minute
        self.max_per_day = max_per_day
        self.name = name
        self._day_fn = day_fn
        self._day: Optional[date] = None
        self._day_count = 0
        self._entries: list[list] = []   # [instant de départ réservé, tokens], triés (peuvent être dans le futur)
        self._cooldown_until = 0.0
        self._lock = threading.Lock()

    def _count_daily_request(self) -> None:
        today = self._day_fn()
        if today != self._day:
            self._day, self._day_count = today, 0
        if self.max_per_day is not None and self._day_count >= self.max_per_day:
            raise DailyQuotaExceeded(
                f"Plafond journalier de {self.max_per_day} requêtes atteint pour le modèle {self.name} : "
                "réessayez demain (remise à zéro à minuit, heure du Pacifique)."
            )
        self._day_count += 1

    def reserve_ticket(self, now: Optional[float] = None, tokens: int = 0) -> tuple[float, list]:
        """Réserve le prochain créneau libre pour une requête de `tokens` tokens (entrée estimée) : renvoie le temps à attendre
        avant de l'envoyer et le billet à passer à `settle`. Lève DailyQuotaExceeded si le plafond journalier est atteint."""
        now = time.monotonic() if now is None else now
        with self._lock:
            # Réservations passées oubliées ; les suivantes sont servies dans l'ordre (jamais avant la dernière
            # réservée) : la liste reste triée et chaque fenêtre est vérifiée sur ses seules réservations antérieures.
            self._entries = [entry for entry in self._entries if entry[0] > now - self.window_seconds]
            self._count_daily_request()
            budget = self.max_tokens_per_minute
            # Une requête plus grosse que tout le budget ne pourrait jamais passer : elle est plafonnée à ce budget.
            tokens = min(max(0, tokens), budget) if budget else 0
            start = max(now, self._cooldown_until, self._entries[-1][0] if self._entries else 0.0)
            while True:
                # Epsilon : start = slot + fenêtre peut revenir à slot - 1e-13 en flottants ; sans lui, la boucle ne finirait pas.
                window = [entry for entry in self._entries if entry[0] > start - self.window_seconds + 1e-9]
                if len(window) >= self.max_per_minute:
                    # Fenêtre pleine : le créneau suivant s'ouvre quand la plus ancienne réservation utile en sort.
                    start = window[len(window) - self.max_per_minute][0] + self.window_seconds
                    continue
                used = sum(entry[1] for entry in window)
                if budget and tokens and used + tokens > budget:
                    # Budget de tokens dépassé : attendre que assez de réservations anciennes sortent de la fenêtre.
                    for entry in window:
                        used -= entry[1]
                        if used + tokens <= budget:
                            start = entry[0] + self.window_seconds
                            break
                    continue
                break
            ticket = [start, tokens]
            self._entries.append(ticket)
            return max(0.0, start - now), ticket

    def reserve(self, now: Optional[float] = None, tokens: int = 0) -> float:
        """Comme `reserve_ticket`, sans le billet : renvoie seulement le temps à attendre."""
        return self.reserve_ticket(now, tokens)[0]

    def settle(self, ticket: list, actual_tokens: Optional[int]) -> None:
        """Remplace l'estimation du billet par les tokens réellement consommés (entrée + sortie + réflexion)."""
        if actual_tokens is not None:
            with self._lock:
                ticket[1] = actual_tokens

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


# Limiteur par défaut : modèles absents de llm_models (RPM seul).
limiter = GeminiRateLimiter(_env_positive_int("GEMINI_MAX_REQUESTS_PER_MINUTE", DEFAULT_MAX_PER_MINUTE))

_model_limiters: dict[str, GeminiRateLimiter] = {}
_registry_lock = threading.Lock()
_installed: set[tuple[int, str]] = set()


def limiter_for(model: Optional[str]) -> GeminiRateLimiter:
    """Limiteur du modèle (créé au premier appel, avec ses plafonds effectifs de llm_models), ou le limiteur par défaut."""
    limits = llm_models.limits_for(model)
    if limits is None:
        return limiter
    key = llm_models.model_key(model)
    with _registry_lock:
        existing = _model_limiters.get(key)
        if existing is None:
            existing = _model_limiters[key] = GeminiRateLimiter(
                limits.rpm, max_tokens_per_minute=limits.tpm, max_per_day=limits.rpd, name=key,
            )
        return existing


def reset_model_limiters() -> None:
    """Oublie les limiteurs par modèle (fenêtres et compteurs journaliers) ; les plafonds sont relus au prochain appel."""
    with _registry_lock:
        _model_limiters.clear()


def _announce(wait: float, on_wait: Optional[Callable[[float], None]], lim: GeminiRateLimiter) -> None:
    if wait >= LOG_WAIT_THRESHOLD_SECONDS:
        log.info(f"requête Gemini retardée de {wait:.0f} s (limiteur {lim.name} : {lim.max_per_minute}/min, pause 429 commune)")
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
    """Limiteur du modèle de l'appel, et réservation de son créneau (tokens d'entrée estimés seulement si le modèle a un TPM)."""
    lim = limiter_for(kwargs.get("model"))
    tokens = llm_usage.estimate_prompt_tokens(kwargs.get("contents"), kwargs.get("config")) if lim.max_tokens_per_minute else 0
    wait, ticket = lim.reserve_ticket(tokens=tokens)
    return lim, wait, ticket


def _wrap_sync(function: Callable[..., Any], on_wait: Optional[Callable[[float], None]]) -> Callable[..., Any]:
    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        lim, wait, ticket = _reserve_for(kwargs)
        _announce(wait, on_wait, lim)
        if wait > 0:
            time.sleep(wait)
        try:
            response = function(*args, **kwargs)
        except Exception as error:
            _note_error(error, lim)
            raise
        lim.settle(ticket, llm_usage.total_tokens(response))
        return response
    return wrapper


def _wrap_sync_stream(function: Callable[..., Any], on_wait: Optional[Callable[[float], None]]) -> Callable[..., Any]:
    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        lim, wait, ticket = _reserve_for(kwargs)
        _announce(wait, on_wait, lim)
        if wait > 0:
            time.sleep(wait)
        actual: Optional[int] = None
        try:
            for chunk in function(*args, **kwargs):
                actual = llm_usage.total_tokens(chunk) or actual   # le décompte cumulé arrive sur les derniers morceaux
                yield chunk
        except Exception as error:
            _note_error(error, lim)
            raise
        lim.settle(ticket, actual)
    return wrapper


def _wrap_async(function: Callable[..., Any], on_wait: Optional[Callable[[float], None]]) -> Callable[..., Any]:
    @functools.wraps(function)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        lim, wait, ticket = _reserve_for(kwargs)
        _announce(wait, on_wait, lim)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            response = await function(*args, **kwargs)
        except Exception as error:
            _note_error(error, lim)
            raise
        lim.settle(ticket, llm_usage.total_tokens(response))   # flux asynchrone : l'estimation d'entrée reste comptée
        return response
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
