"""Identifiant de corrélation d'UNE exécution (« run-<id d'historique> ») : le même dans chaque ligne de journal et, plus tard,
dans les traces. Déterministe (dérivé de l'id en base) : on retrouve l'exécution d'une ligne de log, et inversement.

ContextVar : copié dans les threads lancés par `asyncio.to_thread` (le crew tourne ainsi) ; hors exécution, aucun identifiant."""
from contextlib import contextmanager
from contextvars import ContextVar
import functools
import inspect
from typing import Any, Callable, Iterator, Optional

_run_id: ContextVar[Optional[str]] = ContextVar("run_id", default=None)


def run_id_for(execution_id: int) -> str:
    return f"run-{execution_id}"


def current_run_id() -> Optional[str]:
    return _run_id.get()


@contextmanager
def track_run(execution_id: int) -> Iterator[str]:
    """Pose l'identifiant le temps de l'exécution ; restauré à la sortie, même sur exception."""
    run_id = run_id_for(execution_id)
    token = _run_id.set(run_id)
    try:
        yield run_id
    finally:
        _run_id.reset(token)


def tracked(param: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Décorateur de coroutine : toute la fonction (échec compris) s'exécute sous l'identifiant dérivé de son argument `param`."""
    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        signature = inspect.signature(func)

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            with track_run(signature.bind(*args, **kwargs).arguments[param]):
                return await func(*args, **kwargs)
        return wrapper
    return decorate
