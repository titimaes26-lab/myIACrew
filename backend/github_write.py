"""Écriture GitHub : branche de travail, fichier unique, lot de fichiers, modification ciblée par blocs."""
import json

import github_batch
import github_client
import github_edit_failures
import github_guards
from crewai.tools import tool
from github import GithubException
from logs import get_logger

log = get_logger("github")

@tool("github_create_branch")
def github_create_branch(owner: str, repo: str, new_branch: str, base_branch: str = "main") -> str:
    """
    Crée une nouvelle branche à partir d'une branche de base, si elle n'existe pas déjà.
    Toujours appeler cet outil avant toute écriture avec github_write_file.
    Arguments:
        owner (str), repo (str), new_branch (str): nom de la branche de travail à créer.
        base_branch (str): branche source (défaut 'main').
    """
    rejection = github_guards._reject_protected_branch(new_branch)
    if rejection:
        return rejection
    try:
        gh_repo = github_client._get_repo(owner, repo)
        try:
            gh_repo.get_branch(new_branch)
            return f"INFO : la branche '{new_branch}' existe déjà, elle peut être utilisée directement."
        except GithubException:
            pass
        base_ref = gh_repo.get_git_ref(f"heads/{base_branch}")
        gh_repo.create_git_ref(ref=f"refs/heads/{new_branch}", sha=base_ref.object.sha)
        github_client.invalidate_read_cache(owner, repo, new_branch)
        return f"OK : branche '{new_branch}' créée à partir de '{base_branch}'."
    except GithubException as e:
        return github_client._github_error(e)
    except Exception as e:
        return f"ERREUR : {str(e)}"

@tool("github_write_file")
def github_write_file(owner: str, repo: str, path: str, content: str, branch: str, commit_message: str) -> str:
    """
    Crée ou met à jour un fichier sur une branche GitHub donnée.
    L'écriture directe sur 'main'/'master' est refusée : crée d'abord une branche avec github_create_branch.
    Arguments:
        owner (str), repo (str), path (str): chemin du fichier à écrire.
        content (str): contenu complet du fichier.
        branch (str): branche de travail cible (jamais main/master).
        commit_message (str): message de commit.
    """
    rejection = github_guards._reject_protected_branch(branch) or github_guards._reject_sensitive_path(path)
    if rejection:
        return rejection
    syntax_issue = github_guards._reject_invalid_syntax(path, content)
    if syntax_issue:
        return (
            f"ERREUR : écriture refusée, vérification syntaxique de '{path}' échouée AVANT tout "
            f"commit (rien n'a été modifié sur GitHub) : {syntax_issue} Ne retente PAS ce même "
            "appel avec un contenu identique (tu n'as aucun moyen de le corriger) : signale ce "
            "fichier comme non livré dans ton rapport final, avec cette raison."
        )
    try:
        gh_repo = github_client._get_repo(owner, repo)
        try:
            existing = gh_repo.get_contents(path, ref=branch)
        except GithubException as e:
            if e.status == 404:
                gh_repo.create_file(path, commit_message, content, branch=branch)
                github_edit_failures._record_edit_success(owner, repo, path, branch)
                return f"OK : fichier '{path}' créé sur la branche '{branch}'."
            raise

        if isinstance(existing, list):
            return f"ERREUR : '{path}' est un dossier, pas un fichier."

        gh_repo.update_file(path, commit_message, content, existing.sha, branch=branch)
        # Un github_write_file réussi rétablit un contenu correct pour ce fichier : s'il
        # faisait suite à des échecs de github_edit.github_edit_file (voir github_edit_failures._record_edit_failure), ces
        # échecs ne concernent plus l'état actuel du fichier et ne doivent pas continuer à
        # être comptés comme "consécutifs" si le développeur retente une édition ciblée
        # plus tard dans la même exécution.
        github_edit_failures._record_edit_success(owner, repo, path, branch)
        return f"OK : fichier '{path}' mis à jour sur la branche '{branch}'."
    except GithubException as e:
        return github_client._github_error(e)
    except Exception as e:
        return f"ERREUR : {str(e)}"

@tool("github_write_files")
def github_write_files(owner: str, repo: str, branch: str, commit_message: str, files_json: str) -> str:
    """
    Crée ou met à jour PLUSIEURS fichiers EN UN SEUL COMMIT, avec un seul appel d'outil quel que
    soit leur nombre. À PRÉFÉRER À github_write_file dès que tu dois créer/écrire plus de 2-3
    fichiers (ex: un nouveau projet, une fonctionnalité qui touche de nombreux composants) :
    chaque appel d'outil consomme une part de ton budget total (max_iter) pour cette tâche, et un
    projet de plusieurs dizaines de fichiers épuiserait ce budget bien avant la fin si chaque
    fichier coûtait son propre appel — tu produirais alors une réponse qui DÉCRIT le code sans
    l'avoir réellement poussé sur GitHub.
    L'écriture directe sur 'main'/'master' est refusée : crée d'abord une branche avec github_create_branch.
    Arguments:
        owner (str), repo (str): repository cible.
        branch (str): branche de travail cible (jamais main/master).
        commit_message (str): message de commit unique pour tous les fichiers de cet appel.
        files_json (str): liste JSON des fichiers à écrire (nouveaux ou existants), chacun avec
            "path" (chemin dans le repo) et "content" (contenu complet du fichier). Exemple :
            '[{"path": "src/App.tsx", "content": "..."}, {"path": "package.json", "content": "..."}]'
    """
    # Vérifiée AVANT le JSON : sur 'main', l'agent doit apprendre que la branche est interdite,
    # pas qu'il faut corriger son JSON (il retenterait alors sur la même branche).
    rejection = github_guards._reject_protected_branch(branch)
    if rejection:
        return rejection
    try:
        files = json.loads(files_json)
    except json.JSONDecodeError as e:
        return (
            f"ERREUR : files_json n'est pas un JSON valide ({e}). Fournis une liste JSON "
            'de {"path": ..., "content": ...}, ex: [{"path": "a.txt", "content": "..."}]. '
            "Si cette erreur se reproduit (contenu difficile à échapper correctement en JSON), "
            "n'insiste pas : bascule sur des appels séparés à github_write_file, un par fichier."
        )
    return github_batch.write_files_to_branch(owner, repo, branch, commit_message, files)


