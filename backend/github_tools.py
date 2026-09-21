import json
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import NamedTuple

from crewai.tools import tool
from github import Auth, Github, GithubException, InputGitTreeElement


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


@tool("github_write_files")
def github_write_files(owner: str, repo: str, branch: str, commit_message: str, files_json: str) -> str:
    """
    Crée ou met à jour PLUSIEURS fichiers EN UN SEUL COMMIT, avec un seul appel d'outil quel que
    soit leur nombre. À PRÉFÉRER À github_write_file dès que tu dois créer/écrire plus de 2-3
    fichiers (ex: un nouveau projet, une fonctionnalité qui touche de nombreux composants) :
    chaque appel d'outil consomme une part de ton budget total (max_iter) pour cette tâche, et un
    projet de plusieurs dizaines de fichiers épuiserait ce budget bien avant la fin si chaque
    fichier coûtait son propre appel — tu produirais alors une réponse qui DÉCRIT le code sans
    l'avoir réellement poussé sur GitHub. github_edit_file reste préférable pour une correction
    CIBLÉE (quelques lignes) sur un seul fichier déjà existant et volumineux.
    L'écriture directe sur 'main'/'master' est refusée : crée d'abord une branche avec github_create_branch.
    Arguments:
        owner (str), repo (str): repository cible.
        branch (str): branche de travail cible (jamais main/master).
        commit_message (str): message de commit unique pour tous les fichiers de cet appel.
        files_json (str): liste JSON des fichiers à écrire (nouveaux ou existants), chacun avec
            "path" (chemin dans le repo) et "content" (contenu complet du fichier). Exemple :
            '[{"path": "src/App.tsx", "content": "..."}, {"path": "package.json", "content": "..."}]'
    """
    rejection = _reject_protected_branch(branch)
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
    if not isinstance(files, list) or not files:
        return 'ERREUR : files_json doit être une liste JSON non vide de {"path": ..., "content": ...}.'

    # Validé intégralement AVANT le premier appel réseau : un chemin dupliqué ou un élément mal
    # formé découvert à mi-parcours (ex: après avoir déjà créé des blobs pour les premiers
    # fichiers) laisserait ce commit dans un état incomplet sans possibilité de revenir en arrière
    # proprement — mieux vaut échouer d'un coup, avant toute écriture, avec un message qui permet
    # à l'agent de corriger et de retenter l'appel en entier.
    paths_seen = set()
    for f in files:
        if not isinstance(f, dict) or not isinstance(f.get("path"), str) or not isinstance(f.get("content"), str):
            return 'ERREUR : chaque élément de files_json doit être un objet avec "path" (str) et "content" (str).'
        if f["path"] in paths_seen:
            return f"ERREUR : '{f['path']}' apparaît plusieurs fois dans files_json, chaque chemin doit être unique."
        paths_seen.add(f["path"])

    try:
        gh_repo = _get_repo(owner, repo)
        ref = gh_repo.get_git_ref(f"heads/{branch}")
        base_commit = gh_repo.get_git_commit(ref.object.sha)

        # Un chemin qui correspond à un DOSSIER existant sur la branche (pas juste "déjà un
        # fichier", que create_git_tree gère très bien en le remplaçant) doit être bloqué AVANT
        # de construire l'arbre : create_git_tree accepterait sans broncher un blob au même
        # chemin qu'un dossier entier, et le commit résultant effacerait silencieusement tout ce
        # dossier (aucune ERREUR renvoyée, rien qui permette à la QA de comprendre pourquoi ces
        # fichiers ont disparu). Un seul appel recursive=True pour tout le lot plutôt qu'un
        # get_contents par chemin (qui coûterait un aller-retour réseau par fichier, à l'encontre
        # du but même de cet outil).
        existing_tree = gh_repo.get_git_tree(base_commit.tree.sha, recursive=True)
        if existing_tree.truncated:
            # Arbre trop volumineux pour être listé en entier en un seul appel (repo avec un très
            # grand nombre de fichiers) : impossible de garantir l'absence de collision avec un
            # dossier existant en dehors de la portion renvoyée. Mieux vaut refuser explicitement
            # que de risquer un écrasement silencieux non détecté par la vérification ci-dessous.
            return (
                "ERREUR : ce repository a une arborescence trop volumineuse pour être vérifiée en "
                "un seul appel (risque de collision avec un dossier existant non garanti). "
                "Utilise github_write_file séparément pour chaque fichier de ce lot à la place."
            )
        existing_dir_paths = {item.path for item in existing_tree.tree if item.type == "tree"}
        colliding = sorted(paths_seen & existing_dir_paths)
        if colliding:
            return (
                f"ERREUR : {', '.join(colliding)} correspond(ent) à un DOSSIER existant sur la "
                f"branche '{branch}', pas à un fichier — écrire dessus l'effacerait entièrement. "
                "Choisis un chemin de fichier différent (ex: à l'intérieur de ce dossier)."
            )

        tree_elements = [
            InputGitTreeElement(
                path=f["path"], mode="100644", type="blob",
                sha=gh_repo.create_git_blob(f["content"], "utf-8").sha,
            )
            for f in files
        ]
        new_tree = gh_repo.create_git_tree(tree_elements, base_commit.tree)
        new_commit = gh_repo.create_git_commit(commit_message, new_tree, [base_commit])
        ref.edit(new_commit.sha)
        for f in files:
            _record_edit_success(owner, repo, f["path"], branch)
        return (
            f"OK : {len(files)} fichier(s) écrit(s) en un seul commit ({new_commit.sha[:8]}) "
            f"sur la branche '{branch}'."
        )
    except GithubException as e:
        # "non fast-forward" (ref.edit rejeté) : CrewAI peut exécuter plusieurs appels d'outils de
        # cette même tâche en parallèle (voir _edit_failure_lock plus haut) — un autre appel
        # d'écriture sur CETTE MÊME branche a pu avancer sa référence entretemps. Rien n'est perdu
        # ni corrompu : ce commit n'a simplement jamais été appliqué. Retenter cet appel EN L'ÉTAT
        # repart de la référence à jour (get_git_ref est refait à chaque appel), donc un simple
        # nouvel essai suffit — pas besoin de reconstruire files_json.
        message = _github_error(e)
        if "fast" in message.lower() and "forward" in message.lower():
            return (
                f"{message} La branche '{branch}' a été mise à jour par un autre appel entretemps : "
                "aucun fichier de CET appel n'a été perdu, il n'a simplement pas encore été appliqué. "
                "Retente ce même appel github_write_files tel quel."
            )
        return message
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


class GitHubVerificationUnavailable(Exception):
    """Levée par get_branch_head_sha quand l'API GitHub n'a PAS pu confirmer si {branch} existe
    ou non (erreur réseau, rate-limit, credentials temporairement invalides...) — distinct d'une
    branche confirmée absente (404, cas normal au tout premier tour sur un work_branch neuf).
    Son appelant dans main.py traite spécifiquement ce cas comme "repère indisponible" (best-effort,
    n'empêche pas le lancement du crew) plutôt que de le confondre avec "branche inexistante"."""


def get_branch_head_sha(owner: str, repo: str, branch: str) -> str | None:
    """SHA du commit HEAD de {branch}, ou None si la branche n'existe PAS ENCORE (confirmé, 404).

    Lève GitHubVerificationUnavailable (jamais silencieusement None) sur toute autre erreur : un
    appelant qui confondrait "l'API n'a pas répondu" avec "la branche n'existe pas" désactiverait
    par erreur la comparaison de SHA de verify_github_delivery (voir plus bas) exactement quand
    elle est le plus utile — un work_branch réutilisé qui a déjà des commits/une PR d'un tour
    précédent.

    Appelée par main.py AVANT de lancer le crew, pour donner à verify_github_delivery un point de
    référence : sans lui, un work_branch réutilisé d'un tour précédent de la même conversation
    (voir main.py, réutilisation de work_branch entre tours) ferait passer la vérification pour
    "confirmée" même si CE tour n'a lui-même rien poussé, simplement parce qu'une branche et une
    Pull Request d'un tour PRÉCÉDENT existent toujours.
    """
    try:
        gh_repo = _get_repo(owner, repo)
        return gh_repo.get_branch(branch).commit.sha
    except GithubException as e:
        if e.status == 404:
            return None
        raise GitHubVerificationUnavailable(_github_error(e)) from e
    except Exception as e:
        raise GitHubVerificationUnavailable(str(e)) from e


class DeliveryIssue(NamedTuple):
    """Résultat d'un verify_github_delivery en échec (voir plus bas). `message` : à afficher tel
    quel à l'utilisateur. `likely_access_problem` : True pour les seuls cas où le conseil "vérifie
    GITHUB_TOKEN/les permissions" est un diagnostic juste (branche introuvable ou API injoignable) ;
    False quand la branche ET ses nouveaux commits sont confirmés mais qu'il manque une PR à jour
    (l'écriture a bien fonctionné, le problème est ailleurs — l'agent n'a pas terminé sa procédure).
    Un type structuré plutôt qu'un simple texte à parser par préfixe côté appelant (main.py) : un
    futur reformulation de `message` ne pourrait alors plus faire dériver silencieusement ce choix
    de conseil affiché.
    """
    message: str
    likely_access_problem: bool


def verify_github_delivery(
    owner: str, repo: str, branch: str, base_branch: str, sha_before: str | None
) -> DeliveryIssue | None:
    """Vérifie après coup, directement via l'API GitHub, qu'une Pull Request OUVERTE ou MERGÉE
    existe pour {branch} -> {base_branch} et pointe vers le commit ACTUEL de {branch} — que ce
    commit date de cette exécution (sha_before, capturé avant le lancement du crew — voir
    get_branch_head_sha, différent du HEAD actuel) ou d'une exécution précédente sur ce même
    work_branch réutilisé (voir plus bas). Renvoie None si confirmé, sinon un DeliveryIssue
    expliquant ce qui manque.

    Appelée par main.py une fois le crew terminé, jamais en se fiant au texte produit par l'agent
    développeur : qa_task (tasksquestion.yaml) le dit elle-même explicitement, la QA n'a aucun
    outil pour confirmer qu'une PR a réellement été ouverte, donc rien côté agents ne peut détecter
    un rapport de "succès" où github_create_branch/github_write_file/github_open_pull_request
    auraient échoué en silence (erreur retournée comme simple texte à l'agent, jamais une
    exception) ou n'auraient tout simplement jamais été appelés.

    Le SHA d'avant-exécution (sha_before) reste comparé au SHA actuel, mais n'est PLUS le seul
    critère de succès : une PR OUVERTE ou MERGÉE qui correspond déjà au commit actuel de la
    branche suffit aussi (voir plus bas), même quand ce commit date d'un tour PRÉCÉDENT sur ce
    même work_branch réutilisé. Ce choix accepte un vrai compromis (voir "Limite assumée" plus
    bas, second paragraphe) : un tour qui échoue silencieusement sur une demande NOUVELLE, alors
    qu'une PR d'un tour antérieur sans rapport pointe encore vers le commit inchangé, serait
    validé à tort par cette seule présence — exactement ce que cette vérification cherchait
    initialement à exclure. Assumé car le cas inverse (rejeter systématiquement un tour qui ne
    pousse rien de nouveau parce que le travail demandé était déjà entièrement livré) s'est avéré
    bien plus fréquent en pratique sur ce type d'usage conversationnel multi-tours.

    Limite assumée : si get_branch_head_sha (appelé par main.py AVANT le crew) a lui-même échoué
    sur un souci transitoire, sha_before vaut None — traité ici comme "branche neuve, tout commit
    trouvé est forcément nouveau" plutôt que comme "repère non fiable". Sur un work_branch réutilisé
    qui avait déjà une PR d'un tour précédent, ce cas limite (deux échecs indépendants combinés :
    souci transitoire PUIS agent qui ne pousse rien ce tour-ci) pourrait laisser passer un tour qui
    n'a en réalité rien livré. Accepté : durcir ce cas précis demanderait de propager un troisième
    état ("repère non fiable") jusqu'ici pour un scénario déjà peu probable, alors que l'essentiel
    (détecter l'absence totale de branche/PR, le cas très majoritairement observé) reste couvert.

    """
    # Réutilise get_branch_head_sha (plutôt que de refaire ici _get_repo + get_branch + gestion du
    # 404) pour ne pas avoir deux implémentations de la même logique de classification d'erreur
    # susceptibles de diverger silencieusement si l'une est retouchée sans l'autre.
    #
    # Une seule tentative avant de conclure à un échec de LIVRAISON serait trop sévère ici : cette
    # vérification arrive juste après la rafale d'appels github_* du Développeur (jusqu'à 8, sur
    # le même GITHUB_TOKEN partagé) — le moment le plus probable pour heurter un rate-limit
    # secondaire transitoire côté GitHub. Sans ce réessai, une exécution qui a RÉELLEMENT réussi
    # (branche, commits et PR bien présents) pourrait être marquée à tort en échec à cause d'un
    # simple aléa réseau survenu APRÈS coup, pendant cette seule vérification — inutile d'infliger
    # à l'utilisateur un nouveau lancement de crew (coûteux, plusieurs minutes) pour un problème
    # qui n'existe déjà plus quelques secondes après.
    try:
        sha_after = get_branch_head_sha(owner, repo, branch)
    except GitHubVerificationUnavailable:
        time.sleep(2)
        try:
            sha_after = get_branch_head_sha(owner, repo, branch)
        except GitHubVerificationUnavailable as e:
            return DeliveryIssue(f"impossible de vérifier la branche '{branch}' ({e}).", True)

    if sha_after is None:
        return DeliveryIssue(
            f"aucune branche '{branch}' n'existe sur {owner}/{repo} : aucune modification n'a "
            "été poussée sur GitHub malgré le repository cible configuré.",
            True,
        )

    # Vérifié AVANT de statuer sur "nouveau commit ou pas" (voir la docstring) : une PR OUVERTE ou
    # DÉJÀ MERGÉE à jour (head.sha == sha_after, pas juste totalCount > 0 — sur un work_branch
    # réutilisé, une PR d'un tour précédent matcherait aussi head/base sans refléter LE commit
    # actuel) démontre que le commit ACTUEL de la branche est bien proposé/accepté sur GitHub, que
    # ce commit date de cette exécution ou d'une précédente. state="all" (pas seulement "open")
    # reste nécessaire pour repérer une PR déjà mergée, mais exclut explicitement une PR FERMÉE
    # SANS fusion (rejetée) : GitHub fige alors son head.sha au moment de la fermeture, donc un
    # commit identique à celui d'une PR rejetée ne doit surtout pas compter comme "livré" — ce
    # serait au contraire le signe qu'une proposition a été explicitement refusée sur ce commit.
    # pr_check_confirmed : distingue "on a réellement listé les PR et aucune ne correspond" de "la
    # liste elle-même a échoué (except ci-dessous), donc on ne SAIT PAS s'il en existe une" — sans
    # cette distinction, les deux messages d'échec plus bas affirmeraient à tort qu'aucune PR ne
    # correspond, alors que ce second cas signifie seulement que la vérification n'a pas pu se
    # faire (ex: rate-limit transitoire juste après la rafale d'appels github_* du Développeur).
    def _matching_pr_exists() -> bool:
        gh_repo = _get_repo(owner, repo)
        pulls = gh_repo.get_pulls(state="all", head=f"{owner}:{branch}", base=base_branch)
        # merged_at (déjà présent dans la réponse de get_pulls) plutôt que p.merged : cette
        # dernière propriété PyGithub se complète paresseusement par un appel réseau SUPPLÉMENTAIRE
        # si elle n'est pas déjà dans la charge utile listée, inutile alors qu'on a déjà l'info.
        return any(p.head.sha == sha_after and (p.state == "open" or p.merged_at is not None) for p in pulls)

    # pr_check_confirmed distingue "on a réellement listé les PR et aucune ne correspond" de "la
    # liste elle-même a échoué (deux tentatives), donc on ne SAIT PAS s'il en existe une" — sans
    # cette distinction, les messages d'échec plus bas affirmeraient à tort qu'aucune PR ne
    # correspond. Réessayé une fois (même raisonnement que get_branch_head_sha plus haut, symétrique
    # depuis que le succès peut désormais dépendre de CET appel, pas seulement de get_branch_head_sha
    # ci-dessus) : cette vérification arrive juste après la rafale d'appels github_* du Développeur,
    # le moment le plus probable pour heurter un rate-limit secondaire transitoire côté GitHub.
    pr_check_confirmed = True
    try:
        if _matching_pr_exists():
            return None
    except Exception:
        time.sleep(2)
        try:
            if _matching_pr_exists():
                return None
        except Exception:
            # Ne bloque pas la vérification sur un souci PERSISTANT de LISTE des PR : la branche
            # existe bien et a été vérifiée juste au-dessus, c'est déjà la partie la plus
            # importante. On traite prudemment comme "PR non confirmée" plutôt que de risquer un
            # faux positif, et on continue vers les diagnostics ci-dessous plutôt que de conclure ici.
            pr_check_confirmed = False

    # Phrase UNIQUE, réutilisée dans les deux DeliveryIssue ci-dessous plutôt que reformulée deux
    # fois séparément : les deux messages ne peuvent alors plus décrire pr_check_confirmed de
    # façon incohérente si l'un est retouché sans l'autre. Mentionne base_branch (comme avant ce
    # réordonnancement) : indique à l'utilisateur vers quelle branche une PR était attendue, utile
    # si une PR existe mais cible la mauvaise base.
    pr_status_phrase = (
        f"aucune Pull Request à jour vers '{base_branch}' n'a été trouvée pour ce commit"
        if pr_check_confirmed
        else "la présence d'une Pull Request n'a pas pu être vérifiée (erreur transitoire de l'API GitHub)"
    )

    if sha_before is not None and sha_after == sha_before:
        # La conclusion "aucun changement n'a été livré" n'est justifiée que si pr_status_phrase
        # l'a réellement confirmé (pr_check_confirmed) : sinon, on ne SAIT PAS s'il en existe une,
        # l'affirmer à tort orienterait à tort l'utilisateur vers "rien n'a été fait" alors qu'une
        # PR valide peut très bien exister sans qu'on ait pu la voir cette fois-ci.
        conclusion = (
            "aucun changement n'a été livré pendant cette exécution, ni lors d'une précédente "
            "sur cette même branche"
            if pr_check_confirmed
            else "il est possible qu'aucun changement n'ait été livré, mais la vérification n'a "
            "pas pu le confirmer avec certitude"
        )
        return DeliveryIssue(
            f"la branche '{branch}' existe sur {owner}/{repo} mais pointe toujours sur le même "
            f"commit qu'avant le lancement de cette exécution, et {pr_status_phrase} : {conclusion}.",
            # not pr_check_confirmed : la vérification de PR a échoué deux fois de suite (API
            # injoignable), un vrai souci de connectivité/permissions — le conseil GITHUB_TOKEN
            # de main.py est alors pertinent, contrairement au cas "confirmé absente" (False).
            not pr_check_confirmed,
        )

    # sha_before is not None à ce stade garantit sha_after != sha_before (le cas égal est déjà
    # sorti par l'early return ci-dessus) : un vrai nouveau commit est donc confirmé. Si
    # sha_before est None (branche neuve avant cette exécution, ou repère indisponible — voir la
    # docstring), on ne peut rien affirmer de plus que "la branche existe".
    commit_note = " (nouveaux commits confirmés)" if sha_before is not None else ""
    return DeliveryIssue(
        f"la branche '{branch}' existe bien sur {owner}/{repo}{commit_note} mais {pr_status_phrase}.",
        not pr_check_confirmed,
    )


@tool("github_open_pull_request")
def github_open_pull_request(owner: str, repo: str, branch: str, base_branch: str, title: str, body: str) -> str:
    """
    Ouvre une Pull Request de la branche de travail vers la branche de base.
    À appeler une fois toutes les modifications commitées via github_write_file/github_write_files.
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
