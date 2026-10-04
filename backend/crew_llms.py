"""Configuration des modèles : variables d'environnement, réglage LiteLLM, un LLM par nature de travail, planification du diagnostic."""
import os
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv

# --- CHARGEMENT DES VARIABLES D'ENVIRONNEMENT ---
load_dotenv(dotenv_path=Path(__file__).resolve().parent / '.env')

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Configuration LiteLLM & Quotas (avant l'import de crewai)
os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY or ""
os.environ["LITELLM_NUM_RETRIES"] = "7"
os.environ["LITELLM_TIME_CONTINUOUS_BACKOFF"] = "2"

from crewai import LLM  # noqa: E402
from crewai.agent.planning_config import PlanningConfig  # noqa: E402

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini/gemini-3.5-flash-lite")

DIAGNOSTIC_PLAN_MAX_STEPS = 5

DIAGNOSTIC_STEP_MAX_ITERATIONS = 8   # défaut CrewAI : 15 ; assez pour lister puis lire quelques fichiers dans une étape

_REASONING_EFFORTS: tuple[Literal["low", "medium", "high"], ...] = ("low", "medium", "high")

def _diagnostic_reasoning_effort() -> Literal["low", "medium", "high"]:
    """Effort de planification du diagnostic (DIAGNOSTIC_REASONING_EFFORT : low | medium | high) ; « low » par défaut,
    y compris pour une valeur illisible."""
    value = os.getenv("DIAGNOSTIC_REASONING_EFFORT", "").strip().lower()
    for effort in _REASONING_EFFORTS:
        if value == effort:
            return effort
    return "low"

def _diagnostic_step_max_iterations() -> int:
    """Itérations du modèle par étape du plan (DIAGNOSTIC_STEP_MAX_ITERATIONS) ; entier >= 1, sinon le défaut."""
    try:
        value = int(os.getenv("DIAGNOSTIC_STEP_MAX_ITERATIONS", ""))
    except ValueError:
        return DIAGNOSTIC_STEP_MAX_ITERATIONS
    return value if value >= 1 else DIAGNOSTIC_STEP_MAX_ITERATIONS

def _diagnostic_planning_config() -> PlanningConfig:
    return PlanningConfig(
        reasoning_effort=_diagnostic_reasoning_effort(),
        max_attempts=1,
        max_steps=DIAGNOSTIC_PLAN_MAX_STEPS,
        max_step_iterations=_diagnostic_step_max_iterations(),
    )

def _make_llm(temperature: float, request_timeout: int = 120, max_tokens: Optional[int] = None) -> LLM:
    # max_tokens borne la SORTIE d'un appel (la génération domine la latence) ; absent, le plafond du fournisseur.
    extra = {"max_tokens": max_tokens} if max_tokens else {}
    return LLM(model=MODEL_NAME, api_key=GEMINI_API_KEY, temperature=temperature, request_timeout=request_timeout, **extra)

# Une température par nature de travail, au lieu d'un 0.7 unique : classer, recopier ou
# vérifier demande de la constance ; seule la conception fonctionnelle gagne à rester créative.
# request_timeout borne UN appel LLM (pas toute la tâche, qui peut en enchaîner max_iter) : un
# timeout unique de 120s pour tous les agents faisait attendre aussi longtemps un appel de
# classification JSON (qualification) qu'une génération de fichiers complets (diagnostic) avant
# de considérer l'appel bloqué et de déclencher le retry litellm — au détriment de la détection
# rapide d'un appel réellement figé sur les agents les plus légers.
qualification_llm = _make_llm(0.1, request_timeout=45)   # sortie JSON structurée, sans outil

designer_llm = _make_llm(0.5, request_timeout=90)        # texte de specs + quelques lectures

def _architect_max_tokens() -> int:
    """Plafond de sortie de l'Architecte (ARCHITECT_MAX_OUTPUT_TOKENS, 8192 par défaut). Sur les modèles Gemini
    récents les tokens de réflexion comptent dans cette limite : un plafond trop juste coupe le plan."""
    try:
        value = int(os.getenv("ARCHITECT_MAX_OUTPUT_TOKENS", ""))
    except ValueError:
        return 8192
    return value if value >= 1024 else 8192

architect_llm = _make_llm(0.3, request_timeout=90, max_tokens=_architect_max_tokens())  # ~1000 mots + une ligne de contrat par fichier

diagnostic_llm = _make_llm(0.2, request_timeout=120)     # génère le code source COMPLET des fichiers : le plus volumineux

developer_llm = _make_llm(0.0, request_timeout=90)       # appels d'outils, mais reçoit en CONTEXTE le code

                                                          # complet de diagnostic_task (potentiellement volumineux,
                                                          # voir diagnostic_llm) et peut devoir en recopier des
                                                          # extraits dans un appel github_write_file(s) de repli
                                                          # (voir developer_agent) : pas aussi bas que le 60s
                                                          # initialement envisagé pour "peu de texte généré", pour
                                                          # ne pas risquer de couper ce repli sur une grosse livraison.
qa_llm = _make_llm(0.2, request_timeout=90)               # appels d'outils + rapport final

# Timeout dédié, plus court que celui des agents (120s) : un résumé qui traîne ne doit
# pas ajouter jusqu'à 2 minutes à une réponse dont le vrai travail est déjà terminé.
# 25 et non 20 : le résumé demande désormais 4-6 phrases (au lieu de 3-5) plus, le cas
# échéant, la justification des choix (voir _build_summary_prompt), une génération
# légèrement plus longue qui reprenait la marge de cette valeur sans que celle-ci ait
# été ajustée en conséquence.
summary_llm = LLM(model=MODEL_NAME, api_key=GEMINI_API_KEY, temperature=0.5, request_timeout=25)
