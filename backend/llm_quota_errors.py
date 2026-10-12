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
