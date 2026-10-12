"""Fenêtre glissante de débit d'UN modèle : requêtes par minute, tokens par minute, compteur journalier, pause après un 429.

Extrait de llm_limiter (qui choisit le limiteur de chaque appel et enveloppe le client `google-genai`) : cette classe ne
connaît ni le client ni les modèles, seulement des plafonds et des instants.
"""
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from llm_quota_errors import retry_delay_seconds

DEFAULT_MAX_PER_MINUTE = 12
WINDOW_SECONDS = 60.0
# Pause appliquée à un 429 dont le délai n'est pas lisible, et marge ajoutée au délai demandé.
DEFAULT_COOLDOWN_SECONDS = 20.0
COOLDOWN_MARGIN_SECONDS = 1.0

# Pause commune maximale acceptée : au-delà (quota JOURNALIER épuisé, délai de plusieurs heures), dormir ferait croire à
# une exécution vivante alors qu'elle ne peut plus avancer. Aucune pause n'est alors posée : les appels échouent vite
# avec l'erreur d'origine (« Quota épuisé » côté utilisateur).
MAX_COOLDOWN_SECONDS = 120.0


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
        avant de l'envoyer et le billet [départ, tokens, cause de l'attente] à passer à `settle`. Lève DailyQuotaExceeded si le
        plafond journalier est atteint."""
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
            reason = "pause après un 429" if self._cooldown_until > now else "file d'attente"
            while True:
                # Epsilon : start = slot + fenêtre peut revenir à slot - 1e-13 en flottants ; sans lui, la boucle ne finirait pas.
                window = [entry for entry in self._entries if entry[0] > start - self.window_seconds + 1e-9]
                if len(window) >= self.max_per_minute:
                    # Fenêtre pleine : le créneau suivant s'ouvre quand la plus ancienne réservation utile en sort.
                    start = window[len(window) - self.max_per_minute][0] + self.window_seconds
                    reason = f"{self.max_per_minute} requêtes par minute"
                    continue
                used = sum(entry[1] for entry in window)
                if budget and tokens and used + tokens > budget:
                    # Budget de tokens dépassé : attendre que assez de réservations anciennes sortent de la fenêtre.
                    for entry in window:
                        used -= entry[1]
                        if used + tokens <= budget:
                            start = entry[0] + self.window_seconds
                            break
                    reason = f"{budget} tokens par minute"
                    continue
                break
            ticket = [start, tokens, reason]
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
