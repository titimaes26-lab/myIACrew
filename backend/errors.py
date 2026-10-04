"""Erreurs applicatives typées : un code stable (exploitable par l'interface) + un message
lisible, au lieu de `str(exc)` brut ou d'un `except Exception` muet.

Deux usages :
- côté API : `AppError` (levée par un endpoint) et les gestionnaires d'exceptions de main.py, qui
  renvoient toujours `{"detail": message, "code": CODE, "retryable": bool}` (`detail` reste la
  clé historique lue par le frontend) ;
- côté exécution du crew : `classify_exception` range un échec dans un code, persisté sur
  `ExecutionHistory.error_code` pour que l'interface propose l'action adaptée.
"""
import asyncio
import re
from typing import NamedTuple, Optional


class ErrorCode:
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"
    GITHUB_UNAVAILABLE = "GITHUB_UNAVAILABLE"
    GUARDRAIL_FAILED = "GUARDRAIL_FAILED"
    DELIVERY_FAILED = "DELIVERY_FAILED"
    INTERRUPTED = "INTERRUPTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    HTTP_ERROR = "HTTP_ERROR"


# Source unique des « erreurs transitoires » du fournisseur LLM (utilisée aussi par les retries de
# crewquestion.py) : une nouvelle formulation à reconnaître ne se corrige qu'ici.
QUOTA_MARKERS = ("429", "resource_exhausted", "rate limit", "quota")
UNAVAILABLE_MARKERS = ("503", "unavailable", "high demand", "overloaded")
GUARDRAIL_MARKERS = ("guardrail", "garde-fou")
TIMEOUT_MARKERS = ("timed out", "timeout")

_STATUS_CODES = {
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.QUOTA_EXHAUSTED,
    400: ErrorCode.VALIDATION_ERROR,
    500: ErrorCode.INTERNAL_ERROR,
    502: ErrorCode.SERVICE_UNAVAILABLE,
    503: ErrorCode.SERVICE_UNAVAILABLE,
    504: ErrorCode.SERVICE_UNAVAILABLE,
}
_RETRYABLE_STATUSES = {429, 502, 503, 504}

_USER_MESSAGES = {
    ErrorCode.QUOTA_EXHAUSTED: "Le quota du modèle IA est épuisé pour le moment. Réessayez dans quelques minutes.",
    ErrorCode.LLM_UNAVAILABLE: "Le modèle IA est momentanément indisponible ou surchargé. Réessayez dans quelques minutes.",
    ErrorCode.LLM_TIMEOUT: "Le modèle IA a mis trop de temps à répondre. Réessayez.",
    ErrorCode.EXECUTION_TIMEOUT: "L'exécution a dépassé sa durée maximale et a été arrêtée pour libérer le service. Réessayez, ou simplifiez la demande.",
    ErrorCode.GITHUB_UNAVAILABLE: "GitHub est momentanément injoignable : la livraison n'a pas pu être vérifiée. Réessayez.",
    ErrorCode.GUARDRAIL_FAILED: "Le résultat produit ne respecte pas les contrôles de qualité. Reformulez ou précisez la demande.",
}
# Variante d'EXECUTION_TIMEOUT quand l'attente du quota a dominé le temps passé (voir ExecutionTimeoutError).
_QUOTA_TIMEOUT_MESSAGE = (
    "L'exécution a dépassé sa durée maximale, en grande partie à cause de l'attente du quota du modèle IA (service saturé), "
    "et a été arrêtée pour libérer le service. Réessayez dans quelques minutes."
)


class DeliveryError(RuntimeError):
    """Livraison GitHub non confirmée après l'exécution (aucune branche, aucun commit, aucune PR). Son texte porte
    déjà le constat et le rapport de l'agent : le classement ne lui ajoute donc pas de message lisible."""


class ExecutionTimeoutError(RuntimeError):
    """Le crew a dépassé la durée maximale d'une exécution (EXECUTION_TIMEOUT_S, voir main.py).
    `quota_wait_seconds` : part de ce temps passée à attendre le quota du modèle (pauses du limiteur, backoff) ; quand elle
    est importante, la cause réelle est la saturation du quota, pas la taille de la demande."""

    def __init__(self, message: str, quota_wait_seconds: float = 0.0, limit_seconds: float = 0.0):
        super().__init__(message)
        self.quota_wait_seconds = quota_wait_seconds
        self.quota_dominant = limit_seconds > 0 and quota_wait_seconds >= QUOTA_DOMINANT_SHARE * limit_seconds


# Part du délai passée à attendre le quota à partir de laquelle l'arrêt est attribué au quota.
QUOTA_DOMINANT_SHARE = 0.25


class ErrorInfo(NamedTuple):
    code: str
    retryable: bool
    # Message à destination de l'utilisateur ; None quand l'erreur n'est pas reconnue (l'appelant
    # choisit alors : message générique côté API, texte d'origine côté historique d'exécution).
    message: Optional[str]


class AppError(Exception):
    """Erreur d'API volontaire : statut HTTP, code stable, message lisible."""

    def __init__(self, status_code: int, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable


def code_for_status(status_code: int) -> str:
    return _STATUS_CODES.get(status_code, ErrorCode.HTTP_ERROR)


def is_retryable_status(status_code: int) -> bool:
    return status_code in _RETRYABLE_STATUSES


def error_body(message: str, code: str, retryable: bool = False, **extra) -> dict:
    """Corps JSON unique de toutes les erreurs de l'API."""
    return {"detail": message, "code": code, "retryable": retryable, **extra}


def _matches(markers, text: str) -> bool:
    # Mots entiers (\b) : « 429 » ne doit pas reconnaître « page_4290.tsx », ni « unavailable » le
    # nom d'une classe. Les retries de crewquestion.py gardent, eux, la recherche par sous-chaîne.
    return any(re.search(rf"\b{re.escape(marker)}\b", text) for marker in markers)


def _exception_chain(exc: Optional[BaseException]):
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen and len(seen) < 8:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def classify_exception(exc: BaseException) -> ErrorInfo:
    """Range un échec d'exécution/appel LLM dans un code stable (jamais d'exception).
    CrewStepError ne garde que le texte de l'erreur d'origine : on remonte donc la chaîne
    `__cause__`/`__context__` pour retrouver son type (timeout, GitHub indisponible)."""
    chain = list(_exception_chain(exc))
    # En premier et sur toute la chaîne : le rapport joint de l'agent peut citer « quota » ou « timeout », qui
    # feraient classer à tort l'exception englobante par son texte.
    if any(isinstance(err, DeliveryError) for err in chain):
        return ErrorInfo(ErrorCode.DELIVERY_FAILED, False, None)
    for err in chain:
        info = _classify_single(err)
        if info.code != ErrorCode.INTERNAL_ERROR:
            return info
    return ErrorInfo(ErrorCode.INTERNAL_ERROR, False, None)


def _classify_single(exc: BaseException) -> ErrorInfo:
    # GitHubVerificationUnavailable est importée ici pour ne pas lier ce module à github_snapshot
    # à l'import (github_snapshot importe PyGithub).
    try:
        from github_snapshot import GitHubVerificationUnavailable
        if isinstance(exc, GitHubVerificationUnavailable):
            return ErrorInfo(ErrorCode.GITHUB_UNAVAILABLE, True, _USER_MESSAGES[ErrorCode.GITHUB_UNAVAILABLE])
    except Exception:
        pass
    if isinstance(exc, ExecutionTimeoutError):
        message = _QUOTA_TIMEOUT_MESSAGE if exc.quota_wait_seconds > 0 and exc.quota_dominant else _USER_MESSAGES[ErrorCode.EXECUTION_TIMEOUT]
        return ErrorInfo(ErrorCode.EXECUTION_TIMEOUT, True, message)
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return ErrorInfo(ErrorCode.LLM_TIMEOUT, True, _USER_MESSAGES[ErrorCode.LLM_TIMEOUT])
    text = str(exc).lower()
    for markers, code, retryable in (
        (QUOTA_MARKERS, ErrorCode.QUOTA_EXHAUSTED, True),
        (UNAVAILABLE_MARKERS, ErrorCode.LLM_UNAVAILABLE, True),
        (TIMEOUT_MARKERS, ErrorCode.LLM_TIMEOUT, True),
        (GUARDRAIL_MARKERS, ErrorCode.GUARDRAIL_FAILED, False),
    ):
        if _matches(markers, text):
            return ErrorInfo(code, retryable, _USER_MESSAGES[code])
    return ErrorInfo(ErrorCode.INTERNAL_ERROR, False, None)


def http_status_for(code: str) -> int:
    return {
        ErrorCode.QUOTA_EXHAUSTED: 429,
        ErrorCode.LLM_UNAVAILABLE: 503,
        ErrorCode.GITHUB_UNAVAILABLE: 503,
        ErrorCode.LLM_TIMEOUT: 504,
        ErrorCode.EXECUTION_TIMEOUT: 504,
    }.get(code, 500)
