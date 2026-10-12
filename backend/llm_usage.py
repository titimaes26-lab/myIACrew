"""Comptage des tokens d'un appel Gemini pour le limiteur de débit : estimation AVANT l'appel (entrée), valeur réelle APRÈS.

L'estimation compte les caractères du texte envoyé (messages, consigne système, schémas d'outils) à raison de 4 par token,
un ordre de grandeur volontairement un peu large : le limiteur la remplace par le total réellement facturé dès que la
réponse est connue (`usage_metadata.total_token_count`, qui inclut la sortie et la réflexion).
"""
import math
from typing import Any, Optional

CHARS_PER_TOKEN = 4
_MAX_DEPTH = 12


def _chars(value: Any, depth: int = 0) -> int:
    """Nombre de caractères de texte contenus dans `value` (chaînes, listes, dictionnaires, modèles pydantic)."""
    if value is None or depth > _MAX_DEPTH or isinstance(value, (bytes, bytearray, int, float, bool)):
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(_chars(item, depth + 1) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return sum(_chars(item, depth + 1) for item in value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return _chars(dump(exclude_none=True), depth + 1)
        except Exception:
            return 0
    return 0


def estimate_prompt_tokens(contents: Any, config: Any = None) -> int:
    """Estimation du nombre de tokens envoyés par un appel (messages + configuration : consigne système, outils)."""
    return math.ceil((_chars(contents) + _chars(config)) / CHARS_PER_TOKEN)


def total_tokens(response: Any) -> Optional[int]:
    """Total de tokens réellement consommé par la réponse (ou le dernier morceau d'un flux), ou None s'il est inconnu."""
    usage = getattr(response, "usage_metadata", None)
    total = getattr(usage, "total_token_count", None)
    return total if isinstance(total, int) and total >= 0 else None


def served_model(response: Any) -> Optional[str]:
    """Modèle que l'API déclare avoir réellement servi (`model_version`, par exemple « gemini-3.8-flash »), ou None."""
    version = getattr(response, "model_version", None)
    return version.strip() if isinstance(version, str) and version.strip() else None
