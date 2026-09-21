import asyncio
import traceback
import os
import uuid
from datetime import datetime

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from sqlmodel import Session, select

from crewquestion import AppDevelopmentCrew, AnalysisReport, CrewStepError, build_conversation_context, track_execution_metrics
from database import create_db_and_tables, get_session, engine, Conversation, ExecutionHistory
from auth import get_current_user, close_http_client
from github_tools import verify_github_delivery, get_branch_head_sha, GitHubVerificationUnavailable

# 1. INSTANCIATION DE FASTAPI (Obligatoire au tout début !)
app = FastAPI(title="CrewAI App Development API")

# 2. ÉVÉNEMENT DE DÉMARRAGE (Création des tables BDD)
@app.on_event("startup")
def on_startup():
    create_db_and_tables()

# Ferme proprement le client HTTP partagé de auth.py (voir sa docstring) plutôt que de
# laisser ses connexions ouvertes à l'arrêt du process.
@app.on_event("shutdown")
async def on_shutdown():
    await close_http_client()

# 3. CONFIGURATION CORS
# Constantes (pas juste inline dans add_middleware) : relues par _log_unhandled_exception plus
# bas, qui doit reproduire la même décision d'autorisation d'origine/identifiants que
# CORSMiddleware — une seule source de vérité, pour qu'un futur changement de l'une de ces deux
# valeurs ne puisse pas être oublié dans l'un des deux endroits qui en dépendent.
CORS_ALLOW_ORIGINS = ["*"]
CORS_ALLOW_CREDENTIALS = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Conversation-Id"],
)

# Filet de sécurité global : ne remplace PAS les try/except explicites des endpoints (ex:
# execute_workflow imprime déjà sa propre trace détaillée et marque db_entry "failed" avant de
# lever HTTPException — Starlette route un HTTPException vers le handler dédié que FastAPI
# enregistre lui-même, plus spécifique que celui-ci, donc ce handler-ci ne l'intercepte jamais).
# Couvre uniquement le cas où une exception échapperait à TOUT try/except existant (ex: bug dans
# une dépendance, erreur avant le try d'un endpoint) : sans ce filet, Starlette renvoie quand même
# un 500 par défaut, mais rien ne garantit que sa trace complète soit toujours visible en clair
# dans les logs applicatifs — exactement le genre de "500 sans aucune trace exploitable" observé
# sur une exécution réelle, qu'il ne faut plus jamais laisser invisible.
#
# Un handler enregistré sur la classe Exception NUE (comme ici) est extrait par Starlette dans
# ServerErrorMiddleware, qui enveloppe TOUT le reste — y compris CORSMiddleware ci-dessus — et non
# l'inverse : sa réponse ne passe donc JAMAIS par l'injection d'en-têtes CORS de CORSMiddleware.
# Sans les ajouter nous-mêmes ici, le navigateur d'un frontend cross-origin (allow_origins=["*"])
# rejetterait cette réponse 500 comme une erreur CORS opaque ("Failed to fetch" côté JS), cachant
# le vrai statut/contenu à l'utilisateur malgré un vrai 500 bien envoyé sur le fil — exactement le
# symptôme observé (log Render confirmant un 500, mais l'interface n'affiche qu'une erreur
# générique). allow_credentials=True interdit le littéral "*" : il faut réfléchir l'Origin exacte
# de la requête, comme le ferait CORSMiddleware lui-même.
@app.exception_handler(Exception)
async def _log_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    # Ce diagnostic (print/lecture mémoire) ne doit JAMAIS empêcher de renvoyer une réponse :
    # print() lui-même peut échouer (pipe de logs saturé/coupé, disque plein — plausible pile
    # dans les conditions de pression mémoire que ce diagnostic vise à détecter). Une exception
    # ICI, dans ce handler global lui-même, ne serait rattrapée par personne (ServerErrorMiddleware
    # ne retombe sur son propre filet de sécurité QUE quand aucun handler custom n'est enregistré,
    # ce qui n'est plus le cas dès qu'on en définit un) : la connexion serait alors abandonnée
    # sans aucune réponse, exactement le symptôme opaque ("Failed to fetch") que ce handler existe
    # pour éliminer.
    try:
        _log_memory(f"exception non gérée sur {request.url.path}")
        print(f"--- EXCEPTION NON GEREE SUR {request.method} {request.url.path} ---")
        print(traceback.format_exc())
    except Exception:
        pass

    # Message générique (pas str(exc)) : contrairement aux deux endpoints qui choisissent
    # délibérément d'exposer le détail d'une erreur CrewAI connue, ce filet attrape N'IMPORTE
    # QUELLE exception non anticipée sur N'IMPORTE QUEL endpoint — le détail réel reste
    # disponible dans les logs Render (print ci-dessus), jamais renvoyé tel quel au client.
    response = JSONResponse(status_code=500, content={"detail": "Erreur interne du serveur."})

    # Reproduit la décision d'autorisation d'origine de CORSMiddleware (voir CORS_ALLOW_ORIGINS) :
    # un handler enregistré sur la classe Exception nue est extrait par Starlette dans
    # ServerErrorMiddleware, qui enveloppe TOUT le reste — y compris CORSMiddleware — et non
    # l'inverse, donc cette réponse ne passe JAMAIS par l'injection d'en-têtes CORS habituelle.
    # Sans ça, le navigateur d'un frontend cross-origin rejetterait ce 500 comme une erreur CORS
    # opaque ("Failed to fetch" côté JS), cachant le vrai statut/contenu à l'utilisateur malgré un
    # vrai 500 bien envoyé sur le fil. CORS_ALLOW_CREDENTIALS=True interdit le littéral "*" : il
    # faut réfléchir l'Origin exacte de la requête (uniquement si elle est bien autorisée par
    # CORS_ALLOW_ORIGINS), plus Vary: Origin — comme le fait CORSMiddleware lui-même — pour qu'un
    # éventuel cache intermédiaire ne serve jamais la réponse d'une origine à une autre. Ne couvre
    # que allow_origins/allow_credentials (les seules options CORS utilisées ci-dessus) : un futur
    # allow_origin_regex sur CORSMiddleware devrait être répercuté ici aussi.
    origin = request.headers.get("origin")
    if origin and ("*" in CORS_ALLOW_ORIGINS or origin in CORS_ALLOW_ORIGINS):
        response.headers["Access-Control-Allow-Origin"] = origin
        if CORS_ALLOW_CREDENTIALS:
            response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
    return response

crew_instance = AppDevelopmentCrew()

class UserRequestInput(BaseModel):
    user_request: str

class WorkflowExecutionInput(BaseModel):
    user_request: str
    target_workflow: str
    clarifications: Optional[str] = ""
    repo_owner: Optional[str] = None
    repo_name: Optional[str] = None
    base_branch: Optional[str] = "main"
    conversation_id: Optional[int] = None

class ConversationCreateInput(BaseModel):
    title: Optional[str] = None

def _current_memory_mb() -> Optional[float]:
    """RSS (mémoire physique réellement utilisée par ce process) en Mo, lue depuis
    /proc/self/status (Linux uniquement — couvre tout environnement de déploiement réaliste ici :
    Render, Docker...). Best-effort : None si indisponible (OS différent, fichier absent) plutôt
    qu'une exception — un simple diagnostic ne doit jamais faire échouer une exécution par
    ailleurs saine.

    Ajouté pour diagnostiquer les cas où le process backend semble mourir sans laisser aucune
    trace applicative (voir _log_memory, appelé à chaque changement d'étape d'exécution) : sur le
    plan gratuit de Render, ni l'onglet "Events" (qui indiquerait un OOM kill explicitement) ni le
    graphique mémoire des "Metrics" ne sont accessibles, ce print() dans les logs applicatifs est
    donc le seul moyen de voir la tendance mémoire avant une éventuelle coupure brutale — un OOM
    kill (SIGKILL) tue le process instantanément, sans qu'aucune exception Python ne soit jamais
    levée ni journalisée : seules ces lectures PÉRIODIQUES avant le crash peuvent le suggérer
    (une dernière valeur déjà élevée juste avant l'arrêt net des logs), jamais une preuve directe.
    """
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024  # kB -> Mo
    except Exception:
        pass
    return None

def _log_memory(context: str) -> None:
    # Best-effort, y compris print() lui-même : appelée depuis des points qui doivent absolument
    # ne jamais lever (le except d'execute_workflow avant que db_entry ne soit marqué "failed", et
    # _persist_current_step avant la classification CrewStepError — voir leurs docstrings). print()
    # peut échouer (pipe de logs saturé/coupé, disque plein) précisément dans les conditions de
    # pression mémoire que ce diagnostic vise à observer ; sans cette garde, l'échec d'un simple
    # print() de diagnostic ferait dérailler l'exécution qu'il essaie seulement d'observer.
    try:
        mem_mb = _current_memory_mb()
        if mem_mb is not None:
            print(f"[MEM] {context} : {mem_mb:.0f} Mo (RSS)")
    except Exception:
        pass

def _safe_refresh(session: Session, db_entry: ExecutionHistory, context: str) -> None:
    """Recharge db_entry depuis la base avant d'y réassigner des champs — voir ses appelants.

    Nécessaire avant de réassigner current_step à None (succès ou échec) : sans ce refresh, la
    Session de CETTE requête ignore les écritures faites entretemps par _persist_current_step via
    SA PROPRE Session (voir plus bas), et SQLAlchemy — comparant à sa valeur en mémoire périmée
    (None depuis la création de db_entry, jamais relue depuis) plutôt qu'à la valeur réellement en
    base (ex: 'qa') — omettrait purement et simplement cette colonne de l'UPDATE suivant.

    Un pépin ici (ex: connexion DB coupée par le pooler Postgres de Supabase après une longue
    exécution FEATURE/DESIGN_AND_DEV pendant laquelle cette Session est restée inactive) NE DOIT
    PAS empêcher d'enregistrer l'issue réelle de l'exécution : rollback() remet la Session dans un
    état utilisable pour le commit() qui suit (sans lui, celui-ci échouerait à son tour avec une
    erreur DIFFÉRENTE — sur la transaction invalidée, pas la connexion), et l'erreur n'est que
    loggée ; les assignations faites par l'appelant juste après restent valables dans tous les cas.
    """
    try:
        session.refresh(db_entry)
    except Exception as e:
        session.rollback()
        print(f"AVERTISSEMENT : échec du refresh de db_entry avant finalisation ({context}, id={db_entry.id}) : {type(e).__name__}: {e}")

def _persist_current_step(execution_id: int, step_key: Optional[str]) -> None:
    """Invoqué par run_dynamic_crew (voir crewquestion.py) à chaque changement d'étape.

    step_key=None efface la progression affichée (fin d'exécution, ou pause avant une nouvelle
    tentative suite à une erreur de quota — voir l'appel correspondant dans crewquestion.py).

    Ouvre sa propre Session plutôt que de réutiliser celle de la requête HTTP en cours : ce
    callback est appelé par CrewAI depuis le thread d'arrière-plan de kickoff_async (pas le
    thread de la requête FastAPI qui, lui, est simplement suspendu sur l'await), et une Session
    SQLAlchemy n'est pas conçue pour être partagée entre threads même sans accès concurrent réel.

    Best-effort, volontairement : appelé à la fois AVANT le try/except de run_dynamic_crew (pour
    annoncer la toute première étape) et depuis task_callback pendant kickoff_async, donc une
    exception ici non rattrapée remonterait soit sans passer par ce try/except du tout, soit
    empoisonnerait CrewStepError en désignant à tort l'agent en cours comme responsable de
    l'échec — dans les deux cas, une simple panne d'affichage de la progression ferait échouer
    (ou mal diagnostiquer) une exécution par ailleurs saine.
    """
    # Appelé à CHAQUE changement d'étape (donc plusieurs fois par exécution) : le point le plus
    # régulier disponible pour observer la tendance mémoire pendant une exécution DESIGN_AND_DEV/
    # FEATURE, qui peut enchaîner 4 tâches sur plusieurs minutes. Voir _current_memory_mb.
    _log_memory(f"execution_id={execution_id}, étape={step_key!r}")
    try:
        with Session(engine) as step_session:
            entry = step_session.get(ExecutionHistory, execution_id)
            if entry is not None:
                entry.current_step = step_key
                step_session.add(entry)
                step_session.commit()
    except Exception as e:
        print(f"AVERTISSEMENT : échec de la mise à jour de la progression (execution_id={execution_id}, step={step_key!r}) : {type(e).__name__}: {e}")

# 4. ENDPOINTS API
@app.get("/")
def read_root():
    return {"status": "API CrewAI opérationnelle"}

@app.post("/api/qualify", response_model=AnalysisReport)
async def qualify_request(data: UserRequestInput, user: dict = Depends(get_current_user)):
    """Étape 1 : Qualification du besoin"""
    try:
        report = await crew_instance.analyze_user_request(data.user_request)
        crew_instance.save_analysis_report(report, data.user_request)
        return report
    except Exception as e:
        print("--- ERREUR CREWAI DETECTEE ---")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/execute")
async def execute_workflow(
    data: WorkflowExecutionInput,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Étape 2 : Lancement dynamique des agents & enregistrement BDD"""
    _log_memory("début /api/execute")
    final_prompt = (
        f"Demande initiale : {data.user_request}\n"
        f"Type d'exécution : {data.target_workflow}\n"
        f"Précisions apportées : {data.clarifications if data.clarifications else 'Aucune.'}"
    )

    has_repo_target = bool(data.repo_owner and data.repo_name)
    # ANALYSE_ONLY ne comprend que design_task/architecture_task, en lecture seule (voir
    # run_dynamic_crew dans crewquestion.py) : jamais de branche/commit/PR à vérifier pour ce
    # workflow. Calculé une seule fois et réutilisé aux deux points qui en ont besoin plus bas
    # (capture du SHA de référence avant le crew, vérification après coup) plutôt que dupliqué,
    # pour qu'ils ne puissent pas diverger silencieusement si l'un est modifié sans l'autre.
    should_verify_github_delivery = has_repo_target and data.target_workflow != "ANALYSE_ONLY"

    if data.conversation_id is not None:
        conversation = session.get(Conversation, data.conversation_id)
        if not conversation or conversation.user_id != user.get("id"):
            raise HTTPException(status_code=404, detail="Conversation introuvable.")
    else:
        conversation = Conversation(
            user_id=user.get("id"),
            title=data.user_request.strip()[:80] or "Nouvelle conversation",
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)

    # Tours précédents de cette conversation : donnent aux agents un rappel de ce qui a
    # déjà été demandé/livré, et permettent de continuer sur la même branche de travail
    # plutôt que d'en ouvrir une nouvelle déconnectée à chaque message (voir plus bas).
    prior_entries = session.exec(
        select(ExecutionHistory)
        .where(ExecutionHistory.conversation_id == conversation.id)
        .order_by(ExecutionHistory.created_at.asc())
    ).all()
    conversation_context = build_conversation_context(prior_entries)

    # Empêche deux exécutions concurrentes sur la même conversation. Nécessaire depuis la
    # réutilisation du work_branch entre tours (voir plus bas) : sans ce garde-fou, deux
    # requêtes lancées en parallèle sur la même conversation (ex: double clic, deux onglets)
    # écriraient toutes les deux sur la même branche via github_write_file/github_edit_file,
    # qui se basent sur le SHA du fichier pour détecter les conflits (optimistic concurrency) —
    # l'une des deux échouerait alors avec un SHA obsolète au lieu d'une erreur claire.
    # Limite connue : un tour resté bloqué à "running" (ex: crash serveur en cours d'exécution,
    # qui saute le bloc except ci-dessous) bloquerait la conversation jusqu'à correction manuelle
    # de son statut en base ; accepté ici plutôt que d'ajouter un mécanisme d'expiration.
    if any(entry.status == "running" for entry in prior_entries):
        raise HTTPException(
            status_code=409,
            detail="Une exécution est déjà en cours pour cette conversation. Attends qu'elle se termine avant d'envoyer un nouveau message.",
        )

    # Normalisé une seule fois : utilisé à la fois pour comparer aux tours précédents et
    # pour ce qui est stocké sur ce tour, afin que les deux restent cohérents (sinon un
    # base_branch vide explicitement envoyé empêcherait à tort la réutilisation de
    # branche au tour suivant, qui compare toujours à la valeur normalisée).
    normalized_base_branch = (data.base_branch or "main") if has_repo_target else None

    work_branch = ""
    if has_repo_target:
        for entry in reversed(prior_entries):
            if (
                entry.work_branch
                and entry.repo_owner == data.repo_owner
                and entry.repo_name == data.repo_name
                and entry.base_branch == normalized_base_branch
            ):
                work_branch = entry.work_branch
                break
        if not work_branch:
            work_branch = f"crewai/{data.target_workflow.lower()}-{uuid.uuid4().hex[:8]}"

    # Enregistrement immédiat (statut "running") pour garder une trace même en cas d'échec
    db_entry = ExecutionHistory(
        user_request=data.user_request,
        workflow=data.target_workflow,
        clarifications=data.clarifications,
        status="running",
        user_id=user.get("id"),
        conversation_id=conversation.id,
        repo_owner=data.repo_owner if has_repo_target else None,
        repo_name=data.repo_name if has_repo_target else None,
        base_branch=normalized_base_branch,
        work_branch=work_branch or None,
    )
    session.add(db_entry)
    session.commit()
    session.refresh(db_entry)

    # Une instance FRAÎCHE par exécution, pas le crew_instance partagé utilisé par
    # /api/qualify : les méthodes décorées @task/@agent de AppDevelopmentCrew (design_task(),
    # architecture_task()...) sont mémoïsées par CrewAI sur (nom de méthode, id(self)) — voir
    # crewai/project/utils.py, `_make_hashable` traite `self` comme `("__instance__", id(self))`.
    # Avec le singleton crew_instance (même `self` pour toutes les requêtes, pour toujours),
    # deux exécutions qui partagent un rôle de tâche (ex: deux BUGFIX simultanés dans DEUX
    # conversations différentes — le contrôle de concurrence plus haut n'empêche qu'une même
    # conversation d'avoir deux exécutions en vol, pas deux conversations différentes en
    # parallèle) recevraient LE MÊME objet Task pour ce rôle, et donc partageraient sa
    # `.callback` et son `.output` en cours d'exécution : bien plus grave qu'un simple souci
    # d'affichage de progression, cela mélangerait de vrais résultats d'agents entre deux
    # exécutions concurrentes sans rapport. Une instance locale à CETTE requête (vivante le temps
    # de l'await ci-dessous, donc jamais partagée avec une autre requête en vol) donne un id(self)
    # distinct et donc des objets Task/Agent distincts pour toute la durée de cette exécution.
    #
    # Compromis assumé : le cache de mémoïsation de CrewAI (crewai.project.utils.cache, un
    # dict module-level SANS éviction) grossit alors d'une poignée d'entrées à CHAQUE exécution
    # au lieu de rester borné à la taille du singleton précédent — une lente fuite mémoire,
    # bornée par le nombre total d'exécutions depuis le démarrage du process. Volontairement
    # accepté plutôt que de continuer à réutiliser le singleton : l'alternative (des exécutions
    # concurrentes de conversations différentes partageant, via ce même cache, le MÊME objet
    # Task — donc sa `.callback` et son `.output` pendant qu'il s'exécute) mélangerait de vrais
    # résultats d'agents entre exécutions sans rapport, un risque de corruption de données bien
    # plus grave qu'une croissance mémoire lente sur un service de cette échelle. Pas de moyen
    # public d'éviction connu côté CrewAI à ce jour ; un redémarrage périodique du service reste
    # le filet de sécurité si la mémoire venait un jour à réellement poser problème.
    #
    try:
        # await asyncio.to_thread(...) et non un appel direct : AppDevelopmentCrew() (la
        # métaclasse @CrewBase de crewai, voir crewai/project/crew_base.py) relit et reparse
        # agentsquestion.yaml/tasksquestion.yaml depuis le disque et résout la config de TOUS
        # les agents qui y sont définis à chaque construction, plus seulement une fois au
        # démarrage du serveur comme avec l'ancien singleton — un appel direct bloquerait la
        # boucle asyncio (donc toutes les autres requêtes concurrentes, y compris le sondage de
        # progression d'autres conversations) le temps de ce travail, à CHAQUE /api/execute.
        #
        # À L'INTÉRIEUR de ce try (pas juste avant) : une erreur ici (ex: agentsquestion.yaml
        # temporairement illisible) doit être rattrapée par le except plus bas comme tout autre
        # échec d'exécution (db_entry marqué "failed", pas laissé bloqué pour toujours sur
        # "running" — voir le contrôle de concurrence plus haut, et /api/history qui refuse
        # désormais de supprimer une ligne "running").
        async def _repo_branch_sha_before() -> str | None:
            # Capturé AVANT le lancement du crew (jamais après) : c'est le repère utilisé plus
            # bas par verify_github_delivery pour distinguer "cette exécution a réellement poussé
            # un nouveau commit" de "une branche/PR d'un tour précédent existe toujours" sur un
            # work_branch réutilisé entre tours d'une même conversation. None (branche pas encore
            # créée : cas normal au tout premier tour) reste distinct d'une erreur transitoire de
            # l'API GitHub (GitHubVerificationUnavailable) : les deux sont volontairement traités
            # pareil ici (repère indisponible, best-effort) plutôt que de laisser un simple souci
            # réseau côté vérification empêcher le lancement du crew lui-même.
            if not should_verify_github_delivery:
                return None
            try:
                return await asyncio.to_thread(get_branch_head_sha, data.repo_owner, data.repo_name, work_branch)
            except GitHubVerificationUnavailable:
                return None

        # asyncio.gather (pas deux `await` séquentiels) : AppDevelopmentCrew() (parsing des YAML,
        # thread séparé) et la capture du SHA de référence (aller-retour réseau vers l'API GitHub)
        # sont deux opérations indépendantes qui ne dépendent l'une de l'autre en rien, autant les
        # laisser se chevaucher plutôt que d'ajouter inutilement leurs latences bout à bout.
        crew_for_this_execution, repo_branch_sha_before = await asyncio.gather(
            asyncio.to_thread(AppDevelopmentCrew),
            _repo_branch_sha_before(),
        )
        _log_memory(f"execution_id={db_entry.id}, crew instancié, avant kickoff")

        with track_execution_metrics() as run_metrics:
            result = await crew_for_this_execution.run_dynamic_crew(
                inputs={
                    'user_request': final_prompt,
                    'conversation_context': conversation_context,
                    'repo_owner': data.repo_owner or '',
                    'repo_name': data.repo_name or '',
                    # `or 'main'` : nécessaire ici (contrairement à db_entry.base_branch
                    # plus haut) car normalized_base_branch est None sans repository cible,
                    # et les tâches interpolent toujours {base_branch} même dans ce cas.
                    'base_branch': normalized_base_branch or 'main',
                    'work_branch': work_branch,
                    'repo_instructions': (
                        f"Repository GitHub cible : {data.repo_owner}/{data.repo_name}\n"
                        f"Branche de base : {normalized_base_branch}\n"
                        f"Branche de travail à créer et utiliser pour toute écriture : {work_branch}"
                        if has_repo_target
                        else "Aucun repository GitHub cible fourni : n'utilise aucun outil github_*, travaille uniquement sur le disque local."
                    ),
                },
                request_type=data.target_workflow,
                on_step_change=lambda step_key: _persist_current_step(db_entry.id, step_key),
            )
        raw_result = str(result.raw) if hasattr(result, 'raw') else str(result)

        # Un rapport d'agent "réussi" ne prouve rien de ce qui s'est réellement passé sur GitHub
        # (voir qa_task dans tasksquestion.yaml : la QA elle-même n'a aucun outil pour confirmer
        # qu'une PR a été ouverte) : un échec silencieux d'un outil github_* (erreur renvoyée
        # comme simple texte à l'agent, jamais une exception qui ferait échouer ce try) ou un
        # agent qui n'appelle tout simplement jamais ces outils produirait quand même ce même
        # statut "success" sans qu'aucune modification n'ait été poussée sur GitHub. Vérifié
        # uniquement quand un développement a effectivement eu lieu (ANALYSE_ONLY ne comprend que
        # design_task/architecture_task, en lecture seule — voir run_dynamic_crew) : sans cela,
        # cette vérification échouerait toujours à tort sur ce workflow, qui n'a jamais eu
        # l'intention de créer de branche ou de PR.
        if should_verify_github_delivery:
            # await asyncio.to_thread(...) : verify_github_delivery fait des appels HTTP
            # bloquants (PyGithub) — comme pour crew_for_this_execution plus haut, un appel
            # direct bloquerait la boucle asyncio, donc toutes les autres requêtes concurrentes,
            # le temps de l'aller-retour réseau vers l'API GitHub.
            delivery_issue = await asyncio.to_thread(
                verify_github_delivery, data.repo_owner, data.repo_name, work_branch,
                normalized_base_branch, repo_branch_sha_before,
            )
            if delivery_issue:
                # likely_access_problem (champ structuré de DeliveryIssue, pas un texte à parser
                # par préfixe) distingue les cas où recommander de vérifier GITHUB_TOKEN est un
                # diagnostic juste (branche introuvable, API injoignable) de ceux où la branche ET
                # ses nouveaux commits sont confirmés mais où il manque une PR à jour : l'écriture
                # a alors bien fonctionné, le problème est que l'agent développeur n'a pas terminé
                # sa procédure (ex: budget d'appels d'outils épuisé avant l'ouverture de la Pull
                # Request) — un conseil GITHUB_TOKEN y serait un diagnostic faux.
                if delivery_issue.likely_access_problem:
                    remediation = (
                        "Vérifie la configuration GITHUB_TOKEN du backend (présence, permissions "
                        "d'écriture sur ce repository) puis relance."
                    )
                else:
                    # Deux causes possibles, indiscernables depuis ce seul constat (une lecture
                    # GitHub a réussi, mais rien de neuf n'a été confirmé en écriture) : soit
                    # GITHUB_TOKEN peut lire ce repository mais n'a pas les permissions d'écriture
                    # nécessaires, soit le Développeur n'a pas terminé sa procédure GitHub (budget
                    # d'appels d'outils épuisé, étape non atteinte). Volontairement pas plus
                    # précis sur l'étape en cause (ex: "avant d'ouvrir la Pull Request") : ce même
                    # DeliveryIssue est aussi renvoyé quand AUCUN commit n'a été poussé du tout,
                    # pas seulement quand il ne manque que la Pull Request finale.
                    remediation = (
                        "Cela peut venir d'un manque de permissions d'écriture du GITHUB_TOKEN "
                        "configuré sur ce repository, ou du Développeur qui n'a pas terminé sa "
                        "procédure GitHub : vérifie les deux, puis relance."
                    )
                # raw_result (plan et fichiers annoncés par le Développeur, rapport de la QA) est
                # inclus tel quel plutôt que perdu : bien qu'invérifié côté GitHub, il reste utile
                # à l'utilisateur pour comprendre ce que l'agent a effectivement tenté avant que
                # cette vérification n'échoue, notamment pour juger s'il faut relancer tel quel ou
                # reformuler la demande.
                raise RuntimeError(
                    "Un repository GitHub cible était configuré mais la vérification après coup "
                    f"a échoué : {delivery_issue.message} {remediation}\n\n"
                    "--- Rapport de l'agent (non vérifié sur GitHub) ---\n"
                    f"{raw_result[:3000]}"
                )

        _safe_refresh(session, db_entry, "succès")

        db_entry.result = raw_result
        db_entry.status = "success"
        # Plus rien à afficher une fois l'exécution terminée avec succès (voir current_step sur
        # ExecutionHistory) : remis à None plutôt que laissé sur la dernière étape annoncée. Le
        # cas de l'échec (except plus bas) n'a pas besoin du même traitement ici : run_dynamic_crew
        # (crewquestion.py) l'a déjà fait lui-même, dès l'échec, via ce même on_step_change(None) —
        # avant même que cette requête ne sache si retry_on_rate_limit_async va la rejouer ou non.
        db_entry.current_step = None
        db_entry.api_calls_count = run_metrics.api_calls_count
        db_entry.rate_limit_hits = run_metrics.rate_limit_hits
        db_entry.total_wait_time_seconds = run_metrics.total_wait_time
        db_entry.updated_at = datetime.utcnow()
        conversation.updated_at = datetime.utcnow()
        session.add(db_entry)
        session.add(conversation)
        session.commit()
        session.refresh(db_entry)

        return {
            "status": "success",
            "id": db_entry.id,
            "conversation_id": conversation.id,
            "workflow": data.target_workflow,
            "result": raw_result,
            "api_calls_count": run_metrics.api_calls_count,
            "rate_limit_hits": run_metrics.rate_limit_hits,
            "total_wait_time_seconds": run_metrics.total_wait_time,
        }
    except Exception as e:
        _log_memory(f"execution_id={db_entry.id}, exception attrapée")
        print("--- ERREUR CREWAI EXECUTION DETECTEE ---")
        print(traceback.format_exc())

        if isinstance(e, CrewStepError):
            detail = f"Échec à l'étape {e.step_index}/{e.total_steps} ({e.agent_role}) : {e}"
        else:
            detail = str(e)

        # Couvre le cas (rare) où l'échec survient APRÈS un kickoff_async par ailleurs réussi
        # (ex: _format_crew_result/_generate_summary, appelés dans crewquestion.py hors du
        # try/except qui entoure kickoff_async) : run_dynamic_crew n'a alors PAS pu faire son
        # propre nettoyage via on_step_change(None) (voir son except, qui ne couvre que
        # kickoff_async), laissant current_step sur la dernière étape connue malgré status
        # devenant "failed" ici.
        _safe_refresh(session, db_entry, "échec")
        db_entry.current_step = None

        db_entry.status = "failed"
        db_entry.result = detail
        if 'run_metrics' in locals():
            db_entry.api_calls_count = run_metrics.api_calls_count
            db_entry.rate_limit_hits = run_metrics.rate_limit_hits
            db_entry.total_wait_time_seconds = run_metrics.total_wait_time
        db_entry.updated_at = datetime.utcnow()
        conversation.updated_at = datetime.utcnow()
        session.add(db_entry)
        session.add(conversation)
        session.commit()

        # L'id de conversation est transmis même en cas d'échec (en en-tête, le corps
        # d'une HTTPException reste un message texte) pour que le frontend puisse
        # continuer le même fil de discussion plutôt que d'en recréer un nouveau.
        raise HTTPException(
            status_code=500,
            detail=detail,
            headers={"X-Conversation-Id": str(conversation.id)},
        )

@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(
    data: ConversationCreateInput,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Démarre une nouvelle conversation vide."""
    conversation = Conversation(
        user_id=user.get("id"),
        title=(data.title or "Nouvelle conversation").strip()[:200] or "Nouvelle conversation",
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation

@app.get("/api/conversations", response_model=List[Conversation])
async def list_conversations(
    limit: int = 20,
    offset: int = 0,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Conversations de l'utilisateur courant, les plus récemment actives en premier."""
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    statement = (
        select(Conversation)
        .where(Conversation.user_id == user.get("id"))
        .order_by(Conversation.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return session.exec(statement).all()

@app.get("/api/conversations/{conversation_id}/messages", response_model=List[ExecutionHistory])
async def get_conversation_messages(
    conversation_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Messages (échanges qualify+execute) d'une conversation, dans l'ordre chronologique."""
    conversation = session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.get("id"):
        raise HTTPException(status_code=404, detail="Conversation introuvable.")

    statement = (
        select(ExecutionHistory)
        .where(ExecutionHistory.conversation_id == conversation_id)
        .order_by(ExecutionHistory.created_at.asc())
    )
    return session.exec(statement).all()

@app.get("/api/conversations/{conversation_id}/progress")
async def get_conversation_progress(
    conversation_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Sondage léger de la progression pendant qu'une exécution est en cours.

    Ne sélectionne que 3 colonnes de la (au plus une, garanti côté serveur — voir le contrôle
    de concurrence plus haut) exécution "running" de la conversation, plutôt que de réutiliser
    get_conversation_messages : appelé toutes les quelques secondes par le frontend pendant
    toute exécution, il ne doit pas retélécharger à chaque fois l'historique complet de la
    conversation, résultats déjà terminés inclus (potentiellement volumineux sur un workflow
    FEATURE/DESIGN_AND_DEV).
    """
    conversation = session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.get("id"):
        raise HTTPException(status_code=404, detail="Conversation introuvable.")

    statement = (
        select(ExecutionHistory.id, ExecutionHistory.status, ExecutionHistory.current_step)
        .where(ExecutionHistory.conversation_id == conversation_id)
        .where(ExecutionHistory.status == "running")
    )
    row = session.exec(statement).first()
    if row is None:
        return {"id": None, "status": None, "current_step": None}
    return {"id": row[0], "status": row[1], "current_step": row[2]}

@app.get("/api/repo-targets")
async def list_repo_targets(
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Combinaisons owner/repo/branche déjà utilisées par l'utilisateur, les plus récentes en premier."""
    statement = (
        select(ExecutionHistory.repo_owner, ExecutionHistory.repo_name, ExecutionHistory.base_branch)
        .where(ExecutionHistory.user_id == user.get("id"))
        .where(ExecutionHistory.repo_owner.is_not(None))
        .where(ExecutionHistory.repo_name.is_not(None))
        .order_by(ExecutionHistory.created_at.desc())
    )
    rows = session.exec(statement).all()

    seen = set()
    targets = []
    for repo_owner, repo_name, base_branch in rows:
        key = (repo_owner, repo_name, base_branch)
        if key in seen:
            continue
        seen.add(key)
        targets.append({"repo_owner": repo_owner, "repo_name": repo_name, "base_branch": base_branch})
        if len(targets) >= 20:
            break
    return targets

@app.get("/api/history", response_model=List[ExecutionHistory])
async def get_history(
    limit: int = 20,
    offset: int = 0,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Historique des exécutions de l'utilisateur courant, les plus récentes en premier."""
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    statement = (
        select(ExecutionHistory)
        .where(ExecutionHistory.user_id == user.get("id"))
        .order_by(ExecutionHistory.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return session.exec(statement).all()

@app.delete("/api/history/{execution_id}")
async def delete_history_entry(
    execution_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Supprime une exécution de l'historique de l'utilisateur courant."""
    entry = session.get(ExecutionHistory, execution_id)
    if not entry or entry.user_id != user.get("id"):
        raise HTTPException(status_code=404, detail="Exécution introuvable.")

    # Bloqué sur status="running" : rien ici ne permet de distinguer une exécution VRAIMENT
    # bloquée pour toujours (ex: crash/redémarrage du serveur en plein milieu — voir le
    # commentaire sur le contrôle de concurrence de /api/execute plus haut, qui documente cette
    # limite connue) d'une exécution simplement lente mais toujours bien vivante. Autoriser la
    # suppression dans ce second cas romprait le contrôle de concurrence d'/api/execute (qui ne
    # regarde plus que les lignes encore en base pour décider si la conversation est libre) sans
    # rien faire pour arrêter le crew qui tourne encore réellement en tâche de fond : une nouvelle
    # exécution démarrerait alors EN PARALLÈLE de celle "supprimée" sur la même conversation
    # (potentiellement la même work_branch), et le résultat de cette dernière, une fois terminé,
    # ne pourrait plus être persisté (sa ligne n'existe plus) — silencieusement perdu, PR GitHub
    # potentiellement déjà ouverte comprise. Un cas vraiment bloqué reste, lui, un correctif
    # manuel en base (limite acceptée, voir le commentaire cité plus haut).
    if entry.status == "running":
        raise HTTPException(status_code=409, detail="Impossible de supprimer une exécution encore en cours.")

    session.delete(entry)
    session.commit()
    return {"status": "deleted", "id": execution_id}
