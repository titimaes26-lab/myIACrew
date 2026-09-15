import os
from typing import Optional
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
    result: str

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session
