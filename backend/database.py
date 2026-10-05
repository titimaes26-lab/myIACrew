import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel, create_engine, Session

from logs import get_logger

log = get_logger("database")

# Récupération de l'URL depuis les variables d'environnement
_RAW_DATABASE_URL = os.getenv("DATABASE_URL")
DATABASE_URL = _RAW_DATABASE_URL or "sqlite:///./dev.db"

# Ajustement pour PostgreSQL sur Render/Supabase si besoin
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# echo=True loggue l'intégralité de chaque requête SQL exécutée (utile en debug local,
# mais un coût I/O systématique en production sur CHAQUE écriture d'ExecutionHistory et
# lecture d'historique/conversation) : désactivé par défaut, réactivable via SQL_ECHO=true
# pour le débogage sans devoir modifier le code.
_SQL_ECHO = os.getenv("SQL_ECHO", "false").strip().lower() == "true"
# pool_pre_ping : une connexion coupée par le pooler (Supabase, Render) pendant qu'elle dormait dans le pool est
# détectée avant usage et remplacée, au lieu de faire échouer la requête suivante. pool_recycle : aucune connexion
# n'est gardée plus de 30 minutes.
def _env_int(name: str, default: int, minimum: int) -> int:
    """Entier lu dans l'environnement ; valeur absente, illisible ou sous le minimum : le défaut."""
    try:
        value = int(os.getenv(name, ""))
    except ValueError:
        return default
    return value if value >= minimum else default


def engine_options(url: str) -> dict:
    """Options du moteur. Les points d'accès de base de données tournent en parallèle dans des threads, les exécutions en
    tâche de fond ont leurs propres sessions : la réserve de connexions est réglable (DB_POOL_SIZE, DB_MAX_OVERFLOW,
    DB_POOL_TIMEOUT) plutôt que les 5 + 10 par défaut. Hors SQLite (dont le pool mémoire n'accepte pas ces options)."""
    options: dict = {"echo": _SQL_ECHO, "pool_pre_ping": True, "pool_recycle": 1800}
    if not url.startswith("sqlite"):
        options.update(
            pool_size=_env_int("DB_POOL_SIZE", 10, 1),
            max_overflow=_env_int("DB_MAX_OVERFLOW", 10, 0),
            pool_timeout=_env_int("DB_POOL_TIMEOUT", 30, 1),
        )
    return options


engine = create_engine(DATABASE_URL, **engine_options(DATABASE_URL))

# Regroupe plusieurs exécutions en un fil de discussion persistant
class Conversation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[str] = Field(default=None, index=True)  # id Supabase (auth.users) de l'auteur
    title: str = "Nouvelle conversation"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Table pour sauvegarder les demandes et rapports CrewAI (un "message" du fil)
# Une seule exécution « running » par conversation, garanti par la base : le contrôle applicatif (lire les exécutions en
# cours puis insérer) n'est pas atomique, deux requêtes simultanées (double clic, deux onglets) le passeraient toutes
# les deux. Index unique PARTIEL (Postgres et SQLite) ; le même est créé sur une base existante par la migration
# ci-dessous (create_all ne crée pas les index d'une table déjà présente).
ONE_RUNNING_PER_CONVERSATION_INDEX = "uq_executionhistory_one_running_per_conversation"
ONE_RUNNING_PER_CONVERSATION_WHERE = "status = 'running'"


class ExecutionHistory(SQLModel, table=True):
    __table_args__ = (
        Index(
            ONE_RUNNING_PER_CONVERSATION_INDEX, "conversation_id", unique=True,
            postgresql_where=text(ONE_RUNNING_PER_CONVERSATION_WHERE),
            sqlite_where=text(ONE_RUNNING_PER_CONVERSATION_WHERE),
        ),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    user_request: str
    workflow: str
    clarifications: Optional[str] = None
    result: Optional[str] = None
    status: str = Field(default="running")  # running | success | failed
    # Clé de l'étape CrewAI actuellement en cours (ex: 'design', 'architecture', 'development',
    # 'qa' — voir WORKFLOW_STEPS côté frontend), mise à jour par on_step_change pendant
    # l'exécution (voir crew_run.CrewRun et execution_state.persist_current_step). Persisté en base (pas juste
    # gardé en mémoire) pour que la progression survive à un rechargement de page ou à un
    # redémarrage du serveur pendant qu'une exécution est en cours. Remis à None dès que status
    # quitte "running" (succès OU échec) : plus rien n'est alors réellement en cours, y compris
    # pendant une pause avant une nouvelle tentative sur erreur de quota (retry_on_rate_limit_async
    # peut attendre plusieurs minutes) — le message "Échec à l'étape X/Y" de CrewStepError couvre
    # déjà, plus précisément, le besoin diagnostique de savoir où une exécution s'est arrêtée.
    current_step: Optional[str] = Field(default=None)
    user_id: Optional[str] = Field(default=None, index=True)  # id Supabase (auth.users) de l'auteur
    conversation_id: Optional[int] = Field(default=None, index=True)
    repo_owner: Optional[str] = None
    repo_name: Optional[str] = None
    base_branch: Optional[str] = None
    work_branch: Optional[str] = None
    # Coût/performance de cette exécution (voir metrics_collect.track_execution_metrics).
    api_calls_count: Optional[int] = None
    rate_limit_hits: Optional[int] = None
    total_wait_time_seconds: Optional[float] = None
    # Cause d'un échec (voir errors.classify_exception) : renseignés uniquement quand status="failed".
    # error_code = code stable (QUOTA_EXHAUSTED, GUARDRAIL_FAILED, INTERNAL_ERROR...), error_retryable =
    # « réessayer tel quel a une chance de réussir ». L'étape en échec est dans `result` (« Échec à l'étape N/M »).
    error_code: Optional[str] = None
    error_retryable: Optional[bool] = None
    # Raisonnement de l'exécution : nombre de tentatives (2 après une relance automatique), étapes reprises d'une
    # exécution précédente, et dernier verdict QA du résultat (GO | GO_AVEC_RESERVES | NO_GO) pour suivre la qualité.
    # Taille de qualification d'une FEATURE (PETIT : étape d'architecture sautée) ; None pour tout autre workflow.
    scope: Optional[str] = None
    attempts: Optional[int] = None
    reused_steps: Optional[int] = None
    qa_verdict: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Performance d'UN agent pour UNE exécution (voir metrics_collect.build_agent_run_rows). Table NEUVE :
# create_all la crée sur une base existante, aucune migration ALTER n'est nécessaire.
class AgentRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    execution_id: int = Field(index=True)
    conversation_id: Optional[int] = Field(default=None, index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    workflow: str = ""
    agent: str  # clé d'étape : design | architecture | diagnostic | development | qa | system
    status: str = "completed"  # completed | incomplete | n/a
    duration_seconds: Optional[float] = None
    llm_calls: int = 0
    llm_errors: int = 0
    usage_calls: int = 0  # appels LLM dont l'usage de tokens est connu (moyennes de tokens)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# Journal des lancements d'exécution, pour le plafond horaire par utilisateur (voir limits.py). Table à part : un
# utilisateur peut supprimer son historique, ce qui ne doit pas réinitialiser son quota. Purgé au fil de l'eau (2 h).
# Table NEUVE : create_all la crée sur une base existante.
class ExecutionLaunch(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)

# Sortie d'une étape TERMINÉE (design, architecture, diagnostic) d'une exécution : permet de reprendre
# une exécution en échec à l'étape qui a échoué, sans repayer les étapes déjà réussies (voir
# crewquestion.resumable_prefix). Table NEUVE : create_all la crée sur une base existante.
class ExecutionCheckpoint(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    execution_id: int = Field(index=True)
    step: str  # clé d'étape : design | architecture | diagnostic
    raw: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# SQLModel.metadata.create_all() ne modifie jamais le schéma d'une table déjà existante.
# Ces instructions rattrapent les colonnes ajoutées après la création initiale de la table
# sur une base déjà en place (ex: Supabase en production). Sans effet sur une base neuve
# (déjà créée avec le bon schéma) ou déjà à jour.
_MIGRATION_STATEMENTS = [
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS status VARCHAR NOT NULL DEFAULT 'success'",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT now()",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT now()",
    "ALTER TABLE executionhistory ALTER COLUMN result DROP NOT NULL",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS user_id VARCHAR",
    "CREATE INDEX IF NOT EXISTS ix_executionhistory_user_id ON executionhistory (user_id)",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS repo_owner VARCHAR",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS repo_name VARCHAR",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS base_branch VARCHAR",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS work_branch VARCHAR",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS conversation_id INTEGER",
    "CREATE INDEX IF NOT EXISTS ix_executionhistory_conversation_id ON executionhistory (conversation_id)",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS api_calls_count INTEGER",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS rate_limit_hits INTEGER",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS total_wait_time_seconds FLOAT",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS current_step VARCHAR",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS error_code VARCHAR",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS error_retryable BOOLEAN",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS scope VARCHAR",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS attempts INTEGER",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS reused_steps INTEGER",
    "ALTER TABLE executionhistory ADD COLUMN IF NOT EXISTS qa_verdict VARCHAR",
    # Échoue (journalisé ci-dessous, sans conséquence) si une conversation a déjà plusieurs lignes « running » :
    # le balayage des orphelines les libère, la migration repasse au prochain démarrage.
    f"CREATE UNIQUE INDEX IF NOT EXISTS {ONE_RUNNING_PER_CONVERSATION_INDEX} "
    f"ON executionhistory (conversation_id) WHERE {ONE_RUNNING_PER_CONVERSATION_WHERE}",
]

def _run_lightweight_migrations():
    for statement in _MIGRATION_STATEMENTS:
        try:
            with engine.begin() as conn:
                conn.execute(text(statement))
        except Exception as e:
            # Attendu si la colonne existe déjà ou si le SGBD ne supporte pas la syntaxe
            # (ex: ALTER COLUMN ... DROP NOT NULL sous SQLite). Toute autre cause (droits,
            # erreur de connexion, etc.) doit rester visible dans les logs de démarrage.
            log.warning(f"migration ignorée : {statement!r} -> {type(e).__name__}: {e}")

def create_db_and_tables():
    if _RAW_DATABASE_URL is None:
        log.warning(
            "DATABASE_URL n'est pas définie — le backend utilise une base "
            "SQLite locale et éphémère (sqlite:///./dev.db), PAS Supabase. Toutes les "
            "données seront perdues au prochain redémarrage/redéploiement. Configure "
            "DATABASE_URL avec la chaîne de connexion Postgres de Supabase."
        )
    else:
        log.info(f"connexion à la base de données : {engine.url.render_as_string(hide_password=True)}")
    SQLModel.metadata.create_all(engine)
    _run_lightweight_migrations()

def get_session():
    with Session(engine) as session:
        yield session
