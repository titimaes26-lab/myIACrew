import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import update as sql_update
from sqlmodel import Session, col, select

import database
from database import (
    create_db_and_tables, ExecutionHistory,
)
from auth import close_http_client
from orphans import sweep_stale_executions
from logs import get_logger
from qa_report import final_verdict
import execution_state
import memory_monitor
import routes_conversations
import routes_execute
import routes_history
from routes_metrics import router as metrics_router
from error_handlers import CORS_ALLOW_CREDENTIALS, CORS_ALLOW_ORIGINS, register_error_handlers

log = get_logger("main")

# Instanciation de FastAPI (avant tout décorateur d'événement).
app = FastAPI(title="CrewAI App Development API")


# Délai (best-effort, voir on_shutdown) accordé aux exécutions de crew encore en tâche de fond
# pour se terminer avant que le process ne s'arrête. Constante de module, pas locale à on_shutdown, pour rester
# visible/réutilisable sans dupliquer sa valeur (ex: un futur endpoint de santé qui voudrait l'exposer).
_SHUTDOWN_DRAIN_TIMEOUT_S = 20

_BACKFILL_BATCH = 200

def _backfill_qa_verdicts() -> None:
    """Rattrape `qa_verdict` des exécutions réussies d'avant la colonne, depuis le texte de leur résultat, pour que
    la qualité dans le temps couvre l'historique. Une exécution sans verdict (aucun QA) reste à NULL et est
    relue à chaque démarrage : le coût est borné par lots. Best-effort."""
    try:
        with Session(database.engine) as backfill:
            last_id = 0
            while True:
                rows = backfill.exec(
                    select(ExecutionHistory.id, ExecutionHistory.result)
                    .where(ExecutionHistory.status == "success")
                    .where(col(ExecutionHistory.qa_verdict).is_(None))
                    .where(ExecutionHistory.id > last_id)
                    .order_by(col(ExecutionHistory.id))
                    .limit(_BACKFILL_BATCH)
                ).all()
                if not rows:
                    break
                last_id = rows[-1][0] or last_id
                for execution_id, result in rows:
                    verdict = final_verdict(result or "")
                    if verdict:
                        backfill.exec(
                            sql_update(ExecutionHistory).where(ExecutionHistory.id == execution_id).values(qa_verdict=verdict)
                        )
                backfill.commit()
    except Exception as e:
        log.warning(f"rattrapage des verdicts QA ignoré : {type(e).__name__}: {e}")

# Démarrage : création des tables, rattrapage des verdicts QA, balayage des exécutions orphelines.
@app.on_event("startup")
def on_startup():
    create_db_and_tables()
    _backfill_qa_verdicts()
    # Imprimé une seule fois, au démarrage : rend le plafond mémoire du conteneur visible dans les
    # logs Render dès le boot, sans attendre qu'une exécution déclenche la première ligne [MEM]
    # (memory_monitor.log_memory) — utile pour juger d'emblée si le plan actuel a une marge suffisante pour ce
    # type de charge (CrewAI + plusieurs agents Gemini), avant même de lancer quoi que ce soit.
    memory_monitor.log_memory("démarrage du service")
    # Libère les conversations bloquées par une exécution morte avec l'ancien process (crash, OOM,
    # redéploiement). Best-effort : ne doit jamais empêcher le service de démarrer.
    try:
        with Session(database.engine) as session:
            swept = sweep_stale_executions(session, active_ids=execution_state.active_execution_ids)
        if swept:
            log.info(f"Démarrage : {len(swept)} exécution(s) orpheline(s) marquée(s) interrompue(s) : {swept}")
    except Exception as e:
        log.warning(f"balayage des exécutions orphelines impossible : {type(e).__name__}: {e}")

# Ferme proprement le client HTTP partagé de auth.py (voir sa docstring) plutôt que de
# laisser ses connexions ouvertes à l'arrêt du process.
@app.on_event("shutdown")
async def on_shutdown():
    # Best-effort, PAS une garantie : une exécution de crew tourne désormais en tâche de fond
    # asyncio (execution_state.background_tasks), invisible pour le mécanisme de "graceful shutdown" d'Uvicorn —
    # celui-ci ne suit et n'attend que les requêtes HTTP en vol, jamais des Task créées par
    # l'application elle-même. Sans cette attente explicite ici, un redéploiement Render (SIGTERM)
    # pendant une exécution en cours laisserait sa ligne bloquée sur "running" pour toujours (voir
    # la limite déjà documentée dans routes_execute.execute_workflow). _SHUTDOWN_DRAIN_TIMEOUT_S est un compromis
    # : le délai réel accordé par Render entre SIGTERM et un SIGKILL forcé n'est pas connu
    # précisément d'ici, et une exécution DESIGN_AND_DEV/FEATURE peut de toute façon durer
    # plusieurs minutes — largement au-delà de ce que quelque délai raisonnable que ce soit ici
    # pourrait couvrir. Chaque seconde accordée reste néanmoins strictement plus utile que zéro :
    # une exécution sur le point de se terminer a ainsi une vraie chance de persister son résultat
    # avant l'arrêt, plutôt qu'aucune.
    if execution_state.background_tasks:
        log.info(f"Arrêt du service : attente (best-effort, jusqu'à {_SHUTDOWN_DRAIN_TIMEOUT_S}s) de "
            f"{len(execution_state.background_tasks)} exécution(s) de crew encore en tâche de fond...")
        await asyncio.wait(list(execution_state.background_tasks), timeout=_SHUTDOWN_DRAIN_TIMEOUT_S)
    await close_http_client()


# CORS (politique : voir error_handlers.py) puis format d'erreur unique de l'API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_error_handlers(app)
app.include_router(metrics_router)
app.include_router(routes_history.router)
app.include_router(routes_conversations.router)
app.include_router(routes_execute.router)


@app.get("/")
def read_root():
    return {"status": "API CrewAI opérationnelle"}


