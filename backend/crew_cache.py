"""Purge du cache de mémoïsation de CrewAI à la fin d'une exécution."""
from typing import Any

from crewai.project.utils import cache as _crewai_memoize_cache

from logs import get_logger

log = get_logger("crew")


def evict_memoized_cache_entries(crew_instance: Any) -> None:
    """Purge, best-effort, les entrées de crewai.project.utils.cache (voir son import plus haut)
    associées à `crew_instance`, une fois son exécution terminée.

    Les méthodes décorées @task/@agent de AppDevelopmentCrew sont mémoïsées par CrewAI dans ce
    dict module-level, clé par (nom de méthode, id(self)) — voir le commentaire sur
    crew_for_this_execution dans main.py, qui explique pourquoi chaque exécution instancie un
    AppDevelopmentCrew() DÉDIÉ (donc un id(self) distinct) plutôt que de réutiliser un singleton.
    Ce cache n'offre aucune API d'éviction publique et n'est JAMAIS purgé de lui-même : sans cet
    appel, chaque exécution y laisse une poignée d'entrées orphelines pour toujours, même une fois
    `crew_instance` elle-même devenue inaccessible et éligible au garbage collection — une fuite
    mémoire lente mais réelle, jusqu'ici seulement "acceptée" (voir ce même commentaire dans
    main.py, qui suggérait un redémarrage périodique du service comme seul filet de sécurité).
    Significatif sur un service à mémoire limitée (ex: plan gratuit Render, souvent 512 Mo)
    recevant de nombreuses exécutions sans redémarrage entretemps.

    Reste dans les entrailles PRIVÉES de crewai (cache._cache, un PrivateAttr Pydantic, structure
    non garantie stable d'une version à l'autre — requirements.txt n'épingle pas crewai) faute de
    mieux : best-effort et strictement défensif, une évolution future de cette structure interne
    ne doit jamais faire échouer une exécution par ailleurs saine, juste laisser le cache grossir
    comme avant ce correctif (dégradation silencieuse, pas une régression).

    Passe par cache._lock (le même RWLock que CacheHandler.add()/read() utilisent pour CE MÊME
    dict _cache) plutôt que d'y toucher directement : deux conversations DIFFÉRENTES peuvent
    s'exécuter concurremment (voir main.py, contrôle de concurrence limité à UNE conversation à la
    fois), donc la construction d'un AppDevelopmentCrew() pour l'une (qui appelle cache.add() sous
    ce verrou) peut survenir pendant que cette fonction itère ici sur _cache pour une autre — sans
    le même verrou, ce serait un dict modifié pendant son itération (RuntimeError), rattrapé par
    le except ci-dessous mais qui ferait échouer silencieusement la purge de cette exécution-là.
    """
    try:
        with _crewai_memoize_cache._lock.w_locked():
            internal_cache = _crewai_memoize_cache._cache
            # Repère la clé "__instance__" que _make_hashable (crewai/project/utils.py) produit
            # pour `self` : `("__instance__", id(self))`, dont str() rend exactement ce fragment —
            # présent tel quel dans la clé finale de cache quelle que soit la méthode mémoïsée.
            marker = f"'__instance__', {id(crew_instance)})"
            stale_keys = [k for k in internal_cache if marker in k]
            for k in stale_keys:
                del internal_cache[k]
    except Exception as e:
        # flush=True : sys.stdout est bufferisé par bloc une fois redirigé vers les logs Render
        # (pas un terminal) — voir main.py, _log_memory, qui applique la même garde partout pour
        # ne pas perdre le dernier diagnostic si le process se termine brutalement juste après.
        log.warning(f"échec du nettoyage du cache de mémoïsation CrewAI (best-effort, sans impact) : {type(e).__name__}: {e}")
