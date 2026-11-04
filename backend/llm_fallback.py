"""Modèle de repli quand le modèle principal est surchargé (503 « high demand » de Gemini).

Après `THRESHOLD` erreurs d'indisponibilité CONSÉCUTIVES sur un modèle, les appels suivants passent sur le modèle de repli
(`GEMINI_FALLBACK_MODEL`, défaut `gemini-3.5-flash-lite`) pendant `LLM_FALLBACK_TTL_S` secondes (600 par défaut), puis le modèle
principal est réessayé. Un succès remet le compteur à zéro. Module pur (aucune E/S) : jamais le texte échangé dans les journaux.
"""
import os
import threading
import time
from typing import Optional

from logs import get_logger

log = get_logger("llm_fallback")

THRESHOLD = 2
DEFAULT_FALLBACK_MODEL = "gemini-3.5-flash-lite"
DEFAULT_TTL_S = 600.0
MAX_TTL_S = 86_400.0
_DISABLED = {"off", "none", "false", "0", "non"}

_lock = threading.Lock()
_consecutive: dict[str, int] = {}
_active_until: dict[str, float] = {}


def fallback_model() -> Optional[str]:
    """Modèle de repli (GEMINI_FALLBACK_MODEL), None s'il est désactivé (« off »). Variable absente : le défaut ; vide : désactivé."""
    raw = os.getenv("GEMINI_FALLBACK_MODEL")
    if raw is None:
        return DEFAULT_FALLBACK_MODEL
    value = raw.strip()
    return None if not value or value.lower() in _DISABLED else value


def _ttl() -> float:
    try:
        value = float(os.getenv("LLM_FALLBACK_TTL_S", ""))
    except ValueError:
        return DEFAULT_TTL_S
    return value if 0 < value <= MAX_TTL_S else DEFAULT_TTL_S


def replacement_for(model_key: str, now: Optional[float] = None) -> Optional[str]:
    """Modèle de repli à utiliser À LA PLACE de `model_key` tant que son repli est actif, sinon None."""
    target = fallback_model()
    if target is None or target == model_key:
        return None
    now = time.monotonic() if now is None else now
    with _lock:
        return target if _active_until.get(model_key, 0.0) > now else None


def note_unavailable(model_key: str, now: Optional[float] = None) -> bool:
    """Compte une erreur d'indisponibilité ; True quand CET appel vient de déclencher le repli (il doit être rejoué dessus)."""
    target = fallback_model()
    if target is None or target == model_key:
        return False
    now = time.monotonic() if now is None else now
    with _lock:
        count = _consecutive.get(model_key, 0) + 1
        _consecutive[model_key] = count
        if count < THRESHOLD or _active_until.get(model_key, 0.0) > now:
            return False
        _active_until[model_key] = now + _ttl()
        _consecutive[model_key] = 0
    log.warning(f"modèle {model_key} surchargé ({THRESHOLD} erreurs de suite) : repli sur {target} pendant {_ttl():.0f} s")
    return True


def note_success(model_key: str) -> None:
    with _lock:
        _consecutive.pop(model_key, None)


def reset() -> None:
    with _lock:
        _consecutive.clear()
        _active_until.clear()
