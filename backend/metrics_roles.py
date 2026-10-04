"""Rôles des agents : correspondance rôle CrewAI -> étape du pipeline, libellés et ordre d'affichage."""
import re
from pathlib import Path
from typing import Optional

import yaml

from logs import get_logger

log = get_logger("metrics")

SYSTEM_BUCKET = "system"  # appels LLM hors agent (ex : synthèse finale du résultat)

OTHER_BUCKET = "other"

# Ordre du pipeline, clés alignées sur WORKFLOW_STEPS côté frontend.
PIPELINE_ORDER = ["design", "architecture", "diagnostic", "development", "qa", SYSTEM_BUCKET, OTHER_BUCKET]

STEP_BY_AGENT_CONFIG = {
    "product_designer_agent": "design",
    "architect_agent": "architecture",
    "diagnostic_agent": "diagnostic",
    "developer_agent": "development",
    "qa_agent": "qa",
}

AGENT_LABELS = {
    "design": "Conception",
    "architecture": "Architecture",
    "diagnostic": "Diagnostic",
    "development": "Développement",
    "qa": "QA",
    SYSTEM_BUCKET: "Synthèse (hors agent)",
    OTHER_BUCKET: "Autre",
}

def _normalize_role(role: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (role or "").strip()).lower()

def _load_role_map() -> dict[str, str]:
    """{rôle normalisé -> clé d'étape} lu dans agentsquestion.yaml : le rôle d'un événement CrewAI
    est le texte de `role:`, la seule source de vérité pour relier un appel à son agent."""
    try:
        config = yaml.safe_load((Path(__file__).resolve().parent / "agentsquestion.yaml").read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        _normalize_role(config[name]["role"]): step
        for name, step in STEP_BY_AGENT_CONFIG.items()
        if isinstance(config.get(name), dict) and config[name].get("role")
    }

ROLE_TO_STEP = _load_role_map()

def step_for_role(role: Optional[str]) -> str:
    if not role or not role.strip():
        return SYSTEM_BUCKET
    return ROLE_TO_STEP.get(_normalize_role(role), OTHER_BUCKET)
