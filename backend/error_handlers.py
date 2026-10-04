"""Format d'erreur unique de l'API et filet de sécurité global (handlers d'exceptions), plus la politique CORS."""
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

import memory_monitor
from errors import AppError, ErrorCode, code_for_status, error_body, is_retryable_status
from logs import get_logger

log = get_logger("error_handlers")

# 3. CONFIGURATION CORS
# Constantes (pas juste inline dans add_middleware) : relues par _log_unhandled_exception plus
# bas, qui doit reproduire la même décision d'autorisation d'origine/identifiants que
# CORSMiddleware — une seule source de vérité, pour qu'un futur changement de l'une de ces deux
# valeurs ne puisse pas être oublié dans l'un des deux endroits qui en dépendent.
# 3. CONFIGURATION CORS
# Constantes (pas juste inline dans add_middleware) : relues par _log_unhandled_exception plus
# bas, qui doit reproduire la même décision d'autorisation d'origine/identifiants que
# CORSMiddleware — une seule source de vérité, pour qu'un futur changement de l'une de ces deux
# valeurs ne puisse pas être oublié dans l'un des deux endroits qui en dépendent.
CORS_ALLOW_ORIGINS = ["*"]
CORS_ALLOW_CREDENTIALS = True


def register_error_handlers(app: FastAPI) -> None:
    """Enregistre les quatre handlers d'exceptions de l'application."""
    # Format d'erreur UNIQUE de l'API (voir errors.error_body) : {"detail", "code", "retryable"}.
    # `detail` reste la clé historique lue par le frontend ; `code` permet d'agir selon la cause.
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.message, exc.code, exc.retryable),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Couvre aussi les HTTPException levées par FastAPI/Starlette elles-mêmes (404 de route,
        # 405...) et par auth.py : même format partout, en-têtes d'origine conservés (ex: WWW-Authenticate).
        structured: dict[str, Any] = {} if isinstance(exc.detail, str) else {"errors": exc.detail}
        message = exc.detail if isinstance(exc.detail, str) else "Requête refusée."
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(message, code_for_status(exc.status_code), is_retryable_status(exc.status_code), **structured),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI renvoie par défaut `detail` = liste d'objets : illisible tel quel dans l'interface
        # (qui attend une chaîne). On le ramène à une phrase, la liste détaillée restant dans `errors`.
        def _readable(message: str) -> str:
            return message.removeprefix("Value error, ")

        problems = [
            {"field": ".".join(str(part) for part in err.get("loc", ()) if part != "body"), "message": _readable(str(err.get("msg", "")))}
            for err in exc.errors()
        ]
        summary = "; ".join(f"{p['field']} : {p['message']}" if p["field"] else p["message"] for p in problems[:3])
        return JSONResponse(
            status_code=422,
            content=error_body(f"Requête invalide — {summary}" if summary else "Requête invalide.", ErrorCode.VALIDATION_ERROR, errors=problems),
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
        # Ce diagnostic (journalisation/lecture mémoire) ne doit JAMAIS empêcher de renvoyer une réponse :
        # écrire dans les logs peut lui-même échouer (pipe saturé/coupé, disque plein — plausible pile
        # dans les conditions de pression mémoire que ce diagnostic vise à détecter). Une exception
        # ICI, dans ce handler global lui-même, ne serait rattrapée par personne (ServerErrorMiddleware
        # ne retombe sur son propre filet de sécurité QUE quand aucun handler custom n'est enregistré,
        # ce qui n'est plus le cas dès qu'on en définit un) : la connexion serait alors abandonnée
        # sans aucune réponse, exactement le symptôme opaque ("Failed to fetch") que ce handler existe
        # pour éliminer.
        try:
            memory_monitor.log_memory(f"exception non gérée sur {request.url.path}")
            # Le handler de logs (logs.py) vide la sortie à chaque ligne : la trace n'attend pas en mémoire tampon
            # si le process est tué juste après (OOM kill, SIGKILL), exactement le cas que ce handler veut couvrir.
            log.error(f"exception non gérée sur {request.method} {request.url.path}", exc_info=exc)
        except Exception:
            pass

        # Message générique (pas str(exc)) : contrairement aux deux endpoints qui choisissent
        # délibérément d'exposer le détail d'une erreur CrewAI connue, ce filet attrape N'IMPORTE
        # QUELLE exception non anticipée sur N'IMPORTE QUEL endpoint — le détail réel reste
        # disponible dans les logs Render (print ci-dessus), jamais renvoyé tel quel au client.
        response = JSONResponse(
            status_code=500,
            content=error_body("Erreur interne du serveur.", ErrorCode.INTERNAL_ERROR),
        )

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
