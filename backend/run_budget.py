"""Budget de tokens d'UNE exécution : disjoncteur qui l'arrête proprement quand elle consomme trop (boucle d'un agent, demande
démesurée), avant que la facture ou le quota ne soient épuisés. La durée maximale, elle, est `EXECUTION_TIMEOUT_S` (execution_state).

`RUN_MAX_TOKENS` : plafond de tokens (entrée + sortie + réflexion) par exécution, 1 000 000 par défaut ; minimum 10 000.
Le compteur est tenu par `llm_limiter` : `enforce()` AVANT chaque appel au modèle (aucun créneau de débit n'est réservé pour un
appel refusé), `add()` APRÈS chaque appel (total réel, ou l'estimation d'entrée quand l'API n'en donne pas).

Le message d'erreur ne contient AUCUN nombre : `crew_retry` reconnaît les erreurs de quota par sous-chaîne (« 429 »...) et un
compteur de 1 042 900 tokens ferait rejouer l'appel refusé. Les chiffres vont dans le journal."""
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

from logs import get_logger

log = get_logger("run_budget")

DEFAULT_MAX_TOKENS = 1_000_000
MIN_MAX_TOKENS = 10_000
MESSAGE = "budget de tokens de l'exécution atteint : l'exécution est arrêtée avant d'en consommer davantage"


class TokenBudgetExceeded(RuntimeError):
    """Le budget de tokens de l'exécution est atteint ; jamais rejouée (voir errors.TOKEN_BUDGET_EXCEEDED)."""


def max_tokens() -> int:
    try:
        value = int(os.getenv("RUN_MAX_TOKENS", ""))
    except ValueError:
        return DEFAULT_MAX_TOKENS
    return value if value >= MIN_MAX_TOKENS else DEFAULT_MAX_TOKENS


class TokenBudget:
    """Compteur partagé par tous les threads de l'exécution (le crew appelle le modèle depuis plusieurs)."""

    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0
        self._lock = threading.Lock()

    def add(self, tokens: int) -> None:
        with self._lock:
            self.used += max(0, tokens)

    def enforce(self) -> None:
        with self._lock:
            used = self.used
        if used >= self.limit:
            log.warning(f"budget de tokens atteint : {used} utilisés pour un plafond de {self.limit} (RUN_MAX_TOKENS)")
            raise TokenBudgetExceeded(MESSAGE)


_budget: ContextVar[Optional[TokenBudget]] = ContextVar("run_budget", default=None)


@contextmanager
def track_budget(limit: Optional[int] = None) -> Iterator[TokenBudget]:
    """Budget de l'exécution en cours (copié dans les threads de `asyncio.to_thread`) ; retiré à la sortie."""
    budget = TokenBudget(limit if limit is not None else max_tokens())
    token = _budget.set(budget)
    try:
        yield budget
    finally:
        _budget.reset(token)


def enforce() -> None:
    """Refuse l'appel si le budget est atteint ; sans effet hors exécution."""
    budget = _budget.get()
    if budget is not None:
        budget.enforce()


def add(tokens: int) -> None:
    budget = _budget.get()
    if budget is not None:
        budget.add(tokens)
