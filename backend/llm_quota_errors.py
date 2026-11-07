"""Reconnaissance des erreurs de quota (429) de Gemini et du délai que Google demande d'attendre."""
import re
from typing import Optional

# Un 429 se reconnaît à son code HTTP (exceptions google-genai : `.code`), sinon à son TEXTE : « 429 » seul est trop
# large (un « 1429 » ou un « quota » cité dans un message n'est pas un 429).
_QUOTA_TEXT = re.compile(
    r"resource_exhausted|exceeded your current quota|\b429\b[^\n]{0,80}(?:quota|rate.?limit|too many requests)",
    re.IGNORECASE,
)
_RETRY_IN = re.compile(r"retry in ([0-9]+(?:\.[0-9]+)?)\s*s", re.IGNORECASE)
_RETRY_DELAY = re.compile(r"retryDelay['\"]?\s*:\s*['\"]([0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE)



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


_UNAVAILABLE_TEXT = re.compile(r"\bunavailable\b|high demand|overloaded", re.IGNORECASE)
# Modèle retiré ou absent de la clé : « 404 NOT_FOUND : models/<nom> is not found for API version … ». Un 404 qui ne parle pas d'un MODÈLE
# (une URL, une ressource) n'en est pas un.
_MODEL_MISSING_TEXT = re.compile(r"models?/[\w.\-]+ is not found|model [\w.\-/]+ (?:is )?not found", re.IGNORECASE)
_UNAVAILABLE_CODES = (500, 502, 503)    # PAS 504 : c'est le délai que nous imposons à l'appel (DEADLINE_EXCEEDED)


def is_unavailable_error(error: BaseException) -> bool:
    """Modèle surchargé, en panne ou introuvable côté Google (503 « high demand », 500, 502, 404 « model is not found ») : un autre modèle peut répondre."""
    if any(getattr(error, attr, None) in _UNAVAILABLE_CODES for attr in ("code", "status_code")):
        return True
    text = str(error)
    return _UNAVAILABLE_TEXT.search(text) is not None or _MODEL_MISSING_TEXT.search(text) is not None


_DEADLINE_TEXT = re.compile(r"deadline[_ ]exceeded|deadline expired", re.IGNORECASE)


def is_deadline_error(error: BaseException) -> bool:
    """Délai de l'appel dépassé côté Google (504 DEADLINE_EXCEEDED) : le modèle n'a pas répondu avant notre délai (X-Server-Timeout)."""
    if any(getattr(error, attr, None) == 504 for attr in ("code", "status_code")):
        return True
    return _DEADLINE_TEXT.search(str(error)) is not None
