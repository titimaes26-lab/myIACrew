import os
import sys

# Avant tout import de `main` : la seconde tentative automatique attend 90 s en production, jamais dans les tests.
os.environ["AUTO_RETRY_DELAY_S"] = "0"
# Clé factice : CrewAI exige une clé à l'import, aucun test n'appelle réellement le modèle.
os.environ.setdefault("GEMINI_API_KEY", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


import pytest  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402


def _reset_execution_state():
    state = sys.modules.get("execution_state")      # rien à nettoyer si aucun test ne l'a encore chargé
    if state is not None:
        state.abandoned_execution_ids.clear()
        state.active_execution_ids.clear()


@pytest.fixture(autouse=True)
def clean_execution_state():
    """Les identifiants d'exécution d'une base SQLite en mémoire repartent de 1 à chaque test : un identifiant resté
    « abandonné » ou « actif » par un test précédent (module execution_state) ferait ignorer la persistance du suivant."""
    _reset_execution_state()
    yield
    _reset_execution_state()


def _memory_engine():
    import database  # noqa: F401  (enregistre les tables dans SQLModel.metadata)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def engine(monkeypatch):
    """Base SQLite en mémoire, aussi utilisée par le code testé (`database.engine` est remplacé)."""
    import database
    memory = _memory_engine()
    monkeypatch.setattr(database, "engine", memory)
    return memory


@pytest.fixture()
def session():
    """Session sur une base SQLite en mémoire isolée, sans toucher à `database.engine`."""
    with Session(_memory_engine()) as db:
        yield db


@pytest.fixture()
def counting(monkeypatch):
    """Faux GitHub qui compte ses lectures (voir github_support.make_counting_repo)."""
    from github_support import make_counting_repo
    return make_counting_repo(monkeypatch)
