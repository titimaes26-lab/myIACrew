import os
from typing import Optional

import httpx
from fastapi import Header, HTTPException

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

# Client HTTP partagé et réutilisé entre les requêtes plutôt qu'un client neuf ouvert puis
# refermé à CHAQUE requête (comme avant) : get_current_user est la dépendance appelée sur
# (quasi) toutes les routes protégées, donc le chemin le plus chaud de toute l'API.
# httpx.AsyncClient maintient en interne un pool de connexions persistantes (keep-alive)
# vers Supabase, réutilisées entre les appels au lieu de refaire une poignée de main
# TCP/TLS sur chaque requête de cette API.
#
# Accès via _get_http_client() (qui (re)crée le client s'il est absent ou déjà fermé)
# plutôt qu'une simple variable figée à l'import : la création (à l'import du module, une
# seule fois) et la fermeture (sur l'évènement "shutdown" de FastAPI, potentiellement
# déclenché PLUSIEURS FOIS dans le même process, ex: plusieurs `with TestClient(app):`
# dans une suite de tests) ne sont pas liées au même cycle de vie. Sans cette recréation
# paresseuse, un deuxième cycle démarrage/arrêt laisserait _http_client définitivement fermé
# et casserait get_current_user pour le reste du process (RuntimeError sur client fermé,
# vue plus bas, transformée en 503 qui pointerait à tort vers Supabase).
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=10)
    return _http_client


async def close_http_client() -> None:
    """À appeler à l'arrêt du serveur (voir main.py) pour fermer proprement le pool de
    connexions du client HTTP courant plutôt que de le laisser un file descriptor ouvert."""
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()


async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """Vérifie le token Supabase envoyé par le frontend et retourne l'utilisateur authentifié."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentification requise.")

    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise HTTPException(
            status_code=500,
            detail="SUPABASE_URL / SUPABASE_ANON_KEY manquants dans les variables d'environnement du backend.",
        )

    token = authorization.split(" ", 1)[1].strip()
    client = _get_http_client()

    try:
        response = await client.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"Authorization": f"Bearer {token}", "apikey": SUPABASE_ANON_KEY},
        )
    except (httpx.HTTPError, RuntimeError):
        # RuntimeError et non seulement httpx.HTTPError : c'est l'exception (non dérivée de
        # HTTPError) que lève httpx quand une requête concurrente arrive juste après que
        # close_http_client() a fermé ce client (ex: pendant l'arrêt du serveur, avant que
        # la prochaine requête n'en obtienne un nouveau via _get_http_client()) — sans ce
        # cas, elle remonterait comme une 500 non gérée au lieu de la 503 voulue ici.
        raise HTTPException(status_code=503, detail="Impossible de vérifier l'authentification (Supabase injoignable).")
    finally:
        # client est PARTAGÉ entre tous les utilisateurs (voir plus haut, avant c'était un
        # client neuf par requête). Si Supabase, ou un intermédiaire devant lui, renvoyait un
        # jour un Set-Cookie sur cette réponse, httpx le stockerait dans le jar de ce client
        # et le renverrait automatiquement à l'appel suivant — y compris pour un tout autre
        # utilisateur. On vide le jar par précaution après chaque appel pour qu'aucun cookie
        # ne puisse jamais s'accumuler ni fuiter d'un utilisateur à l'autre.
        client.cookies.clear()

    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Session invalide ou expirée.")

    return response.json()
