"""Aperçu du dépôt cible et SHA de tête d'une branche."""

import github_client
import github_read
from github import GithubException
from logs import get_logger
from project_summary import summarize_project

log = get_logger("github")

# Aperçu du repo donné à l'Architecte (voir main._execute_crew_and_persist) : ce qu'il lisait par 4 appels d'outils
# (racine, package.json, tsconfig.json, src), lu ici en Python, sans tour de LLM.
_SNAPSHOT_FILE_LIMITS = (("package.json", 6000), ("tsconfig.json", 3000))

# Le prompt est renvoyé à chaque tour de la boucle de l'agent : un dossier très peuplé ne doit pas le gonfler.
_SNAPSHOT_MAX_LISTING_LINES = 150

def _capped_listing(listing: str) -> str:
    lines = listing.splitlines()
    if len(lines) <= _SNAPSHOT_MAX_LISTING_LINES:
        return listing
    hidden = len(lines) - _SNAPSHOT_MAX_LISTING_LINES
    return "\n".join(lines[:_SNAPSHOT_MAX_LISTING_LINES] + [f"… {hidden} autres entrées"])

def build_repo_snapshot(owner: str, repo: str, branch: str) -> str:
    """Texte « ## Racine / ## Résumé du projet / ## src » de la branche lue (JSON brut en repli), ou "" si la racine est
    illisible (l'agent retombe alors sur ses outils). Une lecture secondaire qui échoue devient une ligne « non lu »."""
    root = github_read.list_directory_cached(owner, repo, "", branch)
    if root.startswith("INFO"):
        # Dossier vide : le dire (l'Architecte n'a alors rien à relire) plutôt que de le laisser relire la racine.
        return f"Aperçu du repository {owner}/{repo} (branche lue : {branch}).\n\n## Racine\ndépôt vide"
    if root.startswith("ERREUR"):
        return ""
    root_names = {line.split(": ", 1)[-1].strip() for line in root.splitlines()}
    sections = [f"Aperçu du repository {owner}/{repo} (branche lue : {branch}).", f"## Racine\n{_capped_listing(root)}"]
    # package.json + tsconfig.json : un résumé déterministe (dépendances, conventions, mode strict) bien plus court
    # que le JSON brut ; le brut tronqué ne sert que de repli quand package.json n'est pas un JSON exploitable.
    raw_files: dict[str, str] = {}
    for name, _limit in _SNAPSHOT_FILE_LIMITS:
        if name in root_names:
            raw_files[name] = github_read.read_file_cached(owner, repo, name, branch)
    package = raw_files.get("package.json")
    tsconfig = raw_files.get("tsconfig.json")
    summary = summarize_project(package, tsconfig if tsconfig and not tsconfig.startswith("ERREUR") else None) \
        if package and not package.startswith("ERREUR") else None
    if summary:
        sections.append(f"## Résumé du projet\n{summary}")
    else:
        for name, limit in _SNAPSHOT_FILE_LIMITS:
            content = raw_files.get(name)
            if content is None:
                continue
            if content.startswith("ERREUR"):
                sections.append(f"## {name}\nnon lu : {content}")
            else:
                cut = "\n[… tronqué]" if len(content) > limit else ""
                sections.append(f"## {name}\n{content[:limit]}{cut}")
    if "src" in root_names:
        listing = github_read.list_directory_cached(owner, repo, "src", branch)
        sections.append(f"## src\n{'non lu : ' + listing if listing.startswith('ERREUR') else _capped_listing(listing)}")
    return "\n\n".join(sections)

class GitHubVerificationUnavailable(Exception):
    """Levée par get_branch_head_sha quand l'API GitHub n'a PAS pu confirmer si {branch} existe
    ou non (erreur réseau, rate-limit, credentials temporairement invalides...) — distinct d'une
    branche confirmée absente (404, cas normal au tout premier tour sur un work_branch neuf).
    Son appelant (execution_context.capture_branch_sha) traite spécifiquement ce cas comme "repère indisponible" (best-effort,
    n'empêche pas le lancement du crew) plutôt que de le confondre avec "branche inexistante"."""

def get_branch_head_sha(owner: str, repo: str, branch: str) -> str | None:
    """SHA du commit HEAD de {branch}, ou None si la branche n'existe PAS ENCORE (confirmé, 404).

    Lève GitHubVerificationUnavailable (jamais silencieusement None) sur toute autre erreur : un
    appelant qui confondrait "l'API n'a pas répondu" avec "la branche n'existe pas" désactiverait
    par erreur la comparaison de SHA de verify_github_delivery (voir plus bas) exactement quand
    elle est le plus utile — un work_branch réutilisé qui a déjà des commits/une PR d'un tour
    précédent.

    Appelée par execution_context.capture_branch_sha AVANT de lancer le crew, pour donner à verify_github_delivery un point de
    référence : sans lui, un work_branch réutilisé d'un tour précédent de la même conversation
    (voir execution_context.previous_work_branch, réutilisation de work_branch entre tours) ferait passer la vérification pour
    "confirmée" même si CE tour n'a lui-même rien poussé, simplement parce qu'une branche et une
    Pull Request d'un tour PRÉCÉDENT existent toujours.
    """
    try:
        gh_repo = github_client._get_repo(owner, repo)
        return gh_repo.get_branch(branch).commit.sha
    except GithubException as e:
        if e.status == 404:
            return None
        raise GitHubVerificationUnavailable(github_client._github_error(e)) from e
    except Exception as e:
        raise GitHubVerificationUnavailable(str(e)) from e
