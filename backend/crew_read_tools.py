"""Outils de lecture GitHub d'une exécution, ÉPINGLÉS sur son repository cible."""
from crewai.tools import tool

import github_read


def pinned_github_readers(crew):
    """(github_read_file, github_list_directory) de CETTE exécution : owner/repo sont choisis par le modèle, et un modèle léger
    lit alors un autre dépôt accessible avec le jeton (le studio lui-même) puis livre son code (exécution 137). Un autre
    repository est refusé avec le bon nom ; sans repository cible, les outils d'origine servent tels quels."""
    def refusal(owner: str, repo: str) -> str | None:
        target = getattr(crew, "_repo_target", None)
        if target is None or (owner.strip().lower(), repo.strip().lower()) == (target[0].lower(), target[1].lower()):
            return None
        return (f"ERREUR : lecture refusée sur {owner}/{repo}. Le repository de cette exécution est {target[0]}/{target[1]} : "
                f"relis ce fichier avec owner='{target[0]}' et repo='{target[1]}'.")

    @tool("github_read_file")
    def read_file(owner: str, repo: str, path: str, branch: str = "main") -> str:
        """
        Lit le contenu d'un fichier depuis un repository GitHub.
        Arguments:
            owner (str): propriétaire/organisation du repo (ex: 'titimaes26-lab').
            repo (str): nom du repository (ex: 'myIACrew').
            path (str): chemin du fichier dans le repo (ex: 'src/App.tsx').
            branch (str): branche à lire (défaut 'main').
        """
        return refusal(owner, repo) or github_read.read_file_text(owner, repo, path, branch)

    @tool("github_list_directory")
    def list_directory(owner: str, repo: str, path: str = "", branch: str = "main") -> str:
        """
        Liste les fichiers et sous-dossiers d'un répertoire d'un repository GitHub.
        Arguments:
            owner (str): propriétaire/organisation du repo.
            repo (str): nom du repository.
            path (str): chemin du dossier (défaut la racine).
            branch (str): branche à inspecter (défaut 'main').
        """
        return refusal(owner, repo) or github_read.list_directory_cached(owner, repo, path, branch)

    return read_file, list_directory
