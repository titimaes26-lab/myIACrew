"""Résumé déterministe d'un projet React/Vite, déduit de `package.json` et `tsconfig.json` sans appel LLM.

Donné aux agents à la place du JSON brut : même information utile (dépendances installées, conventions) en bien
moins de tokens, et identique pour l'Architecte et le Diagnostic (plus de redécouverte par chacun)."""
import json
import re
from typing import Optional

MAX_LISTED_DEPENDENCIES = 60

# Famille de conventions -> {paquet: nom affiché}. Seuls les paquets réellement présents sont cités.
_CONVENTIONS: dict[str, dict[str, str]] = {
    "état": {
        "@reduxjs/toolkit": "Redux Toolkit", "redux": "Redux", "zustand": "Zustand", "jotai": "Jotai",
        "recoil": "Recoil", "mobx": "MobX", "@tanstack/react-query": "TanStack Query", "swr": "SWR",
    },
    "routage": {"react-router-dom": "React Router", "react-router": "React Router", "@tanstack/react-router": "TanStack Router"},
    "CSS": {
        "tailwindcss": "Tailwind", "styled-components": "styled-components", "@emotion/react": "Emotion",
        "sass": "Sass", "@mui/material": "MUI", "bootstrap": "Bootstrap", "antd": "Ant Design",
        "@chakra-ui/react": "Chakra UI",
    },
    "tests": {
        "vitest": "Vitest", "jest": "Jest", "@playwright/test": "Playwright", "cypress": "Cypress",
        "@testing-library/react": "Testing Library",
    },
    "qualité": {"eslint": "ESLint", "prettier": "Prettier"},
}


def _strip_jsonc(text: str) -> str:
    """Retire les commentaires (`//` et `/* */`, hors chaînes) et les virgules finales d'un JSON « avec commentaires »
    (tsconfig.json, y compris celui que génère `tsc --init`)."""
    out: list[str] = []
    i, length, in_string = 0, len(text), False
    while i < length:
        char = text[i]
        if in_string:
            out.append(char)
            if char == "\\" and i + 1 < length:
                out.append(text[i + 1])
                i += 1
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
            out.append(char)
        elif text.startswith("//", i):
            while i < length and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = length if end == -1 else end + 2
            continue
        else:
            out.append(char)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _load_json(text: str) -> Optional[dict]:
    """JSON, ou JSON avec commentaires et virgules finales (tsconfig.json) ; None si illisible."""
    for candidate in (text, _strip_jsonc(text)):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        return data if isinstance(data, dict) else None
    return None


def _listed(dependencies: dict) -> str:
    names = [f"{name}@{version}" for name, version in sorted(dependencies.items())]
    extra = len(names) - MAX_LISTED_DEPENDENCIES
    shown = ", ".join(names[:MAX_LISTED_DEPENDENCIES])
    return shown + (f" … (+{extra})" if extra > 0 else "")


def summarize_project(package_json: str, tsconfig: Optional[str] = None) -> Optional[str]:
    """Texte du résumé, ou None si package.json n'est pas un JSON exploitable (l'appelant garde alors le brut)."""
    package = _load_json(package_json)
    if package is None:
        return None
    dependencies = package.get("dependencies") if isinstance(package.get("dependencies"), dict) else {}
    dev_dependencies = package.get("devDependencies") if isinstance(package.get("devDependencies"), dict) else {}
    installed = {**dev_dependencies, **dependencies}
    lines = ["Résumé du projet (déduit de package.json et tsconfig.json) :"]
    if package.get("name"):
        lines.append(f"- Nom : {package['name']}")
    scripts = package.get("scripts")
    if isinstance(scripts, dict) and scripts:
        lines.append(f"- Scripts : {', '.join(sorted(scripts))}")
    lines.append(f"- Dépendances : {_listed(dependencies) or 'aucune'}")
    lines.append(f"- Dépendances de dev : {_listed(dev_dependencies) or 'aucune'}")
    detected = []
    for family, packages in _CONVENTIONS.items():
        found = sorted({label for name, label in packages.items() if name in installed})
        if found:
            detected.append(f"{family} : {', '.join(found)}")
    lines.append(f"- Conventions détectées : {' ; '.join(detected) if detected else 'aucune bibliothèque d’état, de routage, de CSS ni de test reconnue'}")
    if tsconfig:
        config = _load_json(tsconfig)
        options = config.get("compilerOptions") if isinstance(config, dict) and isinstance(config.get("compilerOptions"), dict) else None
        if config is None:
            lines.append("- TypeScript : tsconfig.json illisible")
        elif options is None and config.get("references"):
            lines.append("- TypeScript : tsconfig.json ne fait que référencer d’autres configurations (strict : voir tsconfig.app.json, non lu)")
        else:
            strict = (options or {}).get("strict")
            lines.append(f"- TypeScript : mode strict {'activé' if strict else 'non activé'} (tsconfig.json)")
    elif "typescript" in installed:
        lines.append("- TypeScript : installé, tsconfig.json non lu")
    return "\n".join(lines)
