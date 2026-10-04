"""Garde-fous d'écriture : branche protégée, portée d'écriture d'une exécution, fichiers sensibles, syntaxe invalide."""
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar

from logs import get_logger
from tools import check_syntax_content

log = get_logger("github")

# Toutes les branches de travail créées par ce service (voir main.execute_workflow) commencent par ce préfixe : c'est
# la SEULE zone où les agents ont le droit d'écrire. Ni `main`, ni `test`, ni une branche de production : le nom de
# branche d'un appel d'outil vient du modèle, qu'une consigne glissée dans le dépôt cible pourrait détourner.
WORK_BRANCH_PREFIX = "crewai/"

# Nom admissible : préfixe puis caractères sûrs seulement (lettres, chiffres, « . _ - / »), sans espace ni retour à la
# ligne, sans « // », sans « / » ni « . » final. Les noms réels sont « crewai/<workflow>-<hex8> ».
_WORK_BRANCH_NAME = re.compile(r"crewai/[A-Za-z0-9_](?:(?:[A-Za-z0-9._-]|/(?!/))*[A-Za-z0-9_-])?")

# Branche de travail de l'exécution en cours, quand elle est connue : seule celle-ci est alors écrivable (plus strict
# que le préfixe). Absente d'un thread qui n'a pas hérité du contexte, la règle du préfixe reste en vigueur.
_write_scope: ContextVar[str | None] = ContextVar("write_scope", default=None)

# Signal d'arrêt de l'exécution en cours : posé quand elle dépasse sa durée maximale (voir main.py). Le thread du crew,
# lui, continue de tourner (Python ne sait pas le tuer) : sans ce signal, il pourrait écrire sur GitHub alors que le
# créneau est rendu et qu'une relance écrit sur la même branche.
_write_cancelled: ContextVar[threading.Event | None] = ContextVar("write_cancelled", default=None)

@contextmanager
def track_write_scope(branch: str, cancelled: threading.Event | None = None):
    """Limite les écritures GitHub de l'exécution de crew en cours à `branch` (une branche `crewai/…`) ; une fois
    `cancelled` posé, plus aucune écriture ni ouverture de Pull Request."""
    token = _write_scope.set(branch)
    cancel_token = _write_cancelled.set(cancelled)
    try:
        yield
    finally:
        _write_cancelled.reset(cancel_token)
        _write_scope.reset(token)

def writes_cancelled() -> bool:
    event = _write_cancelled.get()
    return event is not None and event.is_set()

STOPPED_EXECUTION_MESSAGE = (
    "ERREUR : écriture refusée : cette exécution a été arrêtée (durée maximale dépassée). Ne retente rien."
)

def _log_refused_write(branch: object) -> None:
    """Trace d'une écriture refusée : c'est le signe d'un agent qui sort de sa branche de travail (consigne glissée dans
    le dépôt cible ?). Le nom de branche et la branche autorisée seulement, jamais de contenu de fichier ; repr() pour
    qu'un nom piégé (retours à la ligne) ne fabrique pas de fausses lignes de log."""
    log.warning(f"écriture GitHub refusée sur la branche {branch!r} "
        f"(branche autorisée : {_write_scope.get() or WORK_BRANCH_PREFIX + '…'!r}).")

def _log_refused_path(path: object) -> None:
    """Trace d'une écriture refusée sur un fichier sensible (chemin seulement, jamais de contenu ; repr() contre les
    retours à la ligne)."""
    log.warning(f"écriture GitHub refusée sur le fichier sensible {path!r}.")

def _reject_protected_branch(branch: str) -> str | None:
    """None si l'écriture sur `branch` peut continuer, sinon le message d'erreur à renvoyer tel quel (aucun appel réseau)."""
    if writes_cancelled():
        log.warning("écriture GitHub refusée : l'exécution a été arrêtée (durée maximale dépassée).")
        return STOPPED_EXECUTION_MESSAGE
    if branch in ("main", "master"):
        return "ERREUR : écriture directe sur la branche principale interdite. Utilise d'abord github_create_branch."
    valid = isinstance(branch, str) and _WORK_BRANCH_NAME.fullmatch(branch) is not None and ".." not in branch
    if not valid:
        _log_refused_write(branch)
        return (
            f"ERREUR : écriture refusée sur la branche '{branch}' : seules les branches de travail "
            f"'{WORK_BRANCH_PREFIX}…' sont modifiables. Utilise la branche de travail indiquée dans le contexte repository."
        )
    scope = _write_scope.get()
    if scope is not None and branch != scope:
        _log_refused_write(branch)
        return (
            f"ERREUR : écriture refusée sur la branche '{branch}' : cette exécution ne peut écrire que sur sa branche "
            f"de travail '{scope}'."
        )
    return None

# Fichiers qu'un agent ne doit JAMAIS écrire : un workflow de CI, un fichier d'environnement ou un descripteur de
# déploiement committé sur la branche de travail peut s'exécuter avec les secrets du dépôt dès le push, avant toute
# relecture de la Pull Request (consigne glissée dans le dépôt cible comprise). Comparaison insensible à la casse, sur le
# chemin normalisé (« ./ », « / » initial, « \\ »).
SENSITIVE_DIRECTORIES = frozenset({".github", ".git", ".circleci", ".husky", ".gitlab"})

SENSITIVE_FILENAMES = frozenset({
    ".gitlab-ci.yml", ".travis.yml", "jenkinsfile", "azure-pipelines.yml", "bitbucket-pipelines.yml",
    "cloudbuild.yaml", "cloudbuild.yml", "dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "vercel.json", "render.yaml", "render.yml", "netlify.toml", "fly.toml", "procfile", ".npmrc",
    ".pre-commit-config.yaml", ".gitmodules", ".gitattributes",
})

# Modèles d'environnement sans secret : seuls fichiers « .env* » qu'un agent peut écrire.
SENSITIVE_ENV_TEMPLATES = frozenset({".env.example", ".env.sample", ".env.template"})

def _is_sensitive_filename(name: str) -> bool:
    """`name` en minuscules. Nom entier comparé (jamais une simple sous-chaîne : `docker-utils.ts`,
    `Dockerfile-notes.md` ne sont pas des fichiers de déploiement)."""
    if name in SENSITIVE_FILENAMES or name in SENSITIVE_DIRECTORIES:
        return True
    if name.startswith(".env"):
        return name not in SENSITIVE_ENV_TEMPLATES
    return name.startswith(("dockerfile.", "docker-compose.")) or name.endswith(".dockerfile")

def _reject_sensitive_path(path: str) -> str | None:
    """None si `path` peut être écrit, sinon le message d'erreur à renvoyer tel quel (aucun appel réseau)."""
    parts = [part for part in str(path).replace("\\", "/").split("/") if part not in ("", ".")]
    lowered = [part.lower() for part in parts]
    sensitive = (
        not parts
        or ".." in parts
        or any(part in SENSITIVE_DIRECTORIES for part in lowered[:-1])
        or _is_sensitive_filename(lowered[-1])
    )
    if not sensitive:
        return None
    _log_refused_path(path)
    return (
        f"ERREUR : écriture refusée sur '{path}' : les fichiers de CI, d'environnement et de déploiement "
        "(.github/, .env*, Dockerfile, vercel.json…) ne sont jamais modifiés par un agent. Ne retente pas : "
        "signale ce fichier comme non livré dans ton rapport final, avec cette raison."
    )

def _reject_invalid_syntax(path: str, content: str) -> str | None:
    """None si le contenu passe la vérification EXACTE de check_syntax_content (Python/JSON/YAML)
    ou si son extension n'y est pas soumise, sinon le message ERREUR_SYNTAXE qu'elle renvoie, à
    faire remonter tel quel à l'agent appelant.

    Garde-fou avant commit (voir github_write_file/github_write_files) contre une troncature
    silencieuse du contenu produit par diagnostic_task (voir tasksquestion.yaml) : ni
    developer_agent (qui committe ce contenu) ni ses outils d'écriture n'ont normalement de
    moyen de détecter qu'un fichier a été coupé en cours de génération — ce filet le fait à leur
    place, sans coût sur le budget max_iter de l'agent (appel Python interne, pas un outil
    CrewAI invoqué séparément). Complémentaire à la consigne de prompt qui demande déjà à
    diagnostic_task de ne pas soumettre un fichier qu'elle craint de tronquer, pas un
    remplacement : les deux peuvent laisser passer des cas que l'autre aurait rattrapés.

    Volontairement limité à ERREUR_SYNTAXE (Python/JSON/YAML, vrai parseur exact) : la sortie
    PROBLÈME(S) DÉTECTÉ(S) (heuristique JS/TS/JSX/TSX de check_syntax_content) a un faux positif
    connu sur toute apostrophe française en texte JSX hors commentaire (ex: "n'y", très fréquent
    dans cette app en français — voir _check_balanced_delimiters, tools.py) : bloquer un commit
    dessus rejetterait EN PERMANENCE des fichiers .tsx/.jsx par ailleurs valides, sans recours
    possible pour developer_agent (aucun outil de lecture pour corriger ni retenter). check_syntax
    reste disponible en usage manuel par l'agent (voir development_task, tasksquestion.yaml) pour
    ces extensions, juste plus en verrou automatique ici.
    """
    try:
        result = check_syntax_content(content, path)
    except Exception as e:
        # Défensif seulement : check_syntax_content ne lève normalement jamais elle-même (toutes
        # ses branches sont déjà protégées par try/except et renvoient une chaîne). Si cet appel
        # échoue quand même, ne pas bloquer un commit par ailleurs valide — ce garde-fou est un
        # filet SUPPLÉMENTAIRE, pas la seule protection contre une troncature (voir plus haut).
        # print visible (pas juste avalé) : cette hypothèse pourrait un jour être invalidée par
        # un futur changement de tools.py, et ce cas mérite d'être investigué même s'il ne
        # bloque pas le commit.
        log.warning(f"check_syntax_content a levé une exception inattendue pour '{path}' "
            f"({type(e).__name__}: {e}) — commit non bloqué (garde-fou best-effort), mais ce cas "
            "devrait être investigué : voir _reject_invalid_syntax.")
        return None
    if result.startswith("ERREUR_SYNTAXE"):
        return result
    return None
