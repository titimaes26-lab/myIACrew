import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_auto_retry_delay(monkeypatch):
    """La seconde tentative automatique attend 90 s en production : jamais dans les tests."""
    import main
    monkeypatch.setattr(main, "AUTO_RETRY_DELAY_S", 0)
