"""Parties du résumé final calculées SANS modèle : ligne de faits et résumé de repli.

Les chiffres (étapes jouées, fichiers produits, verdict QA) viennent du code, jamais d'un LLM : le
modèle ne peut donc pas les inventer, et ils restent présents quand sa génération échoue.
"""
import re
from typing import Iterable, Optional

from analyst_output import parse_file_sections
from qa_report import final_verdict

MAX_FALLBACK_LINE_CHARS = 220
_MARKDOWN_EMPHASIS = re.compile(r"\*\*|__|`")
_NOISE_LINE = re.compile(r"^(#|<!--|```|---|\|)")

VERDICT_LABELS = {"GO": "GO", "GO_AVEC_RESERVES": "GO avec réserves", "NO_GO": "NO GO"}


def _is_role(agent_name: str, keyword: str) -> bool:
    return keyword.lower() in agent_name.lower()


def delivery_facts(sections: Iterable[tuple[str, str]], request_type: Optional[str] = None,
                   scope: Optional[str] = None) -> str:
    """Une ligne de faits : type de demande, étapes, fichiers produits par l'Analyste, verdict QA."""
    sections = list(sections)
    if not sections:
        return ""
    parts = []
    if request_type:
        label = request_type + (" (petite)" if request_type == "FEATURE" and scope == "PETIT" else "")
        parts.append(f"type : {label}")
    parts.append(f"{len(sections)} étape{'s' if len(sections) > 1 else ''} terminée{'s' if len(sections) > 1 else ''}")
    for agent_name, raw in sections:
        if _is_role(agent_name, "diagnostic"):
            files, broken = parse_file_sections(raw)
            if files or broken:
                text = f"{len(files)} fichier{'s' if len(files) > 1 else ''} produit{'s' if len(files) > 1 else ''}"
                if broken:
                    text += f", {len(broken)} inexploitable{'s' if len(broken) > 1 else ''}"
                parts.append(text)
        elif _is_role(agent_name, "qa"):
            verdict = final_verdict(raw)
            if verdict:
                parts.append(f"verdict QA : {VERDICT_LABELS.get(verdict, verdict)}")
    return "**Faits :** " + " · ".join(parts)


def _first_meaningful_line(raw: str) -> str:
    for line in (raw or "").splitlines():
        text = _MARKDOWN_EMPHASIS.sub("", line.strip().lstrip(">-• ")).strip()
        if text and not _NOISE_LINE.match(line.strip()):
            return text[:MAX_FALLBACK_LINE_CHARS] + ("…" if len(text) > MAX_FALLBACK_LINE_CHARS else "")
    return ""


def fallback_summary(sections: Iterable[tuple[str, str]]) -> str:
    """Résumé de repli quand le modèle n'a pas répondu : début de la sortie de chaque agent."""
    lines = []
    for agent_name, raw in sections:
        first = _first_meaningful_line(raw)
        if first:
            lines.append(f"- **{agent_name}** : {first}")
    if not lines:
        return ""
    return "\n".join(lines) + "\n\n_Résumé automatique : la synthèse rédigée n'a pas pu être générée._"
