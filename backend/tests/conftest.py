import os
import sys

# Avant tout import de `main` : la seconde tentative automatique attend 90 s en production, jamais dans les tests.
os.environ["AUTO_RETRY_DELAY_S"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
