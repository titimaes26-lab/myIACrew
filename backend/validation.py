"""Validation des entrées de /api/execute et /api/qualify : fonctions pures, messages en français.

Objectif : refuser tout de suite (422, un message par champ) une faute de frappe qui, sinon, ne se
découvrirait qu'au milieu d'une exécution de plusieurs minutes, après avoir consommé le quota LLM.
"""
import re
from typing import Optional

MAX_REQUEST_CHARS = 20_000
ALLOWED_WORKFLOWS = ("ANALYSE_ONLY", "BUGFIX", "FEATURE", "DESIGN_AND_DEV")

_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
# Interdits dans un nom de référence git (git check-ref-format) : contrôle, espace, ~ ^ : ? * [ \
_BRANCH_FORBIDDEN = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


def empty_to_none(value: Optional[str]) -> Optional[str]:
    """Le frontend peut envoyer "" pour « pas de repository » : traité comme absent."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def validate_repo_owner(value: Optional[str]) -> Optional[str]:
    value = empty_to_none(value)
    if value is not None and not _OWNER.match(value):
        raise ValueError(
            "propriétaire GitHub invalide (lettres, chiffres et tirets, 39 caractères au plus, "
            "ni début ni fin par un tiret)"
        )
    return value


def validate_repo_name(value: Optional[str]) -> Optional[str]:
    value = empty_to_none(value)
    if value is not None and (not _REPO.match(value) or value in (".", "..")):
        raise ValueError("nom de repository invalide (lettres, chiffres, « . », « _ » et « - », 100 caractères au plus)")
    return value


def validate_branch_name(value: Optional[str]) -> Optional[str]:
    value = empty_to_none(value)
    if value is None:
        return None
    invalid = (
        len(value) > 255
        or _BRANCH_FORBIDDEN.search(value)
        or ".." in value
        or "@{" in value
        or "//" in value
        or value.startswith(("-", "/"))
        or value.endswith(("/", ".", ".lock"))
        or value == "@"
    )
    if invalid:
        raise ValueError("nom de branche invalide (sans espace ni « .. », « ~ », « ^ », « : », « ? », « * », « [ », « \\ »)")
    return value


def validate_user_request(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("la demande ne peut pas être vide")
    if len(value) > MAX_REQUEST_CHARS:
        raise ValueError(f"la demande est trop longue ({MAX_REQUEST_CHARS} caractères au plus)")
    return value


def validate_clarifications(value: Optional[str]) -> Optional[str]:
    if value is not None and len(value) > MAX_REQUEST_CHARS:
        raise ValueError(f"les précisions sont trop longues ({MAX_REQUEST_CHARS} caractères au plus)")
    return value


def validate_workflow(value: str) -> str:
    if value not in ALLOWED_WORKFLOWS:
        raise ValueError(f"workflow inconnu (attendu : {', '.join(ALLOWED_WORKFLOWS)})")
    return value
