"""Suivi de la mémoire du process (RSS) face au plafond du conteneur : repère d'un OOM probable dans les logs."""
import asyncio
from typing import NamedTuple, Optional

from logs import get_logger

log = get_logger("memory_monitor")


def current_memory_mb() -> Optional[float]:
    """RSS (mémoire physique réellement utilisée par ce process) en Mo, lue depuis
    /proc/self/status (Linux uniquement — couvre tout environnement de déploiement réaliste ici :
    Render, Docker...). Best-effort : None si indisponible (OS différent, fichier absent) plutôt
    qu'une exception — un simple diagnostic ne doit jamais faire échouer une exécution par
    ailleurs saine.

    Ajouté pour diagnostiquer les cas où le process backend semble mourir sans laisser aucune
    trace applicative (voir log_memory, appelé à chaque changement d'étape d'exécution) : sur le
    plan gratuit de Render, ni l'onglet "Events" (qui indiquerait un OOM kill explicitement) ni le
    graphique mémoire des "Metrics" ne sont accessibles, cette ligne dans les logs applicatifs est
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

class MemoryLimit(NamedTuple):
    """`mb` : la valeur en Mo. `is_container_limit` : True si lue depuis un cgroup (le plafond
    RÉEL de ce conteneur), False si repli sur /proc/meminfo (MemTotal de la machine HÔTE,
    potentiellement partagée entre plusieurs services — une valeur sans rapport avec ce qui est
    réellement alloué ici, à ne jamais confondre avec un vrai plafond dans les logs)."""
    mb: float
    is_container_limit: bool

def container_memory_limit_mb() -> Optional[MemoryLimit]:
    """Plafond mémoire réellement appliqué à CE conteneur — lu depuis les cgroups Linux, le
    mécanisme que Docker/Render utilisent pour appliquer cette limite. Essaie cgroup v2
    (memory.max) puis v1 (memory.limit_in_bytes) ; ne retombe sur /proc/meminfo (MemTotal, la RAM
    de la machine hôte) que si aucun des deux n'est accessible ou n'indique de limite explicite —
    voir MemoryLimit.is_container_limit, qui distingue ce cas pour que son appelant ne l'affiche
    jamais comme un vrai plafond de service.

    Ne renvoie que des valeurs STRICTEMENT positives (jamais 0 ni négatif) : un cgroup mal
    configuré ou lu en pleine transition d'arrêt du conteneur pourrait théoriquement exposer une
    limite de 0, qui diviserait par zéro chez l'appelant plutôt que de simplement dégrader vers
    "indisponible" comme n'importe quelle autre lecture ratée.

    Calculé une seule fois au démarrage (CONTAINER_MEMORY_LIMIT_MB ci-dessous), jamais à chaque
    appel de log_memory : ce plafond ne peut pas changer pendant la vie du process, inutile de
    rouvrir ces fichiers à chaque changement d'étape d'une exécution.
    """
    try:
        with open("/sys/fs/cgroup/memory.max") as f:
            raw = f.read().strip()
            if raw != "max":
                mb = int(raw) / (1024 * 1024)
                if mb > 0:
                    return MemoryLimit(mb, True)
    except Exception:
        pass
    try:
        with open("/sys/fs/cgroup/memory/memory.limit_in_bytes") as f:
            raw_v1 = int(f.read().strip())
            # cgroup v1 représente "illimité" par une très grande valeur (pas un mot-clé explicite
            # comme "max" en v2) : un seuil large mais arbitraire écarte ce cas plutôt que
            # d'afficher une "limite" de plusieurs exaoctets, dénuée de sens pratique.
            if 0 < raw_v1 < (1 << 62):
                return MemoryLimit(raw_v1 / (1024 * 1024), True)
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mb = int(line.split()[1]) / 1024
                    if mb > 0:
                        return MemoryLimit(mb, False)
    except Exception:
        pass
    return None

# Calculé une seule fois à l'import (voir la docstring de container_memory_limit_mb) : imprimé
# explicitement au démarrage (on_startup plus bas) pour que ce plafond soit visible même sans
# faire défiler les logs jusqu'à une exécution, et réutilisé par chaque ligne [MEM] (log_memory)
# pour situer la RSS courante par rapport à ce plafond sans avoir à les rapprocher manuellement.
CONTAINER_MEMORY_LIMIT_MB = container_memory_limit_mb()

def log_memory(context: str) -> None:
    # Best-effort, y compris l'écriture du log elle-même : appelée depuis des points qui doivent absolument
    # ne jamais lever (le except d'execute_workflow avant que db_entry ne soit marqué "failed", et
    # _persist_current_step avant la classification CrewStepError — voir leurs docstrings). Écrire dans les logs
    # peut échouer (pipe saturé/coupé, disque plein) précisément dans les conditions de pression mémoire que ce
    # diagnostic vise à observer ; sans cette garde, l'échec d'un simple log de diagnostic ferait dérailler
    # l'exécution qu'il essaie seulement d'observer.
    try:
        mem_mb = current_memory_mb()
        if mem_mb is not None:
            if CONTAINER_MEMORY_LIMIT_MB is not None:
                limit = CONTAINER_MEMORY_LIMIT_MB
                pct = 100 * mem_mb / limit.mb
                # "plafond conteneur" seulement si RÉELLEMENT lu depuis un cgroup — sinon
                # "RAM machine hôte, PAS le plafond réel de ce service" : les deux ont des
                # implications opposées (3% d'un plafond conteneur de 512 Mo est alarmant tout
                # près de la limite, 3% d'une RAM hôte de 16 Go ne veut rien dire du tout).
                label = "plafond conteneur" if limit.is_container_limit else "RAM machine hôte, PAS le plafond réel de ce service"
                # flush=True : sys.stdout est bufferisé par bloc (pas par ligne) une fois
                # redirigé vers les logs Render (pas un terminal) — sans vidage explicite, cette
                # ligne pourrait rester en mémoire tampon et disparaître si le process se termine
                # brutalement juste après (OOM kill notamment, qui ne laisse aucune chance de
                # vider ce tampon), précisément la dernière lecture la plus utile à voir.
                log.info(f"[MEM] {context} : {mem_mb:.0f} Mo / {limit.mb:.0f} Mo ({pct:.0f}%) (RSS / {label})")
            else:
                log.info(f"[MEM] {context} : {mem_mb:.0f} Mo (RSS) — plafond du conteneur indisponible")
    except Exception:
        pass

MEMORY_TICK_SECONDS = 2.0

async def periodic_memory_logger(context: str) -> None:
    """Répète log_memory(context) toutes les MEMORY_TICK_SECONDS secondes, indéfiniment, jusqu'à
    ce que cette tâche asyncio soit annulée (task.cancel()) — voir son appelant, qui la lance en
    tâche de fond juste avant un kickoff_async potentiellement long, puis l'annule dès qu'il se
    termine (succès ou échec).

    Une granularité plus fine que les points [MEM] existants (uniquement au démarrage de la
    requête et à chaque CHANGEMENT DE TÂCHE du crew, voir _persist_current_step) est nécessaire
    pour repérer la tendance mémoire PENDANT une tâche unique, pas seulement entre deux tâches :
    le crash observé en pratique (voir PR #36) est survenu en plein streaming de la toute première
    tâche (design_task), avant qu'aucun changement d'étape n'ait eu l'occasion de déclencher la
    moindre ligne [MEM] existante — la dernière lecture disponible (au tout début de la requête)
    était alors déjà bien trop ancienne pour être utile.
    """
    while True:
        log_memory(context)
        await asyncio.sleep(MEMORY_TICK_SECONDS)
