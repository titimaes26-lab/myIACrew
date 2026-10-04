import os
import time
from typing import NamedTuple

import github_client
import github_guards
import github_read
from github import GithubException

from delivery import merge_pull_request_body
from project_summary import summarize_project
from logs import get_logger

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
        gh_repo = github_client._get_repo(owner, repo)
        return gh_repo.get_branch(branch).commit.sha
    except GithubException as e:
        if e.status == 404:
            return None
        raise GitHubVerificationUnavailable(github_client._github_error(e)) from e
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


class DeliveredPullRequest(NamedTuple):
    """Info de la Pull Request confirmée par verify_github_delivery en cas de succès (voir plus
    bas), pour que main.py puisse l'ajouter de façon DÉTERMINISTE (URL réellement observée sur
    GitHub) au résumé de l'exécution, plutôt que de compter sur le rapport — non vérifié — du
    Développeur pour la mentionner (voir tasksquestion.yaml, qa_task : "tu n'as pas d'outil pour
    vérifier toi-même qu'une Pull Request a réellement été ouverte").
    """
    html_url: str
    merged: bool


def verify_github_delivery(
    owner: str, repo: str, branch: str, base_branch: str, sha_before: str | None
) -> tuple[DeliveredPullRequest | None, DeliveryIssue | None]:
    """Vérifie après coup, directement via l'API GitHub, qu'une Pull Request OUVERTE ou MERGÉE
    existe pour {branch} -> {base_branch} et pointe vers le commit ACTUEL de {branch} — que ce
    commit date de cette exécution (sha_before, capturé avant le lancement du crew — voir
    get_branch_head_sha, différent du HEAD actuel) ou d'une exécution précédente sur ce même
    work_branch réutilisé (voir plus bas). Renvoie (DeliveredPullRequest, None) si confirmé,
    sinon (None, DeliveryIssue) expliquant ce qui manque — jamais les deux à la fois.

    Appelée par main.py une fois le crew terminé, jamais en se fiant au texte produit par l'agent
    développeur : qa_task (tasksquestion.yaml) le dit elle-même explicitement, la QA n'a aucun
    outil pour confirmer qu'une PR a réellement été ouverte, donc rien côté agents ne peut détecter
    un rapport de "succès" où github_write.github_create_branch/github_write.github_write_file/github_open_delivery_pull_request
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
    # Réutilise get_branch_head_sha (plutôt que de refaire ici github_client._get_repo + get_branch + gestion du
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
            return None, DeliveryIssue(f"impossible de vérifier la branche '{branch}' ({e}).", True)

    if sha_after is None:
        return None, DeliveryIssue(
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
    def _find_matching_pr():
        """None si aucune PR ne correspond, sinon l'objet PullRequest trouvé (utilisé aussi bien
        pour la confirmation booléenne que pour son .html_url, voir DeliveredPullRequest)."""
        gh_repo = github_client._get_repo(owner, repo)
        pulls = gh_repo.get_pulls(state="all", head=f"{owner}:{branch}", base=base_branch)
        # merged_at (déjà présent dans la réponse de get_pulls) plutôt que p.merged : cette
        # dernière propriété PyGithub se complète paresseusement par un appel réseau SUPPLÉMENTAIRE
        # si elle n'est pas déjà dans la charge utile listée, inutile alors qu'on a déjà l'info.
        for p in pulls:
            if p.head.sha == sha_after and (p.state == "open" or p.merged_at is not None):
                return p
        return None

    # pr_check_confirmed distingue "on a réellement listé les PR et aucune ne correspond" de "la
    # liste elle-même a échoué (deux tentatives), donc on ne SAIT PAS s'il en existe une" — sans
    # cette distinction, les messages d'échec plus bas affirmeraient à tort qu'aucune PR ne
    # correspond. Réessayé une fois (même raisonnement que get_branch_head_sha plus haut, symétrique
    # depuis que le succès peut désormais dépendre de CET appel, pas seulement de get_branch_head_sha
    # ci-dessus) : cette vérification arrive juste après la rafale d'appels github_* du Développeur,
    # le moment le plus probable pour heurter un rate-limit secondaire transitoire côté GitHub.
    #
    # Le second essai n'est PAS conditionné à une exception (contrairement à get_branch_head_sha
    # plus haut) : get_pulls() est un endpoint de LISTE, avec un délai de cohérence éventuelle
    # connu côté GitHub (une PR tout juste créée — a fortiori déjà fusionnée, comme sur un repo
    # avec auto-merge activé — peut mettre quelques secondes à apparaître dans ses résultats,
    # alors qu'elle existe déjà bel et bien et serait immédiatement visible via un accès direct
    # par numéro). Un premier essai qui répond SANS exception mais ne trouve rien peut donc
    # simplement être arrivé trop tôt, pas confirmer une absence réelle — vécu en pratique : une
    # PR créée ET mergée en ~10s a échappé au premier essai.
    # pr_check_confirmed démarre à False et n'est mis à True QUE par un essai qui aboutit sans
    # exception (peu importe lequel des deux, et jamais redescendu à False ensuite) : un essai
    # réussi qui confirme déjà l'absence de PR (matched=False mais SANS exception) est une
    # réponse définitive à part entière, que le second essai (déclenché quand même, voir plus
    # bas) ne doit PAS pouvoir invalider s'il échoue lui-même sur un aléa transitoire — sinon une
    # absence de PR déjà confirmée au 1er essai basculerait à tort en "non vérifiée" (et le
    # message d'erreur orienterait à tort l'utilisateur vers GITHUB_TOKEN, voir main.py,
    # likely_access_problem) simplement parce que ce second essai, purement optionnel à ce
    # stade, a lui-même heurté un souci réseau.
    pr_check_confirmed = False
    matched_pr = None
    try:
        matched_pr = _find_matching_pr()
        pr_check_confirmed = True
    except Exception:
        pass
    if matched_pr is None:
        time.sleep(2)
        try:
            matched_pr = _find_matching_pr()
            pr_check_confirmed = True
        except Exception:
            # Ne pas réinitialiser pr_check_confirmed à False ici : s'il valait déjà True (1er
            # essai réussi), cette confirmation reste valable malgré l'échec de CE second essai.
            # S'il valait encore False (1er essai déjà en échec), il reste False, comme avant —
            # deux échecs persistants sur la LISTE des PR restent traités prudemment comme "PR
            # non confirmée" plutôt que de risquer un faux positif.
            pass
    if matched_pr is not None:
        return DeliveredPullRequest(matched_pr.html_url, matched_pr.merged_at is not None), None

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
        return None, DeliveryIssue(
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
    return None, DeliveryIssue(
        f"la branche '{branch}' existe bien sur {owner}/{repo}{commit_note} mais {pr_status_phrase}.",
        not pr_check_confirmed,
    )


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


class PartialDelivery(NamedTuple):
    """Ce qui existe RÉELLEMENT sur GitHub après une exécution en échec (voir
    describe_partial_delivery). `new_commits` : True/False si l'on sait comparer au SHA d'avant
    l'exécution, None sinon. `ahead_by` : commits d'avance sur la branche de base (None si inconnu)."""
    branch: str
    branch_exists: bool
    new_commits: bool | None
    ahead_by: int | None
    pr_url: str | None
    pr_state: str | None  # "open" | "merged" | None
    # False quand la recherche de PR a elle-même échoué : « aucune PR » serait alors une affirmation
    # non vérifiée (pr_url=None ne veut rien dire).
    pr_checked: bool = True


def describe_partial_delivery(
    owner: str, repo: str, branch: str, base_branch: str, sha_before: str | None
) -> PartialDelivery:
    """Constate (lecture seule) ce qu'une exécution ÉCHOUÉE a déjà écrit sur GitHub : branche,
    commits d'avance, Pull Request. Lève GitHubVerificationUnavailable si GitHub ne répond pas
    (l'appelant l'indique alors à l'utilisateur au lieu d'affirmer « rien n'a été poussé »).
    Les détails secondaires (comparaison, PR) sont best-effort : leur échec laisse None, il
    n'invalide pas ce qui a été constaté sur la branche."""
    sha_after = get_branch_head_sha(owner, repo, branch)
    if sha_after is None:
        return PartialDelivery(branch, False, None, None, None, None)

    new_commits = (sha_after != sha_before) if sha_before is not None else None
    ahead_by = None
    pr_url = None
    pr_state = None
    pr_checked = True
    try:
        gh_repo = github_client._get_repo(owner, repo)
        try:
            ahead_by = gh_repo.compare(base_branch, branch).ahead_by
        except Exception:
            ahead_by = None
        for pull in gh_repo.get_pulls(state="all", head=f"{owner}:{branch}", base=base_branch):
            if pull.state == "open" or pull.merged_at is not None:
                pr_url = pull.html_url
                pr_state = "merged" if pull.merged_at is not None else "open"
                break
    except Exception:
        pr_checked = False
    return PartialDelivery(branch, True, new_commits, ahead_by, pr_url, pr_state, pr_checked)


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
