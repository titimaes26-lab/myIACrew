"""Vérification de la livraison GitHub (branche, commits, PR) et description d'une livraison partielle."""
import time
from typing import NamedTuple

import github_client
import github_snapshot
from logs import get_logger

log = get_logger("github")

class DeliveryIssue(NamedTuple):
    """Résultat d'un verify_github_delivery en échec (voir plus bas). `message` : à afficher tel
    quel à l'utilisateur. `likely_access_problem` : True pour les seuls cas où le conseil "vérifie
    GITHUB_TOKEN/les permissions" est un diagnostic juste (branche introuvable ou API injoignable) ;
    False quand la branche ET ses nouveaux commits sont confirmés mais qu'il manque une PR à jour
    (l'écriture a bien fonctionné, le problème est ailleurs — l'agent n'a pas terminé sa procédure).
    Un type structuré plutôt qu'un simple texte à parser par préfixe côté appelant (execution_outcomes.py) : un
    futur reformulation de `message` ne pourrait alors plus faire dériver silencieusement ce choix
    de conseil affiché.
    """
    message: str
    likely_access_problem: bool

class DeliveredPullRequest(NamedTuple):
    """Info de la Pull Request confirmée par verify_github_delivery en cas de succès (voir plus
    bas), pour que execution_outcomes.py puisse l'ajouter de façon DÉTERMINISTE (URL réellement observée sur
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
    github_snapshot.get_branch_head_sha, différent du HEAD actuel) ou d'une exécution précédente sur ce même
    work_branch réutilisé (voir plus bas). Renvoie (DeliveredPullRequest, None) si confirmé,
    sinon (None, DeliveryIssue) expliquant ce qui manque — jamais les deux à la fois.

    Appelée par execution_outcomes.py une fois le crew terminé, jamais en se fiant au texte produit par l'agent
    développeur : qa_task (tasksquestion.yaml) le dit elle-même explicitement, la QA n'a aucun
    outil pour confirmer qu'une PR a réellement été ouverte, donc rien côté agents ne peut détecter
    un rapport de "succès" où github_create_branch/github_write_file/github_open_delivery_pull_request
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

    Limite assumée : si github_snapshot.get_branch_head_sha (appelé par execution_context.capture_branch_sha AVANT le crew) a lui-même échoué
    sur un souci transitoire, sha_before vaut None — traité ici comme "branche neuve, tout commit
    trouvé est forcément nouveau" plutôt que comme "repère non fiable". Sur un work_branch réutilisé
    qui avait déjà une PR d'un tour précédent, ce cas limite (deux échecs indépendants combinés :
    souci transitoire PUIS agent qui ne pousse rien ce tour-ci) pourrait laisser passer un tour qui
    n'a en réalité rien livré. Accepté : durcir ce cas précis demanderait de propager un troisième
    état ("repère non fiable") jusqu'ici pour un scénario déjà peu probable, alors que l'essentiel
    (détecter l'absence totale de branche/PR, le cas très majoritairement observé) reste couvert.

    """
    # Réutilise github_snapshot.get_branch_head_sha (plutôt que de refaire ici github_client._get_repo + get_branch + gestion du
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
        sha_after = github_snapshot.get_branch_head_sha(owner, repo, branch)
    except github_snapshot.GitHubVerificationUnavailable:
        time.sleep(2)
        try:
            sha_after = github_snapshot.get_branch_head_sha(owner, repo, branch)
        except github_snapshot.GitHubVerificationUnavailable as e:
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
    # correspond. Réessayé une fois (même raisonnement que github_snapshot.get_branch_head_sha, symétrique
    # depuis que le succès peut désormais dépendre de CET appel, pas seulement de github_snapshot.get_branch_head_sha) :
    # cette vérification arrive juste après la rafale d'appels github_* du Développeur, le moment le plus probable pour heurter un rate-limit secondaire transitoire côté GitHub.
    #
    # Le second essai n'est PAS conditionné à une exception (contrairement à github_snapshot.get_branch_head_sha) : get_pulls() est un endpoint de LISTE, avec un délai de cohérence éventuelle
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
    # message d'erreur orienterait à tort l'utilisateur vers GITHUB_TOKEN, voir execution_outcomes.py,
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
            # d'execution_outcomes.py est alors pertinent, contrairement au cas "confirmé absente" (False).
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
    commits d'avance, Pull Request. Lève github_snapshot.GitHubVerificationUnavailable si GitHub ne répond pas
    (l'appelant l'indique alors à l'utilisateur au lieu d'affirmer « rien n'a été poussé »).
    Les détails secondaires (comparaison, PR) sont best-effort : leur échec laisse None, il
    n'invalide pas ce qui a été constaté sur la branche."""
    sha_after = github_snapshot.get_branch_head_sha(owner, repo, branch)
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
