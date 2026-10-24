import atexit
import itertools
import os
import shutil
import sys
import tempfile

# Avant tout import de `main` : la seconde tentative automatique attend 90 s en production, jamais dans les tests.
os.environ["AUTO_RETRY_DELAY_S"] = "0"
# Clé factice : CrewAI exige une clé à l'import, aucun test n'appelle réellement le modèle.
os.environ.setdefault("GEMINI_API_KEY", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


import pytest  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402


def _reset_execution_state():
    state = sys.modules.get("execution_state")      # rien à nettoyer si aucun test ne l'a encore chargé
    if state is not None:
        state.abandoned_execution_ids.clear()
        state.active_execution_ids.clear()
    limiter = sys.modules.get("llm_limiter")        # fenêtres et compteurs journaliers par modèle : propres à chaque test
    if limiter is not None:
        limiter.reset_model_limiters()


@pytest.fixture(autouse=True)
def clean_execution_state():
    """Les identifiants d'exécution d'une base SQLite de test repartent de 1 à chaque test : un identifiant resté
    « abandonné » ou « actif » par un test précédent (module execution_state) ferait ignorer la persistance du suivant."""
    _reset_execution_state()
    yield
    _reset_execution_state()


# Base SQLite SUR FICHIER (un dossier temporaire pour toute la session de tests), une par test : comme en production, chaque thread a
# SA connexion. La base « en mémoire » précédente partageait UNE connexion entre tous les threads (`asyncio.to_thread` du code testé) : sous
# charge, leurs transactions s'entremêlaient et un test sur ~100 échouait au hasard (« Multiple rows were found when exactly one was
# required »). `timeout` : SQLite attend un verrou au lieu d'échouer tout de suite.
_DB_DIR = tempfile.mkdtemp(prefix="myiacrew-tests-")
atexit.register(shutil.rmtree, _DB_DIR, ignore_errors=True)
_db_counter = itertools.count()


def _test_engine():
    import database  # noqa: F401  (enregistre les tables dans SQLModel.metadata)
    path = os.path.join(_DB_DIR, f"test{next(_db_counter)}.db")
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 30})
    SQLModel.metadata.create_all(engine)
    return engine


def _discard(engine) -> None:
    """Ferme les connexions et supprime le fichier de la base d'un test."""
    path = engine.url.database
    engine.dispose()
    if path:
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except FileNotFoundError:
                pass


@pytest.fixture()
def engine(monkeypatch):
    """Base SQLite (fichier temporaire propre au test), aussi utilisée par le code testé (`database.engine` est remplacé)."""
    import database
    memory = _test_engine()
    monkeypatch.setattr(database, "engine", memory)
    yield memory
    _discard(memory)


@pytest.fixture()
def session():
    """Session sur une base SQLite (fichier temporaire) isolée, sans toucher à `database.engine`."""
    isolated = _test_engine()
    with Session(isolated) as db:
        yield db
    _discard(isolated)


@pytest.fixture()
def counting(monkeypatch):
    """Faux GitHub qui compte ses lectures (voir github_support.make_counting_repo)."""
    from github_support import make_counting_repo
    return make_counting_repo(monkeypatch)
