"""Écriture d'un LOT de fichiers en un seul commit : contrôles avant envoi, collision avec un dossier, arbre git."""
import github_client
import github_edit_failures
import github_guards
from github import GithubException, InputGitTreeElement
from logs import get_logger

log = get_logger("github")

_BAD_LIST = 'ERREUR : la liste des fichiers doit être non vide, chaque élément {"path": ..., "content": ...}.'
_BAD_ITEM = 'ERREUR : chaque fichier doit être un objet avec "path" (str) et "content" (str).'


def _invalid_batch(files) -> str | None:
    """Message d'erreur si le lot est mal formé ou contient un chemin dupliqué, None sinon.

    Validé intégralement AVANT le premier appel réseau : un chemin dupliqué ou un élément mal formé découvert à
    mi-parcours (après avoir déjà créé des blobs) laisserait ce commit dans un état incomplet sans retour arrière
    propre — mieux vaut échouer d'un coup, avec un message qui permet à l'agent de corriger et de retenter."""
    if not isinstance(files, list) or not files:
        return _BAD_LIST
    paths_seen = set()
    for f in files:
        if not isinstance(f, dict) or not isinstance(f.get("path"), str) or not isinstance(f.get("content"), str):
            return _BAD_ITEM
        if f["path"] in paths_seen:
            return f"ERREUR : '{f['path']}' apparaît plusieurs fois dans la liste, chaque chemin doit être unique."
        paths_seen.add(f["path"])
    return None


def _split_by_checks(files: list[dict]) -> tuple[list[dict], list[tuple[str, str]]]:
    """(fichiers valides, [(chemin, raison)] refusés) : chemin sensible ou syntaxe invalide (garde-fou contre une
    troncature silencieuse du contenu produit par diagnostic_task, voir github_guards._reject_invalid_syntax), AVANT
    tout appel réseau — un lot entièrement invalide ne touche jamais l'API GitHub.

    Commit PARTIEL (pas tout-ou-rien) volontaire : le reste du pipeline traite déjà la livraison partielle avec rapport
    explicite comme mode dégradé normal (voir tasksquestion.yaml) ; un mode tout-ou-rien pénaliserait N-1 fichiers
    valides à cause d'un seul fichier à risque."""
    valid_files, rejected = [], []
    for f in files:
        issue = github_guards._reject_sensitive_path(f["path"]) or github_guards._reject_invalid_syntax(f["path"], f["content"])
        (rejected.append((f["path"], issue)) if issue else valid_files.append(f))
    return valid_files, rejected


def _rejected_lines(rejected: list[tuple[str, str]]) -> str:
    return "\n".join(f"- '{p}' : {reason}" for p, reason in rejected)


def _all_rejected_message(rejected: list[tuple[str, str]]) -> str:
    return (
        f"ERREUR : les {len(rejected)} fichier(s) de ce lot ont TOUS été refusés par les contrôles "
        f"avant commit (chemin sensible ou syntaxe), rien n'a été écrit sur GitHub :\n{_rejected_lines(rejected)}\n"
        "Ne retente pas cet appel avec le même contenu (tu n'as aucun moyen de le corriger) : "
        "signale ces fichiers comme non livrés dans ton rapport final, avec leur raison."
    )


def _folder_collision_error(gh_repo, base_commit, branch: str, paths: set[str]) -> str | None:
    """Message d'erreur si un chemin du lot correspond à un DOSSIER existant (ou si l'arbre est trop gros pour le
    savoir), None sinon.

    create_git_tree accepterait sans broncher un blob au même chemin qu'un dossier entier, et le commit résultant
    effacerait silencieusement tout ce dossier. Un seul appel recursive=True pour tout le lot plutôt qu'un
    get_contents par chemin (un aller-retour réseau par fichier, à l'encontre du but de cet outil)."""
    existing_tree = gh_repo.get_git_tree(base_commit.tree.sha, recursive=True)
    if existing_tree.truncated:
        # Impossible de garantir l'absence de collision en dehors de la portion renvoyée : mieux vaut refuser que de
        # risquer un écrasement silencieux.
        return (
            "ERREUR : ce repository a une arborescence trop volumineuse pour être vérifiée en "
            "un seul appel (risque de collision avec un dossier existant non garanti). "
            "Utilise github_write_file séparément pour chaque fichier de ce lot à la place."
        )
    existing_dir_paths = {item.path for item in existing_tree.tree if item.type == "tree"}
    colliding = sorted(paths & existing_dir_paths)
    if colliding:
        return (
            f"ERREUR : {', '.join(colliding)} correspond(ent) à un DOSSIER existant sur la "
            f"branche '{branch}', pas à un fichier — écrire dessus l'effacerait entièrement. "
            "Choisis un chemin de fichier différent (ex: à l'intérieur de ce dossier)."
        )
    return None


def _commit_files(gh_repo, branch: str, commit_message: str, files: list[dict], base_commit, ref) -> str:
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
    return new_commit.sha


def _non_fast_forward_message(message: str, branch: str) -> str | None:
    """« non fast-forward » (ref.edit rejeté) : CrewAI peut exécuter plusieurs appels d'outils de cette même tâche en
    parallèle (voir github_edit_failures._edit_failure_lock) — un autre appel d'écriture sur CETTE MÊME branche a pu
    avancer sa référence entretemps. Rien n'est perdu ni corrompu : ce commit n'a jamais été appliqué, et retenter
    l'appel en l'état repart de la référence à jour (get_git_ref est refait à chaque appel)."""
    if "fast" in message.lower() and "forward" in message.lower():
        return (
            f"{message} La branche '{branch}' a été mise à jour par un autre appel entretemps : "
            "aucun fichier de CET appel n'a été perdu, il n'a simplement pas encore été appliqué. "
            "Retente ce même appel tel quel."
        )
    return None


def write_files_to_branch(
    owner: str, repo: str, branch: str, commit_message: str, files,
    rejected_sink: dict[str, str] | None = None,
) -> str:
    """Implémentation de github_write_files, factorée en fonction Python pure pour être aussi
    appelée par github_commit_analyst_files (crew_tools.py) avec les fichiers extraits en
    Python de la sortie de diagnostic_task — mêmes garde-fous (branche protégée, syntaxe,
    collision avec un dossier), sans passer par un JSON rédigé par le LLM.
    rejected_sink reçoit {chemin: raison} des fichiers rejetés par la vérification syntaxique,
    pour que l'appelant n'ait pas à refaire ce contrôle."""
    rejection = github_guards._reject_protected_branch(branch)
    if rejection:
        return rejection
    invalid = _invalid_batch(files)
    if invalid:
        return invalid

    files, rejected = _split_by_checks(files)
    if rejected_sink is not None:
        rejected_sink.update(dict(rejected))
    if not files:
        return _all_rejected_message(rejected)
    # Tout ce qui suit committe/couvre EXCLUSIVEMENT ce sous-ensemble validé, jamais les fichiers rejetés.
    paths = {f["path"] for f in files}

    try:
        gh_repo = github_client._get_repo(owner, repo)
        ref = gh_repo.get_git_ref(f"heads/{branch}")
        base_commit = gh_repo.get_git_commit(ref.object.sha)
        collision = _folder_collision_error(gh_repo, base_commit, branch, paths)
        if collision:
            return collision
        commit_sha = _commit_files(gh_repo, branch, commit_message, files, base_commit, ref)
        for f in files:
            github_edit_failures._record_edit_success(owner, repo, f["path"], branch)
        message = (
            f"OK : {len(files)} fichier(s) écrit(s) en un seul commit ({commit_sha[:8]}) "
            f"sur la branche '{branch}'."
        )
        if rejected:
            message += (
                f"\nREJETÉS ({len(rejected)}) — non committés, vérification syntaxique échouée "
                f"avant tout envoi vers GitHub :\n{_rejected_lines(rejected)}\n"
                "Ne retente PAS ces fichiers avec le même contenu (tu n'as aucun moyen de les "
                "corriger) : signale-les comme non livrés dans ton rapport final, avec leur raison."
            )
        return message
    except GithubException as e:
        message = github_client._github_error(e)
        return _non_fast_forward_message(message, branch) or message
    except Exception as e:
        return f"ERREUR : {str(e)}"
