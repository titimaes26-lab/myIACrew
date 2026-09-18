import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar

from crewai.tools import tool
from github import Auth, Github, GithubException


def _get_repo(owner: str, repo: str):
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN manquant dans les variables d'environnement du backend.")
    client = Github(auth=Auth.Token(token))
    return client.get_repo(f"{owner}/{repo}")


def _github_error(e: GithubException) -> str:
    message = e.data.get("message", str(e)) if isinstance(e.data, dict) else str(e)
    return f"ERREUR_GITHUB : {message}"


def _reject_protected_branch(branch: str) -> str | None:
    """None si l'écriture peut continuer, sinon le message d'erreur à renvoyer tel quel."""
    if branch in ("main", "master"):
        return "ERREUR : écriture directe sur la branche principale interdite. Utilise d'abord github_create_branch."
    return None


# Suivi des échecs CONSÉCUTIFS de github_edit_file sur un même fichier (ex: old_string mal
# recopié à répétition, conflit persistant). Le message d'erreur de l'outil pousse déjà à
# relire le fichier après un premier échec, mais rien n'empêchait l'agent de re-tenter
# indéfiniment la même approche jusqu'à épuiser son budget max_iter (voir crewquestion.py,
# developer_agent) sans jamais committer le correctif. Au-delà du seuil, le message d'erreur
# pousse explicitement vers github_write_file (contenu complet) comme stratégie de repli.
#
# ContextVar (pas un simple dict au niveau module) : un work_branch est réutilisé entre
# TOURS d'une même conversation (voir main.py), donc un simple global se souviendrait à
# tort d'échecs d'un tour précédent (déjà résolus, potentiellement via github_write_file,
# qui ne réinitialise pas ce compteur) et déclencherait une fausse alerte "échec répété" dès
# la première tentative d'un tour suivant sur ce même fichier — même raisonnement que
# _current_metrics dans crewquestion.py (qui a évité ce même piège pour les métriques de
# coût/performance). track_edit_failures() (ci-dessous) délimite sa portée à une seule
# exécution de crew, appelé par run_dynamic_crew aux côtés de track_execution_metrics.
_edit_failure_counts: ContextVar[dict[tuple[str, str, str, str], int] | None] = ContextVar(
    "edit_failure_counts", default=None
)
_EDIT_FAILURE_FALLBACK_THRESHOLD = 2
# CrewAI peut exécuter plusieurs appels d'outils natifs en parallèle (ThreadPoolExecutor)
# quand une réponse du LLM en contient plusieurs : sans ce verrou, deux appels concurrents
# de github_edit_file sur EXACTEMENT le même fichier pourraient tous les deux lire la même
# valeur avant que l'un des deux n'écrive son incrément (read-modify-write non atomique),
# perdant silencieusement un échec du compteur.
_edit_failure_lock = threading.Lock()


@contextmanager
def track_edit_failures():
    """Isole le suivi des échecs de github_edit_file à l'exécution de crew en cours (voir
    _edit_failure_counts). À utiliser en même temps que track_execution_metrics."""
    token = _edit_failure_counts.set({})
    try:
        yield
    finally:
        _edit_failure_counts.reset(token)


def _record_edit_failure(owner: str, repo: str, path: str, branch: str, reason: str, retry_hint: str) -> str:
    """reason : ce qui s'est mal passé (toujours affiché). retry_hint : le conseil à donner
    SEULEMENT si ce n'est pas (encore) un échec répété — jamais concaténé au message
    d'escalade ci-dessous, qui donnerait sinon deux instructions contradictoires dans la même
    réponse ("relis et retente" ET "n'insiste pas, change d'outil"), qu'un modèle plus petit
    pourrait suivre dans le mauvais ordre en agissant sur la première clause.
    """
    counts = _edit_failure_counts.get()
    key = (owner, repo, path, branch)
    if counts is not None:
        with _edit_failure_lock:
            count = counts.get(key, 0) + 1
            counts[key] = count
    else:
        # Hors de track_edit_failures() (ne devrait pas arriver en usage normal, l'outil
        # n'étant appelé que par un Agent pendant une exécution) : traité comme un 1er échec,
        # jamais escaladé faute de pouvoir compter les tentatives précédentes.
        count = 1
    if count >= _EDIT_FAILURE_FALLBACK_THRESHOLD:
        return (
            f"ÉCHEC RÉPÉTÉ ({count}x DE SUITE) sur '{path}' : {reason} N'insiste PAS avec une "
            "nouvelle tentative de github_edit_file sur ce même fichier. Bascule sur "
            "github_write_file, mais RELIS D'ABORD ce fichier avec github_read_file pour "
            "repartir de son contenu RÉEL (jamais de mémoire, qui a pu dériver après plusieurs "
            "tentatives manquées) : applique ta correction sur ce contenu relu, puis écris-le "
            "en entier avec github_write_file. Une réécriture complète depuis un contenu "
            "mal mémorisé effacerait silencieusement des parties du fichier."
        )
    return f"ERREUR : {reason} {retry_hint}"


def _record_edit_success(owner: str, repo: str, path: str, branch: str) -> None:
    counts = _edit_failure_counts.get()
    if counts is not None:
        with _edit_failure_lock:
            counts.pop((owner, repo, path, branch), None)


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
    try:
        gh_repo = _get_repo(owner, repo)
        content_file = gh_repo.get_contents(path, ref=branch)
        if isinstance(content_file, list):
            return f"ERREUR : '{path}' est un dossier, pas un fichier. Utilise github_list_directory."
        return content_file.decoded_content.decode("utf-8")
    except GithubException as e:
        if e.status == 404:
            return (
                f"ERREUR_FICHIER_INEXISTANT : '{path}' n'existe pas sur la branche '{branch}' de {owner}/{repo}. "
                "Inutile de réessayer la lecture de ce fichier exact."
            )
        return _github_error(e)
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
    try:
        gh_repo = _get_repo(owner, repo)
        contents = gh_repo.get_contents(path or "", ref=branch)
        if not isinstance(contents, list):
            return f"'{path}' est un fichier, pas un dossier. Utilise github_read_file."
        return "\n".join(f"{item.type}: {item.path}" for item in contents) or "INFO : dossier vide."
    except GithubException as e:
        if e.status == 404:
            return f"ERREUR_DOSSIER_INEXISTANT : '{path}' n'existe pas sur la branche '{branch}' de {owner}/{repo}."
        return _github_error(e)
    except Exception as e:
        return f"ERREUR : {str(e)}"


@tool("github_create_branch")
def github_create_branch(owner: str, repo: str, new_branch: str, base_branch: str = "main") -> str:
    """
    Crée une nouvelle branche à partir d'une branche de base, si elle n'existe pas déjà.
    Toujours appeler cet outil avant toute écriture avec github_write_file.
    Arguments:
        owner (str), repo (str), new_branch (str): nom de la branche de travail à créer.
        base_branch (str): branche source (défaut 'main').
    """
    try:
        gh_repo = _get_repo(owner, repo)
        try:
            gh_repo.get_branch(new_branch)
            return f"INFO : la branche '{new_branch}' existe déjà, elle peut être utilisée directement."
        except GithubException:
            pass
        base_ref = gh_repo.get_git_ref(f"heads/{base_branch}")
        gh_repo.create_git_ref(ref=f"refs/heads/{new_branch}", sha=base_ref.object.sha)
        return f"OK : branche '{new_branch}' créée à partir de '{base_branch}'."
    except GithubException as e:
        return _github_error(e)
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
    rejection = _reject_protected_branch(branch)
    if rejection:
        return rejection
    try:
        gh_repo = _get_repo(owner, repo)
        try:
            existing = gh_repo.get_contents(path, ref=branch)
        except GithubException as e:
            if e.status == 404:
                gh_repo.create_file(path, commit_message, content, branch=branch)
                _record_edit_success(owner, repo, path, branch)
                return f"OK : fichier '{path}' créé sur la branche '{branch}'."
            raise

        if isinstance(existing, list):
            return f"ERREUR : '{path}' est un dossier, pas un fichier."

        gh_repo.update_file(path, commit_message, content, existing.sha, branch=branch)
        # Un github_write_file réussi rétablit un contenu correct pour ce fichier : s'il
        # faisait suite à des échecs de github_edit_file (voir _record_edit_failure), ces
        # échecs ne concernent plus l'état actuel du fichier et ne doivent pas continuer à
        # être comptés comme "consécutifs" si le développeur retente une édition ciblée
        # plus tard dans la même exécution.
        _record_edit_success(owner, repo, path, branch)
        return f"OK : fichier '{path}' mis à jour sur la branche '{branch}'."
    except GithubException as e:
        return _github_error(e)
    except Exception as e:
        return f"ERREUR : {str(e)}"


@tool("github_edit_file")
def github_edit_file(
    owner: str, repo: str, path: str, branch: str, old_string: str, new_string: str, commit_message: str
) -> str:
    """
    Modifie une PARTIE d'un fichier existant en remplaçant un extrait exact par un
    autre, sans avoir à retransmettre tout le contenu du fichier. À préférer à
    github_write_file pour une correction ciblée (quelques lignes) sur un fichier déjà
    volumineux : lire tout le fichier pour n'en changer qu'une ligne coûte plus cher,
    est plus lent, et risque de faire disparaître du contenu si la régénération est
    incomplète. Utilise toujours github_write_file pour créer un nouveau fichier.
    Arguments:
        owner (str), repo (str), path (str): fichier existant à modifier.
        branch (str): branche de travail cible (jamais main/master).
        old_string (str): extrait exact à remplacer. Doit apparaître EXACTEMENT une
            fois dans le fichier ; inclus suffisamment de contexte autour de la ligne
            à changer pour le rendre unique si besoin.
        new_string (str): texte de remplacement.
        commit_message (str): message de commit.
    """
    rejection = _reject_protected_branch(branch)
    if rejection:
        return rejection
    try:
        gh_repo = _get_repo(owner, repo)
        try:
            existing = gh_repo.get_contents(path, ref=branch)
        except GithubException as e:
            if e.status == 404:
                return (
                    f"ERREUR_FICHIER_INEXISTANT : '{path}' n'existe pas sur la branche '{branch}'. "
                    "Utilise github_write_file pour créer un nouveau fichier."
                )
            raise

        if isinstance(existing, list):
            return f"ERREUR : '{path}' est un dossier, pas un fichier."

        content = existing.decoded_content.decode("utf-8")
        occurrences = content.count(old_string)
        if occurrences == 0:
            return _record_edit_failure(
                owner, repo, path, branch,
                reason=f"old_string introuvable tel quel dans '{path}'.",
                retry_hint="Relis le fichier avec github_read_file pour recopier l'extrait exact "
                "(espaces/indentation compris).",
            )
        if occurrences > 1:
            return _record_edit_failure(
                owner, repo, path, branch,
                reason=f"old_string apparaît {occurrences} fois dans '{path}', il doit être unique.",
                retry_hint="Inclus plus de contexte (lignes avant/après) pour ne cibler qu'un seul endroit.",
            )

        updated_content = content.replace(old_string, new_string, 1)
        gh_repo.update_file(path, commit_message, updated_content, existing.sha, branch=branch)
        _record_edit_success(owner, repo, path, branch)
        return f"OK : '{path}' modifié sur la branche '{branch}'."
    except GithubException as e:
        # Pas de _record_edit_failure ici (contrairement aux deux cas occurrences==0/>1
        # ci-dessus) : une erreur GitHub (rate limit, réseau, permissions...) n'a rien à voir
        # avec un old_string mal recopié, et github_write_file échouerait pour la même raison
        # d'infrastructure — pousser vers ce repli ferait perdre un appel d'outil pour rien.
        return _github_error(e)
    except Exception as e:
        return f"ERREUR : {str(e)}"


@tool("github_open_pull_request")
def github_open_pull_request(owner: str, repo: str, branch: str, base_branch: str, title: str, body: str) -> str:
    """
    Ouvre une Pull Request de la branche de travail vers la branche de base.
    À appeler une fois toutes les modifications commitées via github_write_file.
    Arguments:
        owner (str), repo (str): repository cible.
        branch (str): branche de travail (head).
        base_branch (str): branche de destination (base).
        title (str), body (str): titre et description de la Pull Request.
    """
    try:
        gh_repo = _get_repo(owner, repo)
        pr = gh_repo.create_pull(title=title, body=body, head=branch, base=base_branch)
        return f"OK : Pull Request créée avec succès : {pr.html_url}"
    except GithubException as e:
        if e.status == 422:
            return "INFO : une Pull Request existe peut-être déjà pour cette branche, ou aucune modification à proposer."
        return _github_error(e)
    except Exception as e:
        return f"ERREUR : {str(e)}"
