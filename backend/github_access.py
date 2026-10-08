"""Contrôle préalable de l'accès au repository cible."""
import os

import github_client
from github import GithubException
from logs import get_logger

log = get_logger("github")

class GitHubAccessProblem(Exception):
    """Refus du contrôle préalable (voir check_github_access). `kind` : not_found | forbidden |
    invalid_token | missing_token | rate_limited | unavailable ; `message` : à montrer tel quel (dit quoi corriger)."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message

def check_github_access(owner: str, repo: str, base_branch: str) -> None:
    """Contrôle préalable, en lecture seule, AVANT de lancer un crew qui écrira sur GitHub : jeton
    présent, repository lisible, droit d'écriture, branche de base existante. Lève
    GitHubAccessProblem au premier problème : une faute découverte ici ne coûte aucun appel LLM,
    alors que la même découverte en fin d'exécution en coûte plusieurs minutes de quota."""
    if not os.getenv("GITHUB_TOKEN"):
        raise GitHubAccessProblem("missing_token", "GITHUB_TOKEN manque côté serveur : configurez-le puis relancez.")
    target = f"{owner}/{repo}"
    try:
        gh_repo = github_client._get_repo(owner, repo)
    except GithubException as e:
        if e.status == 404:
            raise GitHubAccessProblem(
                "not_found",
                f"Le repository {target} est introuvable ou inaccessible avec le jeton du serveur : "
                "vérifiez le propriétaire, le nom et les droits du GITHUB_TOKEN.",
            ) from e
        if e.status == 401:
            raise GitHubAccessProblem("invalid_token", "Le GITHUB_TOKEN du serveur est invalide ou expiré : renouvelez-le.") from e
        if e.status == 403 and "rate limit" in str(e).lower():
            # Limite de débit : passagère, donc réessayable (≠ droits insuffisants).
            raise GitHubAccessProblem("rate_limited", "La limite de débit de GitHub est atteinte : réessayez dans quelques minutes.") from e
        if e.status == 403:
            raise GitHubAccessProblem(
                "forbidden",
                f"Accès refusé à {target} (droits insuffisants ou limite de débit GitHub atteinte) : "
                "vérifiez les permissions du GITHUB_TOKEN ou réessayez plus tard.",
            ) from e
        raise GitHubAccessProblem("unavailable", f"GitHub est momentanément injoignable ({github_client._github_error(e)}).") from e
    except Exception as e:
        raise GitHubAccessProblem("unavailable", f"GitHub est momentanément injoignable ({e}).") from e

    permissions = getattr(gh_repo, "permissions", None)
    if permissions is not None and not getattr(permissions, "push", False):
        raise GitHubAccessProblem(
            "forbidden",
            f"Le GITHUB_TOKEN du serveur n'a pas le droit d'écrire sur {target} : "
            "ajoutez la permission « Contents : écriture » (et « Pull requests : écriture »).",
        )
    try:
        gh_repo.get_branch(base_branch)
    except GithubException as e:
        if e.status == 404:
            raise GitHubAccessProblem(
                "not_found", f"La branche de base « {base_branch} » n'existe pas sur {target}."
            ) from e
        raise GitHubAccessProblem("unavailable", f"GitHub est momentanément injoignable ({github_client._github_error(e)}).") from e
    except Exception as e:
        raise GitHubAccessProblem("unavailable", f"GitHub est momentanément injoignable ({e}).") from e
