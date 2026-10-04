"""Client GitHub partagé : création du client (jeton d'environnement) et cache de lecture d'une exécution de crew."""
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable

from github import Auth, Github, GithubException

from logs import get_logger

log = get_logger("github")

# Cache de LECTURE d'une exécution de crew (voir track_read_cache) : les agents (Designer, Architecte, Diagnostic)
# relisent les mêmes fichiers (package.json, la racine, src), et chaque lecture coûtait deux appels API (get_repo
# puis get_contents) avec un client recréé à chaque fois. ContextVar plutôt que global : il vit le temps d'UNE
# exécution (pas de fuite entre utilisateurs, une relance repart de zéro), et hors de ce contexte le comportement
# est inchangé. Les vérifications de livraison (make_file_fetcher, make_dir_lister) ne l'utilisent JAMAIS : elles
# doivent voir l'état réel de GitHub.
_read_cache: ContextVar[dict | None] = ContextVar("read_cache", default=None)

_read_cache_lock = threading.Lock()

@contextmanager
def track_read_cache():
    """Active le cache de lecture pour l'exécution de crew en cours (à entourer le prefetch ET le crew)."""
    cache: dict = {"repos": {}, "files": {}, "dirs": {}, "stats": {"hits": 0, "reads": 0}}
    token = _read_cache.set(cache)
    try:
        yield
    finally:
        _read_cache.reset(token)
        stats = cache["stats"]
        if stats["hits"] + stats["reads"]:
            # Rend le gain du cache lisible dans les logs (et prouve qu'il est actif dans les threads d'outils).
            log.info(f"[CACHE LECTURE] hits={stats['hits']} lectures={stats['reads']}")

def invalidate_read_cache(owner: str, repo: str, branch: str) -> None:
    """Oublie les lectures d'une branche après une écriture : les agents relisent alors l'état à jour."""
    cache = _read_cache.get()
    if cache is None:
        return
    with _read_cache_lock:
        for name in ("files", "dirs"):
            for key in [k for k in cache[name] if k[:3] == (owner, repo, branch)]:
                del cache[name][key]

def _cached_read(kind: str, key: tuple, read: Callable[[], str]) -> str:
    """Résultat mémorisé de `read()` ; une ERREUR (texte « ERREUR… ») n'est jamais mémorisée : une branche ou un
    fichier peut apparaître entre deux lectures."""
    cache = _read_cache.get()
    if cache is None:
        return read()
    with _read_cache_lock:
        if key in cache[kind]:
            cache["stats"]["hits"] += 1
            return cache[kind][key]
    value = read()
    with _read_cache_lock:
        cache["stats"]["reads"] += 1
        if not value.startswith("ERREUR"):
            cache[kind][key] = value
    return value

def _get_repo(owner: str, repo: str):
    cache = _read_cache.get()
    if cache is not None:
        with _read_cache_lock:
            cached = cache["repos"].get((owner, repo))
        if cached is not None:
            return cached
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN manquant dans les variables d'environnement du backend.")
    client = Github(auth=Auth.Token(token))
    gh_repo = client.get_repo(f"{owner}/{repo}")
    if cache is not None:
        with _read_cache_lock:
            cache["repos"][(owner, repo)] = gh_repo
    return gh_repo

def _github_error(e: GithubException) -> str:
    message = e.data.get("message", str(e)) if isinstance(e.data, dict) else str(e)
    return f"ERREUR_GITHUB : {message}"
