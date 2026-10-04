"""Reprise sur erreur transitoire (quota, indisponibilité) et pause adaptative entre appels."""
import asyncio
import functools
import re
import time
from typing import Any, Callable

from metrics_collect import _current_metrics
from errors import QUOTA_MARKERS, UNAVAILABLE_MARKERS

# Partagés entre retry_on_rate_limit_async (utilisé par analyze_user_request) et le
# retry résumable de crew_run.CrewRun : une seule définition de "qu'est-ce qu'une
# erreur transitoire" et "combien de temps attendre", pour que les deux mécanismes de retry ne
# puissent jamais diverger silencieusement si l'un est mis à jour (ex: nouveau message d'erreur
# Gemini à reconnaître) sans que l'autre le soit.
def _is_retryable_error(err_msg: str) -> bool:
    return any(marker in err_msg for marker in QUOTA_MARKERS + UNAVAILABLE_MARKERS)

def _compute_backoff_wait(err_msg: str, retries: int, base_delay: float) -> float:
    match = re.search(r'retry after (\d+(\.\d+)?)', err_msg)
    return float(match.group(1)) + 2.0 if match else base_delay * (2 ** (retries - 1))

def retry_on_rate_limit_async(max_retries: int = 5, base_delay: float = 10.0):
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            retries = 0
            while True:
                m = _current_metrics.get()
                if m is not None:
                    m.record_attempt()
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    err_msg = str(e).lower()
                    if _is_retryable_error(err_msg):
                        retries += 1
                        if retries > max_retries:
                            raise e
                        wait_time = _compute_backoff_wait(err_msg, retries, base_delay)
                        if m is not None:
                            m.record_rate_limit(wait_time)
                        await asyncio.sleep(wait_time)
                    else:
                        raise e
        return wrapper
    return decorator

class QuotaManager:
    def __init__(self):
        self.last_execution_time = 0.0
        self.min_interval_seconds = 5.0

    def adaptive_pause(self, task_output=None):
        elapsed = time.time() - self.last_execution_time
        if elapsed < self.min_interval_seconds:
            wait_time = self.min_interval_seconds - elapsed
            m = _current_metrics.get()
            if m is not None:
                m.record_wait(wait_time)
            time.sleep(wait_time)
        self.last_execution_time = time.time()

quota_mgr = QuotaManager()

def _record_limiter_wait(wait_seconds: float) -> None:
    """Attente imposée par le limiteur global Gemini : comptée dans les mesures de l'exécution quand le contexte la voit."""
    metrics = _current_metrics.get()
    if metrics is not None:
        metrics.record_wait(wait_seconds)
