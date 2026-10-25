"""Garde-fous du CONTENU livré, avant tout commit : un secret n'est jamais committé, et un script manifestement malveillant non plus.

Complète `github_guards` (chemins sensibles : .github/, .env*, Dockerfile... ; syntaxe invalide ou fichier tronqué). Le texte d'un
dépôt cible est une donnée non fiable : une consigne glissée dans un README peut pousser un agent à écrire `curl ... | sh` dans un
script d'installation, ou à recopier une clé vue pendant sa lecture.

Les scripts dangereux ne sont cherchés que dans les fichiers EXÉCUTABLES (shell, PowerShell, batch, Makefile) et les scripts de
cycle de vie de `package.json` (fichier entier ou fragment) : jamais dans un README ni dans le code applicatif, où `curl ... | sh` est une documentation légitime.

Les motifs sont volontairement étroits (un refus est définitif pour le fichier) : ce garde-fou est un filet, pas une garantie ;
une écriture déguisée (`c""url | sh`, contenu encodé) n'est pas détectée. Un message de refus ne contient jamais la valeur d'un secret."""
import json
import re
from pathlib import PurePosixPath
from typing import Callable

from input_guards import REDACTED, find_secret_kinds
from logs import get_logger

log = get_logger("delivery_guards")

_SCRIPT_SUFFIXES = frozenset({".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".mk"})
_SCRIPT_NAMES = frozenset({"makefile", "gnumakefile", "justfile"})
_LIFECYCLE_SCRIPTS = frozenset({
    "preinstall", "install", "postinstall", "prepare", "prepublish", "prepublishonly", "prepack", "postpack", "preuninstall", "postuninstall",
})

_SHELL_TARGET = r"(?:ba|z|da|k)?sh"
_SHELL_AT_START = re.compile(rf"\s*(?:sudo\s+)?{_SHELL_TARGET}\b", re.IGNORECASE)


def _regex(pattern: str) -> Callable[[str], bool]:
    compiled = re.compile(pattern, re.IGNORECASE)
    return lambda text: compiled.search(text) is not None


def _piped_into_shell(source: str) -> Callable[[str], bool]:
    """Vrai si, sur une ligne, un segment contenant `source` est suivi (par « | ») d'un segment qui commence par un shell. Temps LINÉAIRE
    (chaque segment est examiné deux fois au plus) : l'expression unique `source[^|]*\\|\\s*sh` était quadratique sur une longue ligne, et
    la borner aurait permis de contourner le contrôle en allongeant la commande."""
    compiled = re.compile(source, re.IGNORECASE)

    def check(text: str) -> bool:
        for line in text.split("\n"):
            segments = line.split("|")
            if any(compiled.search(before) and _SHELL_AT_START.match(after) for before, after in zip(segments, segments[1:])):
                return True
        return False
    return check


def _then_on_same_line(first: str, then: str) -> Callable[[str], bool]:
    """Vrai si, sur une ligne, `then` apparaît APRÈS la première occurrence de `first` (une seule recherche par ligne, donc linéaire)."""
    first_re, then_re = re.compile(first, re.IGNORECASE), re.compile(then, re.IGNORECASE)

    def check(text: str) -> bool:
        for line in text.split("\n"):
            start = first_re.search(line)
            if start and then_re.search(line, start.end()):
                return True
        return False
    return check


_MKFS = _regex(r"\bmkfs(?:\.\w+)?\b")
_DD_TO_DISK = _then_on_same_line(r"\bdd\b", r"\bof=/dev/(?:sd|nvme|hd|vd|disk)")
_DANGEROUS: tuple[tuple[str, Callable[[str], bool]], ...] = (
    ("téléchargement exécuté directement (curl/wget | sh)", _piped_into_shell(r"\b(?:curl|wget)\b")),
    ("contenu décodé exécuté directement (base64 | sh)", _piped_into_shell(r"\bbase64\b")),
    ("PowerShell qui télécharge et exécute", _then_on_same_line(
        r"\b(?:iex|invoke-expression)\b", r"\b(?:downloadstring|invoke-webrequest|iwr|invoke-restmethod)\b")),
    ("suppression récursive de la racine ou du dossier personnel", _regex(
        r"\brm\s+(?:-[a-z]+\s+){0,10}-[a-z]*r[a-z]*\s+(?:-[a-z-]+\s+){0,10}(?:/|/\*|~|~/|\$HOME|\$\{HOME\}|\$HOME/)(?=\s|$|;|&)")),
    ("bombe fourche", _regex(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")),
    ("shell inversé (/dev/tcp)", _regex(r"/dev/tcp/")),
    ("shell inversé (nc -e)", _then_on_same_line(r"\b(?:nc|ncat|netcat)\b", r"\s(?:-[a-z]*e\b|--exec\b)")),
    ("permissions 777 sur la racine", _regex(r"\bchmod\s+(?:-[a-z]+\s+){0,10}777\s+/(?=\s|$)")),
    ("formatage ou écrasement d'un disque", lambda text: _MKFS(text) or _DD_TO_DISK(text)),
)


def _is_script(path: str) -> bool:
    name = PurePosixPath(str(path).replace("\\", "/")).name.lower()
    return name in _SCRIPT_NAMES or PurePosixPath(name).suffix in _SCRIPT_SUFFIXES


def _is_package_json(path: str) -> bool:
    return PurePosixPath(str(path).replace("\\", "/")).name.lower() == "package.json"


def _dangerous_in(text: str) -> str | None:
    for name, check in _DANGEROUS:
        if check(text):
            return name
    return None


_LIFECYCLE_ENTRY = re.compile(
    r'"(?P<key>[A-Za-z]+)"\s*:\s*"(?P<value>(?:[^"\\\n]|\\.)*)"'
)


def _lifecycle_scripts(content: str) -> str:
    """Valeurs des scripts de cycle de vie trouvées dans `content`, un package.json ENTIER ou un simple FRAGMENT (le texte ajouté
    par github_edit_file n'est presque jamais du JSON valide : le chercher par motif, pas par analyse du JSON)."""
    values = []
    for match in _LIFECYCLE_ENTRY.finditer(content):
        if match["key"].lower() in _LIFECYCLE_SCRIPTS:
            try:
                values.append(json.loads(f'"{match["value"]}"'))
            except ValueError:
                values.append(match["value"])
    return "\n".join(values)


def reject_unsafe_content(path: str, content: str) -> str | None:
    """None si `content` peut être livré dans `path`, sinon le message d'erreur à renvoyer tel quel à l'agent (aucun appel réseau)."""
    if REDACTED in content:
        log.warning(f"livraison refusée pour {path!r} : marqueur de secret masqué recopié")
        return (
            f"ERREUR : écriture refusée sur '{path}' : le contenu contient le marqueur « {REDACTED} », qui remplace une valeur que tu n'as pas "
            "pu lire (jamais du vrai code). Ne le recopie pas : ne réécris pas ce fichier en entier ; modifie-le avec github_edit_file sur un "
            "extrait SANS marqueur, ou signale-le comme non livré dans ton rapport final, avec cette raison."
        )
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
