"""Ouverture ou mise à jour de la Pull Request d'une exécution."""

import github_client
import github_guards
from delivery import merge_pull_request_body
from github import GithubException
from logs import get_logger

log = get_logger("github")

def _github_422_detail(e: GithubException) -> str:
    """Message(s) réellement renvoyés par GitHub pour une erreur 422 (et non un texte supposé)."""
    data = e.data if isinstance(e.data, dict) else {}
    parts = [str(data.get("message") or "")]
    parts += [str(item.get("message")) for item in data.get("errors", []) if isinstance(item, dict) and item.get("message")]
    return " ; ".join(p for p in parts if p) or "motif non précisé par GitHub"

def open_or_update_pull_request(
    owner: str, repo: str, branch: str, base_branch: str, title: str, body: str, draft: bool = False
) -> tuple[str | None, str]:
    """(URL, message). Sur une Pull Request OUVERTE existante pour la branche, seule la zone générée
    de sa description est mise à jour (titre et texte ajouté à la main conservés) ; sinon une PR est
    créée, en brouillon si `draft` — avec repli sur une PR normale quand le dépôt n'accepte pas les
    brouillons (dépôts privés des offres gratuites). Évite le doublon d'un 2e tour sur la même branche."""
    if github_guards.writes_cancelled():
        log.warning("ouverture de Pull Request refusée : l'exécution a été arrêtée (durée maximale dépassée).")
        return None, github_guards.STOPPED_EXECUTION_MESSAGE
    try:
        gh_repo = github_client._get_repo(owner, repo)
        existing = next(iter(gh_repo.get_pulls(state="open", head=f"{owner}:{branch}", base=base_branch)), None)
        if existing is not None:
            existing.edit(body=merge_pull_request_body(getattr(existing, "body", None), body))
            return existing.html_url, f"OK : Pull Request déjà ouverte, sa description générée a été mise à jour : {existing.html_url}"
        full_body = merge_pull_request_body(None, body)
        label = "(brouillon) " if draft else ""
        try:
            pr = gh_repo.create_pull(title=title, body=full_body, head=branch, base=base_branch, draft=draft)
        except GithubException as e:
            if e.status != 422 or not draft:
                raise
            pr = gh_repo.create_pull(title=title, body=full_body, head=branch, base=base_branch, draft=False)
            label = "(normale : brouillons non pris en charge par ce dépôt) "
        return pr.html_url, f"OK : Pull Request {label}créée avec succès : {pr.html_url}"
    except GithubException as e:
        if e.status == 422:
            return None, f"INFO : aucune Pull Request créée : GitHub a refusé (422) : {_github_422_detail(e)}"
        return None, github_client._github_error(e)
    except Exception as e:
        return None, f"ERREUR : {str(e)}"
