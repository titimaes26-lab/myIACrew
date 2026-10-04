import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import database  # noqa: E402


def test_engine_detects_dead_pooled_connections_and_recycles_old_ones():
    pool = database.engine.pool
    # Une connexion coupée par le pooler pendant qu'elle dormait est vérifiée avant usage, puis remplacée.
    assert pool._pre_ping is True
    assert pool._recycle == 1800
