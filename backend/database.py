import os
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlmodel import Field, SQLModel, create_engine, Session

# Récupération de l'URL depuis les variables d'environnement
_RAW_DATABASE_URL = os.getenv("DATABASE_URL")
DATABASE_URL = _RAW_DATABASE_URL or "sqlite:///./dev.db"

# Ajustement pour PostgreSQL sur Render/Supabase si besoin
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, echo=True)

# Regroupe plusieurs exécutions en un fil de discussion persistant
class Conversation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[str] = Field(default=None, index=True)  # id Supabase (auth.users) de l'auteur
    title: str = "Nouvelle conversation"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

# Table pour sauvegarder les demandes et rapports CrewAI (un "message" du fil)
class ExecutionHistory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_request: str
    workflow: str
    clarifications: Optional[str] = None
    result: Optional[str] = None
    status: str = Field(default="running")  # running | success | failed
    user_id: Optional[str] = Field(default=None, index=True)  # id Supabase (auth.users) de l'auteur
    conversation_id: Optional[int] = Field(default=None, index=True)
    repo_owner: Optional[str] = None
    repo_name: Optional[str] = None
    base_branch: Optional[str] = None
    work_branch: Optional[str] = None
    # Coût/performance de cette exécution (voir track_execution_metrics dans crewquestion.py).
    api_calls_count: Optional[int] = None
    rate_limit_hits: Optional[int] = None
    total_wait_time_seconds: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

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
            print(f"MIGRATION IGNORÉE : {statement!r} -> {type(e).__name__}: {e}")

def create_db_and_tables():
    if _RAW_DATABASE_URL is None:
        print(
            "ATTENTION : DATABASE_URL n'est pas définie — le backend utilise une base "
            "SQLite locale et éphémère (sqlite:///./dev.db), PAS Supabase. Toutes les "
            "données seront perdues au prochain redémarrage/redéploiement. Configure "
            "DATABASE_URL avec la chaîne de connexion Postgres de Supabase."
        )
    else:
        print(f"Connexion à la base de données : {engine.url.render_as_string(hide_password=True)}")
    SQLModel.metadata.create_all(engine)
    _run_lightweight_migrations()

def get_session():
    with Session(engine) as session:
        yield session
