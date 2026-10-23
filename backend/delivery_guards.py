"""Garde-fous du CONTENU livré, avant tout commit : un secret n'est jamais committé, et un script manifestement malveillant non plus.

Complète `github_guards` (chemins sensibles : .github/, .env*, Dockerfile... ; syntaxe invalide ou fichier tronqué). Le texte d'un
dépôt cible est une donnée non fiable : une consigne glissée dans un README peut pousser un agent à écrire `curl ... | sh` dans un
script d'installation, ou à recopier une clé vue pendant sa lecture.

Les scripts dangereux ne sont cherchés que dans les fichiers EXÉCUTABLES (shell, PowerShell, batch, Makefile) et les scripts de
cycle de vie de `package.json` : jamais dans un README ni dans le code applicatif, où `curl ... | sh` est une documentation légitime.

Les motifs sont volontairement étroits (un refus est définitif pour le fichier) : ce garde-fou est un filet, pas une garantie ;
une écriture déguisée (`c""url | sh`, contenu encodé) n'est pas détectée. Un message de refus ne contient jamais la valeur d'un secret."""
import json
import re
from pathlib import PurePosixPath

from input_guards import find_secret_kinds
from logs import get_logger

log = get_logger("delivery_guards")

_SCRIPT_SUFFIXES = frozenset({".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".mk"})
_SCRIPT_NAMES = frozenset({"makefile", "gnumakefile", "justfile"})
_LIFECYCLE_SCRIPTS = frozenset({
    "preinstall", "install", "postinstall", "prepare", "prepublish", "prepublishonly", "prepack", "postpack", "preuninstall", "postuninstall",
})

_SHELL_TARGET = r"(?:ba|z|da|k)?sh"
_DANGEROUS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        ("téléchargement exécuté directement (curl/wget | sh)", rf"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?{_SHELL_TARGET}\b"),
        ("contenu décodé exécuté directement (base64 | sh)", rf"\bbase64\b[^\n|]*\|\s*(?:sudo\s+)?{_SHELL_TARGET}\b"),
        ("PowerShell qui télécharge et exécute", r"\b(?:iex|invoke-expression)\b[^\n]*\b(?:downloadstring|invoke-webrequest|iwr|invoke-restmethod)\b"),
        ("suppression récursive de la racine ou du dossier personnel", r"\brm\s+(?:-[a-z]+\s+)*-[a-z]*r[a-z]*\s+(?:-[a-z-]+\s+)*(?:/|/\*|~|~/|\$HOME|\$\{HOME\}|\$HOME/)(?=\s|$|;|&)"),
        ("bombe fourche", r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
        ("shell inversé (/dev/tcp)", r"/dev/tcp/"),
        ("shell inversé (nc -e)", r"\b(?:nc|ncat|netcat)\b[^\n]*\s(?:-[a-z]*e\b|--exec\b)"),
        ("permissions 777 sur la racine", r"\bchmod\s+(?:-[a-z]+\s+)*777\s+/(?=\s|$)"),
        ("formatage ou écrasement d'un disque", r"\bmkfs(?:\.\w+)?\b|\bdd\b[^\n]*\bof=/dev/(?:sd|nvme|hd|vd|disk)"),
    )
)


def _is_script(path: str) -> bool:
    name = PurePosixPath(str(path).replace("\\", "/")).name.lower()
    return name in _SCRIPT_NAMES or PurePosixPath(name).suffix in _SCRIPT_SUFFIXES


def _is_package_json(path: str) -> bool:
    return PurePosixPath(str(path).replace("\\", "/")).name.lower() == "package.json"


def _dangerous_in(text: str) -> str | None:
    for name, pattern in _DANGEROUS:
        if pattern.search(text):
            return name
    return None


def _lifecycle_scripts(content: str) -> str:
    """Texte des scripts de cycle de vie d'un package.json (vide si JSON illisible ou sans scripts)."""
    try:
        scripts = json.loads(content).get("scripts", {})
        return "\n".join(str(value) for key, value in scripts.items() if key.lower() in _LIFECYCLE_SCRIPTS)
    except (ValueError, AttributeError, TypeError):
        return ""


def reject_unsafe_content(path: str, content: str) -> str | None:
    """None si `content` peut être livré dans `path`, sinon le message d'erreur à renvoyer tel quel à l'agent (aucun appel réseau)."""
    secrets = find_secret_kinds(content)
    if secrets:
        log.warning(f"livraison refusée pour {path!r} : secret(s) détecté(s) ({', '.join(secrets)}), valeur non journalisée")
        return (
            f"ERREUR : écriture refusée sur '{path}' : le contenu contient un secret ({', '.join(secrets)}). Un secret n'est jamais "
            "committé : lis-le dans une variable d'environnement et documente-la dans un .env.example à valeurs factices. Ne retente "
            "pas avec le même contenu : signale ce fichier comme non livré dans ton rapport final, avec cette raison."
        )
    scanned = content if _is_script(path) else _lifecycle_scripts(content) if _is_package_json(path) else ""
    danger = _dangerous_in(scanned) if scanned else None
    if danger:
        log.warning(f"livraison refusée pour {path!r} : script dangereux ({danger})")
        return (
            f"ERREUR : écriture refusée sur '{path}' : script dangereux détecté ({danger}). Ne retente pas avec le même contenu : "
            "signale ce fichier comme non livré dans ton rapport final, avec cette raison."
        )
    return None
