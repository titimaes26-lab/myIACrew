"""Lecture GitHub : fichiers et dossiers (outils des agents, lecteurs de vérification de livraison)."""
import base64
from typing import Callable

import github_client
from analyst_output import FILE_ABSENT, PRESENT_UNREADABLE
from crewai.tools import tool
from github import GithubException
from logs import get_logger

log = get_logger("github")

def _decode_content_file(gh_repo, content_file, path: str) -> tuple[str | None, str | None]:
    """(contenu, erreur) pour un ContentFile déjà récupéré via get_contents : gère le repli sur
    le blob git pour les fichiers > 1 Mo (get_contents n'en renvoie alors pas le contenu) et un
    encodage non UTF-8. Factorée pour que `github_read_file` et `make_file_fetcher` partagent
    exactement le même comportement plutôt que de dupliquer cette logique."""
    try:
        raw = content_file.decoded_content
    except Exception:
        # Fichier > 1 Mo : get_contents ne renvoie pas son contenu, le blob git oui. Une erreur
        # de CET appel (réseau, quota) rend le fichier non vérifiable, pas illisible.
        try:
            raw = base64.b64decode(gh_repo.get_git_blob(content_file.sha).content)
        except GithubException as e:
            return None, github_client._github_error(e)
        except Exception as e:
            return None, f"ERREUR : {e}"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, f"{PRESENT_UNREADABLE} : '{path}' existe mais n'est pas du texte UTF-8"

def make_file_fetcher(owner: str, repo: str, branch: str) -> Callable[[str], tuple[str | None, str | None]]:
    """Fonction path -> (contenu, None) ou (None, raison), pour un usage Python interne (voir
    qa_verify_delivered_files, crew_tools.py), sans passer par l'objet Tool crewai.

    Le dépôt est résolu UNE fois pour toutes les lectures (un seul get_repo, au lieu d'un par
    fichier). Un fichier qui existe mais n'a pas pu être décodé (au-delà de 1 Mo l'API ne
    renvoie pas son contenu, ou contenu non UTF-8) est signalé par PRESENT_UNREADABLE, pour que
    la QA ne le déclare jamais ABSENT à tort.
    """
    def failing(error: str, branch_missing: bool = False) -> Callable[[str], tuple[str | None, str | None]]:
        def fetch(path: str) -> tuple[str | None, str | None]:
            return None, error
        # Permet à l'appelant de distinguer « la branche n'existe pas (encore) » d'une erreur
        # transitoire : seul le premier cas autorise de lire une autre branche à la place.
        fetch.branch_missing = branch_missing  # type: ignore[attr-defined]
        return fetch

    try:
        gh_repo = github_client._get_repo(owner, repo)
    except GithubException as e:
        error = (
            f"ERREUR : le repository {owner}/{repo} est introuvable ou inaccessible avec ce jeton"
            if e.status == 404 else github_client._github_error(e)
        )
        return failing(error)
    except Exception as e:
        return failing(f"ERREUR : {e}")
    try:
        # Branche vérifiée d'abord : sans elle, chaque lecture renverrait 404 et TOUS les fichiers
        # passeraient pour absents, alors que c'est la branche qui manque (non vérifiable).
        gh_repo.get_branch(branch)
    except GithubException as e:
        if e.status == 404:
            return failing(f"ERREUR : la branche '{branch}' est introuvable sur {owner}/{repo}", branch_missing=True)
        return failing(github_client._github_error(e))
    except Exception as e:
        return failing(f"ERREUR : {e}")

    def fetch(path: str) -> tuple[str | None, str | None]:
        try:
            content_file = gh_repo.get_contents(path, ref=branch)
        except GithubException as e:
            if e.status == 404:
                return None, f"{FILE_ABSENT} : '{path}' n'existe pas sur la branche '{branch}'"
            return None, github_client._github_error(e)
        except Exception as e:
            return None, f"ERREUR : {e}"
        if isinstance(content_file, list):
            return None, f"{FILE_ABSENT} : '{path}' est un dossier, pas un fichier"
        return _decode_content_file(gh_repo, content_file, path)

    return fetch

def make_dir_lister(owner: str, repo: str, branch: str) -> Callable[[str], set[str] | None]:
    """Fonction dossier -> noms des entrées. Un dossier ABSENT (404) ou un fichier donne un
    ensemble vide (il n'y a rien dedans : un import qui y mène n'a pas de cible) ; None signifie
    seulement « inconnu » (dépôt inaccessible, erreur réseau ou de quota). Un seul get_repo est
    fait pour toutes les listes."""
    try:
        gh_repo = github_client._get_repo(owner, repo)
    except Exception:
        return lambda directory: None

    def list_dir(directory: str) -> set[str] | None:
        try:
            contents = gh_repo.get_contents(directory or "", ref=branch)
        except GithubException as e:
            return set() if e.status == 404 else None
        except Exception:
            return None
        if not isinstance(contents, list):
            return set()
        return {item.name for item in contents}

    return list_dir

@tool("github_read_file")
def github_read_file(owner: str, repo: str, path: str, branch: str = "main") -> str:
    """
    Lit le contenu d'un fichier depuis un repository GitHub.
    Arguments:
        owner (str): propriétaire/organisation du repo (ex: 'titimaes26-lab').
        repo (str): nom du repository (ex: 'myIACrew').
        path (str): chemin du fichier dans le repo (ex: 'src/App.tsx').
        branch (str): branche à lire (défaut 'main').
    """
    return read_file_cached(owner, repo, path, branch)

def read_file_cached(owner: str, repo: str, path: str, branch: str) -> str:
    return github_client._cached_read("files", (owner, repo, branch, path), lambda: _read_file_uncached(owner, repo, path, branch))

def _read_file_uncached(owner: str, repo: str, path: str, branch: str) -> str:
    try:
        gh_repo = github_client._get_repo(owner, repo)
        content_file = gh_repo.get_contents(path, ref=branch)
        if isinstance(content_file, list):
            return f"ERREUR : '{path}' est un dossier, pas un fichier. Utilise github_list_directory."
        # _decode_content_file : gère aussi le repli sur le blob git pour les fichiers > 1 Mo,
        # que decoded_content seul ne renvoie pas (voir make_file_fetcher, qui partage ce code).
        content, error = _decode_content_file(gh_repo, content_file, path)
        if content is None:
            # `error` est déjà formaté ("ERREUR_GITHUB : ...", "ERREUR : ...") sauf pour
            # PRESENT_UNREADABLE, qui n'a pas ce préfixe : ne jamais l'imbriquer deux fois.
            message = error or "contenu illisible"  # `error` est toujours renseigné quand `content` est None
            return message if message.startswith("ERREUR") else f"ERREUR : {message}"
        return content
    except GithubException as e:
        if e.status == 404:
            return (
                f"ERREUR_FICHIER_INEXISTANT : '{path}' n'existe pas sur la branche '{branch}' de {owner}/{repo}. "
                "Inutile de réessayer la lecture de ce fichier exact."
            )
        return github_client._github_error(e)
    except Exception as e:
        return f"ERREUR : {str(e)}"

@tool("github_list_directory")
def github_list_directory(owner: str, repo: str, path: str = "", branch: str = "main") -> str:
    """
    Liste les fichiers et sous-dossiers d'un répertoire d'un repository GitHub.
    Arguments:
        owner (str): propriétaire/organisation du repo.
        repo (str): nom du repository.
        path (str): chemin du dossier (défaut la racine).
        branch (str): branche à inspecter (défaut 'main').
    """
    return list_directory_cached(owner, repo, path, branch)

def list_directory_cached(owner: str, repo: str, path: str, branch: str) -> str:
    return github_client._cached_read("dirs", (owner, repo, branch, path or ""), lambda: _list_directory_uncached(owner, repo, path, branch))

def _list_directory_uncached(owner: str, repo: str, path: str, branch: str) -> str:
    try:
        gh_repo = github_client._get_repo(owner, repo)
        contents = gh_repo.get_contents(path or "", ref=branch)
        if not isinstance(contents, list):
            return f"'{path}' est un fichier, pas un dossier. Utilise github_read_file."
        return "\n".join(f"{item.type}: {item.path}" for item in contents) or "INFO : dossier vide."
    except GithubException as e:
        if e.status == 404:
            return f"ERREUR_DOSSIER_INEXISTANT : '{path}' n'existe pas sur la branche '{branch}' de {owner}/{repo}."
        return github_client._github_error(e)
    except Exception as e:
        return f"ERREUR : {str(e)}"
