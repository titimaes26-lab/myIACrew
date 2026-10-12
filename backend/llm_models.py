"""Configuration CENTRALISÉE des modèles Gemini : quel agent utilise quel modèle, et les limites de débit de chaque modèle.

Ce module ne contient que des données et de petites fonctions pures ; il est lu par `crew_llms` (un LLM par agent) et par
`llm_limiter` (un limiteur de débit par modèle).

Limites (offre gratuite / quota standard) :

| modèle                  | RPM | TPM     | RPD   |
|-------------------------|-----|---------|-------|
| `gemini-3.8-flash`      | 10  | 250 000 | 1 500 |
| `gemini-3.5-flash-lite` | 15  | 250 000 | 1 000 |

RPM = requêtes par minute, TPM = tokens par minute (entrée + sortie), RPD = requêtes par jour (remise à zéro à minuit,
heure du Pacifique). Le limiteur applique ces plafonds AVEC UNE MARGE (`LLM_RATE_MARGIN`, 0,8 par défaut : 8 RPM sur 10,
12 sur 15), car l'estimation des tokens avant l'appel est approximative et plusieurs process peuvent partager une clé.

Réglages d'exploitation (variables d'environnement) :
- `GEMINI_MODEL` : force UN SEUL modèle pour tous les agents et le résumé (repli quand un modèle n'est pas disponible sur la
  clé) ; absent, la carte `AGENT_MODELS` s'applique.
- `GEMINI_MODEL_LIMITS` : JSON qui remplace ou ajoute des limites, par exemple pour une offre payante :
  `{"gemini-3.8-flash": {"rpm": 60, "tpm": 1000000, "rpd": 10000}}`. Une entrée illisible est ignorée.
- `LLM_RATE_MARGIN` : part des plafonds réellement utilisée, entre 0,1 et 1 (0,8 par défaut).
"""
import functools
import json
import math
import os
from dataclasses import dataclass
from typing import Optional

from logs import get_logger

log = get_logger("llm_models")


@dataclass(frozen=True)
class ModelLimits:
    """Plafonds d'un modèle : requêtes par minute, tokens par minute, requêtes par jour."""
    rpm: int
    tpm: int
    rpd: int


GEMINI_3_8_FLASH = "gemini-3.8-flash"
GEMINI_3_5_FLASH_LITE = "gemini-3.5-flash-lite"

# Plafonds OFFICIELS (sans marge) ; `limits_for` applique la marge.
MODEL_LIMITS: dict[str, ModelLimits] = {
    GEMINI_3_8_FLASH: ModelLimits(rpm=10, tpm=250_000, rpd=1_500),
    GEMINI_3_5_FLASH_LITE: ModelLimits(rpm=15, tpm=250_000, rpd=1_000),
}

# Modèle de chaque agent (clés = noms des méthodes d'agent de `AppDevelopmentCrew`, voir agentsquestion.yaml).
AGENT_MODELS: dict[str, str] = {
    "qualification_agent": GEMINI_3_5_FLASH_LITE,
    "product_designer_agent": GEMINI_3_8_FLASH,
    "architect_agent": GEMINI_3_8_FLASH,
    "diagnostic_agent": GEMINI_3_8_FLASH,
    "developer_agent": GEMINI_3_5_FLASH_LITE,
    "qa_agent": GEMINI_3_8_FLASH,
}

# Appels qui ne sont pas un agent (résumé final de l'exécution) : le modèle le plus léger.
SUMMARY_MODEL = GEMINI_3_5_FLASH_LITE

DEFAULT_RATE_MARGIN = 0.8
_PROVIDER_PREFIXES = ("gemini/", "models/")


def model_key(name: Optional[str]) -> str:
    """Identifiant canonique d'un modèle : sans préfixe de fournisseur (« gemini/ », « models/ »), en minuscules."""
    key = (name or "").strip().lower()
    for prefix in _PROVIDER_PREFIXES:
        if key.startswith(prefix):
            key = key[len(prefix):]
    return key


def rate_margin() -> float:
    """Part des plafonds réellement utilisée (LLM_RATE_MARGIN, entre 0,1 et 1) ; 0,8 par défaut ou si illisible."""
    try:
        value = float(os.getenv("LLM_RATE_MARGIN", ""))
    except ValueError:
        return DEFAULT_RATE_MARGIN
    return value if 0.1 <= value <= 1.0 else DEFAULT_RATE_MARGIN


def _limits_overrides() -> dict[str, ModelLimits]:
    return _parse_limits_overrides(os.getenv("GEMINI_MODEL_LIMITS", "").strip())


@functools.lru_cache(maxsize=8)
def _parse_limits_overrides(raw: str) -> dict[str, ModelLimits]:
    """Analyse (mémorisée par texte : une valeur illisible n'est signalée qu'UNE fois, pas à chaque appel au modèle)."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {
            model_key(name): ModelLimits(rpm=int(entry["rpm"]), tpm=int(entry["tpm"]), rpd=int(entry["rpd"]))
            for name, entry in data.items() if min(int(entry["rpm"]), int(entry["tpm"]), int(entry["rpd"])) >= 1
        }
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        log.warning(f"GEMINI_MODEL_LIMITS ignorée (JSON attendu : {{\"modèle\": {{\"rpm\": 10, \"tpm\": 250000, \"rpd\": 1500}}}}) : {type(e).__name__}: {e}")
        return {}


def official_limits(model: Optional[str]) -> Optional[ModelLimits]:
    """Plafonds du modèle SANS marge (variable GEMINI_MODEL_LIMITS prise en compte), ou None s'il est inconnu."""
    key = model_key(model)
    return _limits_overrides().get(key) or MODEL_LIMITS.get(key)


def limits_for(model: Optional[str]) -> Optional[ModelLimits]:
    """Plafonds EFFECTIFS du modèle (avec la marge de sécurité, au moins 1 partout), ou None pour un modèle inconnu."""
    official = official_limits(model)
    if official is None:
        return None
    margin = rate_margin()
    return ModelLimits(
        rpm=max(1, math.floor(official.rpm * margin)),
        tpm=max(1, math.floor(official.tpm * margin)),
        rpd=max(1, math.floor(official.rpd * margin)),
    )


def model_for_agent(agent: str) -> str:
    """Modèle d'un agent, au format attendu par CrewAI (« gemini/<modèle> »). `GEMINI_MODEL` le remplace pour tous."""
    forced = os.getenv("GEMINI_MODEL", "").strip()
    return forced if forced else f"gemini/{AGENT_MODELS[agent]}"


def summary_model() -> str:
    """Modèle du résumé final (« gemini/<modèle> »), ou celui de `GEMINI_MODEL` quand il est défini."""
    forced = os.getenv("GEMINI_MODEL", "").strip()
    return forced if forced else f"gemini/{SUMMARY_MODEL}"
