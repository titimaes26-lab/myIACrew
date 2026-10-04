"""Espace de travail local (mode sans repository cible) : écriture, lecture et listing confinés à un dossier par conversation."""
import os
import re
from pathlib import Path

from analyst_output import FILE_ABSENT, PRESENT_UNREADABLE, normalize_path
from github_guards import _reject_invalid_syntax

# Dossier DÉDIÉ aux fichiers livrés en mode local (sans repository cible) : jamais le dossier
# de travail du serveur, où un fichier livré nommé "main.py" ou ".env" écraserait le backend en
# cours d'exécution. Chaque conversation a son propre sous-dossier (dérivé de son id, stable d'un
# tour à l'autre) : deux conversations ne s'écrasent jamais, et un tour de suivi ("corrige ça")
# relit bien ce que le tour précédent a livré.
# LIMITE CONNUE (pas un bug) : ce dossier est VIDE au premier tour d'une conversation. Un
# BUGFIX/FEATURE en mode local ne peut donc pas lire un code préexistant qui n'aurait pas déjà
# été livré par un tour précédent de CETTE MÊME conversation — le mode local convient surtout à
# un DESIGN_AND_DEV qui part de zéro. Corriger ça demanderait un dossier projet local en lecture
# seule, configurable par l'utilisateur (hors périmètre pour l'instant).
BACKEND_DIR = Path(__file__).resolve().parent

LOCAL_WORKSPACE_DIR = Path(os.getenv("LOCAL_WORKSPACE_DIR") or BACKEND_DIR / "workspace").resolve()

def _conversation_workspace(conversation_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", conversation_id or "").strip(".-")
    return LOCAL_WORKSPACE_DIR / (f"conversation-{safe}" if safe else "sans-conversation")

def _local_target(workspace: Path, path: str) -> Path | None:
    """Chemin absolu dans `workspace`, ou None s'il en sortirait (".."). Un préfixe "./" ou "/"
    (fréquent sous la plume d'un LLM) est normalisé plutôt que refusé."""
    normalized = normalize_path(path)
    if normalized is None:
        return None
    root = workspace.resolve()
    target = (root / normalized).resolve()
    return target if root in target.parents else None

def _write_files_locally(workspace: Path, files: list[dict], rejected_sink: dict[str, str]) -> str:
    """Pendant disque local de write_files_to_branch (mode sans repository cible), confiné à
    `workspace`. Les fichiers NON écrits sont ajoutés à rejected_sink ({chemin: raison})."""
    written = []
    for f in files:
        path, content = f["path"], f["content"]
        target = _local_target(workspace, path)
        if target is None:
            rejected_sink[path] = "chemin hors de l'espace de travail local refusé"
            continue
        syntax_issue = _reject_invalid_syntax(path, content)
        if syntax_issue:
            rejected_sink[path] = syntax_issue
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(path)
        except Exception as e:
            rejected_sink[path] = f"{type(e).__name__}: {e}"
    # Comme write_files_to_branch : aucun fichier écrit est une ERREUR, jamais un "OK : 0" que
    # le Développeur lirait comme un succès (règle "OK : passe à l'étape suivante").
    message = (
        f"OK : {len(written)} fichier(s) écrit(s) dans l'espace de travail local."
        if written or not files
        else f"ERREUR : aucun fichier écrit dans l'espace de travail local ({len(files)} rejeté(s))."
    )
    refused = {p: r for p, r in rejected_sink.items() if p in {f["path"] for f in files}}
    if refused:
        message += f"\nREJETÉS ({len(refused)}) — non écrits :\n" + "\n".join(
            f"- '{p}' : {reason}" for p, reason in refused.items()
        )
    return message

def _read_local_file(workspace: Path, path: str) -> tuple[str | None, str | None]:
    target = _local_target(workspace, path)
    if target is None:
        return None, "chemin hors de l'espace de travail local"
    try:
        return target.read_text(encoding="utf-8"), None
    except FileNotFoundError:
        return None, f"{FILE_ABSENT} : '{path}' n'existe pas dans l'espace de travail local"
    except IsADirectoryError:
        return None, f"{FILE_ABSENT} : '{path}' est un dossier, pas un fichier"
    except UnicodeDecodeError:
        return None, f"{PRESENT_UNREADABLE} : '{path}' existe mais n'est pas du texte UTF-8"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

def _list_local_dir(workspace: Path, directory: str) -> set[str] | None:
    """Noms des entrées d'un dossier de l'espace de travail. Dossier absent (ou fichier) : ensemble
    vide, pas « inconnu » ; None seulement hors de l'espace de travail ou en cas d'erreur d'accès."""
    target = _local_target(workspace, directory) if directory else workspace.resolve()
    if target is None:
        return None
    try:
        return {entry.name for entry in target.iterdir()}
    except (FileNotFoundError, NotADirectoryError):
        return set()
    except Exception:
        return None
