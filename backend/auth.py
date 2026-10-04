import asyncio
import base64
import hashlib
import http.cookiejar
import json
import os
import threading
import time
from typing import Optional

import httpx
from fastapi import Header, HTTPException

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")


def _env_ttl(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, ""))
    except ValueError:
        return default
    return value if value >= 0 else default


# Cache de validation des jetons (AUTH_CACHE_TTL_S, 60 s par défaut ; 0 le désactive). Sans lui, CHAQUE appel de l'API —
# dont le sondage de progression toutes les 3 s — interroge Supabase : latence ajoutée, limites de débit de Supabase, et
# une panne passagère de l'authentification bloque tout. Contrepartie : un jeton révoqué (déconnexion ailleurs) reste
# accepté jusqu'à TTL secondes. Seules les validations RÉUSSIES sont gardées (jamais un 401 ni une panne), la durée est
# bornée par l'expiration lue dans le jeton lui-même, et la clé est un hachage : le jeton n'est jamais conservé en clair.
AUTH_CACHE_TTL_S = _env_ttl("AUTH_CACHE_TTL_S", 60.0)
_AUTH_CACHE_MAX_ENTRIES = 1000
_auth_cache: dict[str, tuple[float, dict]] = {}   # hachage du jeton -> (échéance monotonic, utilisateur)
_auth_cache_lock = threading.Lock()


# Validation en cours par jeton (hachage) : l'issue (utilisateur ou exception) que les requêtes simultanées partagent. Une
# seule boucle d'événements : aucune protection de thread nécessaire.
_inflight: dict[str, asyncio.Future] = {}


def _cache_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _jwt_expiry(token: str) -> Optional[float]:
    """Date d'expiration (epoch, secondes) lue dans la charge du JWT SANS vérifier sa signature : elle ne sert qu'à RACCOURCIR
    la durée de cache, jamais à accepter un jeton (Supabase a déjà validé celui-ci)."""
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        exp = data.get("exp")
        return float(exp) if isinstance(exp, (int, float)) else None
    except Exception:
        return None


def cached_user(token: str, now: Optional[float] = None) -> Optional[dict]:
    now = time.monotonic() if now is None else now
    key = _cache_key(token)
    with _auth_cache_lock:
        entry = _auth_cache.get(key)
        if entry is None:
            return None
        if entry[0] <= now:
            del _auth_cache[key]
            return None
        return dict(entry[1])


def cache_user(token: str, user: dict, now: Optional[float] = None, epoch_now: Optional[float] = None) -> None:
    if AUTH_CACHE_TTL_S <= 0:
        return
    now = time.monotonic() if now is None else now
    ttl = AUTH_CACHE_TTL_S
    expiry = _jwt_expiry(token)
    if expiry is not None:
        ttl = min(ttl, expiry - (time.time() if epoch_now is None else epoch_now))
    if ttl <= 0:
        return
    with _auth_cache_lock:
        if len(_auth_cache) >= _AUTH_CACHE_MAX_ENTRIES:
            for key in [key for key, (deadline, _) in _auth_cache.items() if deadline <= now]:
                del _auth_cache[key]
            if len(_auth_cache) >= _AUTH_CACHE_MAX_ENTRIES:
                _auth_cache.pop(next(iter(_auth_cache)))   # plus ancien d'abord
        _auth_cache[_cache_key(token)] = (now + ttl, dict(user))


def clear_auth_cache() -> None:
    with _auth_cache_lock:
        _auth_cache.clear()


class _RejectAllCookiePolicy(http.cookiejar.DefaultCookiePolicy):
    """Politique de cookies qui refuse TOUT stockage, quel que soit le Set-Cookie reçu."""
    def set_ok(self, cookie, request):
        return False


def _new_http_client() -> httpx.AsyncClient:
    # cookies=<CookieJar dédié, tout refuser> : ce client est PARTAGÉ entre requêtes
    # concurrentes de DIFFÉRENTS utilisateurs (voir plus bas), et httpx stocke un
    # Set-Cookie reçu dans le jar du client dès la réception des en-têtes de réponse, AVANT
    # même que le corps ne soit lu (vérifié dans httpx._client._send_single_request) —
    # c'est-à-dire avant tout point de suspension `await` que ce module contrôle. Une simple
    # purge après coup (`client.cookies.clear()`) laisserait donc une fenêtre : une requête
    # concurrente d'un AUTRE utilisateur, planifiée par l'event loop pendant que la nôtre
    # attend encore la lecture du corps de la réponse, pourrait déjà avoir repris ce cookie
    # avant que la purge ne s'exécute. En refusant le stockage à la source (jamais rien
    # écrit dans le jar), cette fenêtre n'existe plus, que Supabase ou un intermédiaire
    # devant lui (CDN, WAF) envoie un jour un Set-Cookie ou non.
    return httpx.AsyncClient(timeout=10, cookies=http.cookiejar.CookieJar(policy=_RejectAllCookiePolicy()))


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
        _http_client = _new_http_client()
    return _http_client


async def close_http_client() -> None:
    """À appeler à l'arrêt du serveur (voir main.py) pour fermer proprement le pool de
    connexions du client HTTP courant plutôt que de le laisser un file descriptor ouvert."""
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()


def _supabase_unavailable() -> HTTPException:
    # Fonction plutôt que littéral dupliqué aux deux endroits où get_current_user lève cette
    # même erreur (httpx.HTTPError et RuntimeError sur client fermé). Au niveau module comme
    # _new_http_client/_get_http_client : ne capture aucun état, rien ne justifie de la
    # recréer à chaque appel de get_current_user (son chemin le plus chaud).
    return HTTPException(status_code=503, detail="Impossible de vérifier l'authentification (Supabase injoignable).")


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
    user = cached_user(token)
    if user is not None:
        return user
    # Une seule validation à la fois PAR jeton : des requêtes simultanées du même utilisateur (onglets, sondages qui se
    # chevauchent) attendent la première et reçoivent SON issue — succès, mais aussi 401 ou 503, pour qu'une panne de
    # Supabase ne les fasse pas attendre chacune son propre délai l'une après l'autre. Rien n'est gardé après coup, sauf
    # un succès (cache) : la requête suivante revalide. Si le validateur est annulé (client déconnecté), les autres
    # reprennent la main plutôt que d'être annulées avec lui.
    key = _cache_key(token)
    while True:
        shared = _inflight.get(key)
        if shared is None:
            break
        try:
            return dict(await asyncio.shield(shared))
        except asyncio.CancelledError:
            if not shared.cancelled():
                raise   # c'est CETTE requête qui est annulée
    outcome: asyncio.Future = asyncio.get_running_loop().create_future()
    _inflight[key] = outcome
    try:
        user = await _validate_with_supabase(token)
        outcome.set_result(user)
        return user
    except asyncio.CancelledError:
        outcome.cancel()
        raise
    except BaseException as exc:
        outcome.set_exception(exc)
        outcome.exception()   # marquée lue : sans attente, asyncio ne signale pas « exception jamais récupérée »
        raise
    finally:
        if _inflight.get(key) is outcome:
            del _inflight[key]


async def _validate_with_supabase(token: str) -> dict:
    client = _get_http_client()

    try:
        response = await client.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"Authorization": f"Bearer {token}", "apikey": SUPABASE_ANON_KEY},
        )
    except httpx.HTTPError:
        raise _supabase_unavailable()
    except RuntimeError:
        # Ne cible QUE le cas précis d'un client déjà fermé (ex: une requête concurrente
        # arrivée juste après que close_http_client() a fermé ce client, avant que la
        # prochaine n'en obtienne un nouveau via _get_http_client()) — pas n'importe quelle
        # RuntimeError. client.is_closed (propriété publique et stable de httpx) plutôt
        # qu'un test sur le texte exact du message d'erreur : ce dernier est un détail
        # d'implémentation interne de httpx (non documenté comme API publique, httpx est
        # d'ailleurs non épinglé dans requirements.txt) qui pourrait changer de formulation
        # sans avertissement dans une future version. Une RuntimeError sans rapport (client
        # resté ouvert, ex: connexion coupée en cours de route côté transport) doit remonter
        # comme une 500 non gérée et rester visible telle quelle dans les logs, plutôt que
        # d'être maquillée en 503 pointant à tort vers Supabase.
        if not client.is_closed:
            raise
        raise _supabase_unavailable()

    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Session invalide ou expirée.")

    user = response.json()
    if isinstance(user, dict):
        cache_user(token, user)
    return user
