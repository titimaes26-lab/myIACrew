"""Écriture GitHub : branche de travail, fichier unique, lot de fichiers, modification ciblée par blocs."""
import json

import github_client
import github_edit_failures
import github_guards
from crewai.tools import tool
from github import GithubException, InputGitTreeElement
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
        # faisait suite à des échecs de github_edit_file (voir github_edit_failures._record_edit_failure), ces
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
    return write_files_to_branch(owner, repo, branch, commit_message, files)

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
    if not isinstance(files, list) or not files:
        return 'ERREUR : la liste des fichiers doit être non vide, chaque élément {"path": ..., "content": ...}.'

    # Validé intégralement AVANT le premier appel réseau : un chemin dupliqué ou un élément mal
    # formé découvert à mi-parcours (ex: après avoir déjà créé des blobs pour les premiers
    # fichiers) laisserait ce commit dans un état incomplet sans possibilité de revenir en arrière
    # proprement — mieux vaut échouer d'un coup, avant toute écriture, avec un message qui permet
    # à l'agent de corriger et de retenter l'appel en entier.
    paths_seen = set()
    for f in files:
        if not isinstance(f, dict) or not isinstance(f.get("path"), str) or not isinstance(f.get("content"), str):
            return 'ERREUR : chaque fichier doit être un objet avec "path" (str) et "content" (str).'
        if f["path"] in paths_seen:
            return f"ERREUR : '{f['path']}' apparaît plusieurs fois dans la liste, chaque chemin doit être unique."
        paths_seen.add(f["path"])

    # Filtre les fichiers dont le contenu échoue check_syntax_content (garde-fou contre une
    # troncature silencieuse du contenu produit par diagnostic_task, voir github_guards._reject_invalid_syntax
    # plus haut) AVANT tout appel réseau — un lot entièrement invalide ne touche alors jamais
    # l'API GitHub. Commit PARTIEL (pas tout-ou-rien) volontaire : le reste de ce pipeline traite
    # déjà la livraison partielle avec rapport explicite comme mode dégradé normal (budget épuisé
    # -> committer un sous-ensemble cohérent, lister le reste comme non réalisé, voir
    # tasksquestion.yaml) — un mode tout-ou-rien pénaliserait N-1 fichiers valides à cause d'un
    # seul fichier à risque.
    valid_files, rejected = [], []
    for f in files:
        issue = github_guards._reject_sensitive_path(f["path"]) or github_guards._reject_invalid_syntax(f["path"], f["content"])
        (rejected.append((f["path"], issue)) if issue else valid_files.append(f))
    if rejected_sink is not None:
        rejected_sink.update(dict(rejected))

    if not valid_files:
        lines = "\n".join(f"- '{p}' : {reason}" for p, reason in rejected)
        return (
            f"ERREUR : les {len(rejected)} fichier(s) de ce lot ont TOUS été refusés par les contrôles "
            f"avant commit (chemin sensible ou syntaxe), rien n'a été écrit sur GitHub :\n{lines}\n"
            "Ne retente pas cet appel avec le même contenu (tu n'as aucun moyen de le corriger) : "
            "signale ces fichiers comme non livrés dans ton rapport final, avec leur raison."
        )
    # Réassigné (pas une nouvelle variable) : tout le reste de cette fonction (tree_elements,
    # la boucle github_edit_failures._record_edit_success finale, et paths_seen recalculé juste en dessous) doit
    # committer/couvrir EXCLUSIVEMENT ce sous-ensemble validé, jamais les fichiers rejetés.
    files = valid_files
    paths_seen = {f["path"] for f in files}

    try:
        gh_repo = github_client._get_repo(owner, repo)
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
            github_edit_failures._record_edit_success(owner, repo, f["path"], branch)
        message = (
            f"OK : {len(files)} fichier(s) écrit(s) en un seul commit ({new_commit.sha[:8]}) "
            f"sur la branche '{branch}'."
        )
        if rejected:
            lines = "\n".join(f"- '{p}' : {reason}" for p, reason in rejected)
            message += (
                f"\nREJETÉS ({len(rejected)}) — non committés, vérification syntaxique échouée "
                f"avant tout envoi vers GitHub :\n{lines}\n"
                "Ne retente PAS ces fichiers avec le même contenu (tu n'as aucun moyen de les "
                "corriger) : signale-les comme non livrés dans ton rapport final, avec leur raison."
            )
        return message
    except GithubException as e:
        # "non fast-forward" (ref.edit rejeté) : CrewAI peut exécuter plusieurs appels d'outils de
        # cette même tâche en parallèle (voir github_edit_failures._edit_failure_lock plus haut) — un autre appel
        # d'écriture sur CETTE MÊME branche a pu avancer sa référence entretemps. Rien n'est perdu
        # ni corrompu : ce commit n'a simplement jamais été appliqué. Retenter cet appel EN L'ÉTAT
        # repart de la référence à jour (get_git_ref est refait à chaque appel), donc un simple
        # nouvel essai suffit — pas besoin de reconstruire files_json.
        message = github_client._github_error(e)
        if "fast" in message.lower() and "forward" in message.lower():
            return (
                f"{message} La branche '{branch}' a été mise à jour par un autre appel entretemps : "
                "aucun fichier de CET appel n'a été perdu, il n'a simplement pas encore été appliqué. "
                "Retente ce même appel tel quel."
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
    rejection = github_guards._reject_protected_branch(branch) or github_guards._reject_sensitive_path(path)
    if rejection:
        return rejection
    try:
        gh_repo = github_client._get_repo(owner, repo)
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
            return github_edit_failures._record_edit_failure(
                owner, repo, path, branch,
                reason=f"old_string introuvable tel quel dans '{path}'.",
                retry_hint="Relis le fichier avec github_read.github_read_file pour recopier l'extrait exact "
                "(espaces/indentation compris).",
            )
        if occurrences > 1:
            return github_edit_failures._record_edit_failure(
                owner, repo, path, branch,
                reason=f"old_string apparaît {occurrences} fois dans '{path}', il doit être unique.",
                retry_hint="Inclus plus de contexte (lignes avant/après) pour ne cibler qu'un seul endroit.",
            )

        updated_content = content.replace(old_string, new_string, 1)
        gh_repo.update_file(path, commit_message, updated_content, existing.sha, branch=branch)
        github_edit_failures._record_edit_success(owner, repo, path, branch)
        return f"OK : '{path}' modifié sur la branche '{branch}'."
    except GithubException as e:
        # Pas de github_edit_failures._record_edit_failure ici (contrairement aux deux cas occurrences==0/>1
        # ci-dessus) : une erreur GitHub (rate limit, réseau, permissions...) n'a rien à voir
        # avec un old_string mal recopié, et github_write_file échouerait pour la même raison
        # d'infrastructure — pousser vers ce repli ferait perdre un appel d'outil pour rien.
        return github_client._github_error(e)
    except Exception as e:
        return f"ERREUR : {str(e)}"
