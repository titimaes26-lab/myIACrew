"""Limites par utilisateur : sans elles, un seul compte peut saturer les exécutions (sémaphore global) et le quota
Gemini partagé par tous.

- Exécutions : plafond d'exécutions simultanées et d'exécutions sur la dernière heure, comptés EN BASE (valables avec
  plusieurs instances du backend).
- Qualifications : fenêtre glissante EN MÉMOIRE (par process : un second process aurait sa propre fenêtre).
Réglables par variables d'environnement ; une valeur absente ou illisible garde le défaut.
"""
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Collection, Optional

from sqlmodel import Session, col, select

from database import ExecutionHistory, _env_int
from errors import AppError, ErrorCode
from orphans import _aware, sweep_stale_executions

MAX_RUNNING_PER_USER = _env_int("MAX_USER_RUNNING_EXECUTIONS", 2, 1)
MAX_EXECUTIONS_PER_HOUR = _env_int("MAX_USER_EXECUTIONS_PER_HOUR", 30, 1)
QUALIFY_PER_MINUTE = _env_int("MAX_USER_QUALIFY_PER_MINUTE", 20, 1)


def _rate_limited(message: str) -> AppError:
    return AppError(429, ErrorCode.RATE_LIMITED, message, True)


def check_user_execution_quota(
    session: Session, user_id: Optional[str], active_ids: Collection[int] = (), now: Optional[datetime] = None,
    max_running: Optional[int] = None, max_per_hour: Optional[int] = None,
) -> None:
    """Lève une AppError 429 (réessayable) quand l'utilisateur a trop d'exécutions en cours ou lancées depuis une heure."""
    if not user_id:
        return
    now = now or datetime.now(timezone.utc)
    max_running = MAX_RUNNING_PER_USER if max_running is None else max_running
    max_per_hour = MAX_EXECUTIONS_PER_HOUR if max_per_hour is None else max_per_hour

    # Une exécution morte (crash, redéploiement) ne compte pas : balayée avant de compter.
    sweep_stale_executions(session, active_ids=active_ids, user_id=user_id, now=now)
    running = session.exec(
        select(ExecutionHistory.id).where(ExecutionHistory.user_id == user_id, ExecutionHistory.status == "running")
    ).all()
    if len(running) >= max_running:
        raise _rate_limited(
            f"Vous avez déjà {len(running)} exécution{'s' if len(running) > 1 else ''} en cours (maximum {max_running}). "
            "Attendez la fin de l'une d'elles avant d'en lancer une autre."
        )

    # Âge jugé en Python (voir orphans) : la max_per_hour-ième exécution la plus récente date de moins d'une heure
    # <=> au moins max_per_hour exécutions sur la dernière heure.
    recent = session.exec(
        select(ExecutionHistory.created_at).where(ExecutionHistory.user_id == user_id)
        .order_by(col(ExecutionHistory.created_at).desc()).limit(max_per_hour)
    ).all()
    if len(recent) >= max_per_hour and _aware(recent[-1]) >= now - timedelta(hours=1):
        raise _rate_limited(
            f"Limite atteinte : {max_per_hour} exécutions par heure. Réessayez dans quelques minutes."
        )


class SlidingWindowLimiter:
    """Au plus `limit` événements par clé sur `window_seconds` secondes (en mémoire, thread-safe)."""

    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            # Clés de comptes inactifs : purgées au passage pour que la table ne grossisse pas indéfiniment.
            if len(self._hits) > 1024:
                for stale in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
                    del self._hits[stale]
            return True


qualify_limiter = SlidingWindowLimiter(QUALIFY_PER_MINUTE)


def check_qualify_rate(user_id: Optional[str]) -> None:
    if user_id and not qualify_limiter.allow(user_id):
        raise _rate_limited("Trop de qualifications en peu de temps : patientez une minute puis réessayez.")
