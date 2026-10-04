import os
import sys

# Avant tout import de `main` : la seconde tentative automatique attend 90 s en production, jamais dans les tests.
os.environ["AUTO_RETRY_DELAY_S"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def clean_execution_state():
    """Les identifiants d'exécution d'une base SQLite en mémoire repartent de 1 à chaque test : un identifiant resté
    « abandonné » ou « actif » par un test précédent (module execution_state) ferait ignorer la persistance du suivant."""
    import execution_state
    execution_state.abandoned_execution_ids.clear()
    execution_state.active_execution_ids.clear()
    yield
    execution_state.abandoned_execution_ids.clear()
    execution_state.active_execution_ids.clear()
