import os
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlmodel import Field, SQLModel, create_engine, Session

# Récupération de l'URL depuis les variables d'environnement
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./dev.db")

# Ajustement pour PostgreSQL sur Render/Supabase si besoin
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, echo=True)

# Table pour sauvegarder les demandes et rapports CrewAI
class ExecutionHistory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_request: str
    workflow: str
    clarifications: Optional[str] = None
    result: Optional[str] = None
    status: str = Field(default="running")  # running | success | failed
    user_id: Optional[str] = Field(default=None, index=True)  # id Supabase (auth.users) de l'auteur
    repo_owner: Optional[str] = None
    repo_name: Optional[str] = None
    base_branch: Optional[str] = None
    work_branch: Optional[str] = None
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
]

def _run_lightweight_migrations():
    for statement in _MIGRATION_STATEMENTS:
        try:
            with engine.begin() as conn:
                conn.execute(text(statement))
        except Exception:
            pass  # colonne déjà présente, ou syntaxe non supportée (ex: SQLite en local)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    _run_lightweight_migrations()

def get_session():
    with Session(engine) as session:
        yield session
