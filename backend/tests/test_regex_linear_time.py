"""Aucune expression régulière du backend n'est à temps quadratique : `re` garde le GIL, un seul motif lent fige TOUT le serveur (API, sondage,
autres exécutions). Constaté deux fois (revues n°15 et n°16) : un tracé SVG de 100 Ko = plus de 20 s dans le masquage des secrets, 30 000 retours à la
ligne = 14 s dans la détection des blocs de code. Chaque motif (module ET appel direct) est joué sur des entrées pathologiques (suites de blancs, de
mots, de retours à la ligne, mots littéraux du motif répétés) dans un interpréteur neuf ; un motif qui dépasse 0,5 s sur 20 000 caractères échoue."""
import json
import os
import subprocess
import sys

import pytest

from regex_scan_support import MATCH_ONLY

HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "regex_scan_support.py")

@pytest.mark.skipif(not hasattr(os, "fork"), reason="le test fork un enfant par motif (Linux/macOS)")
def test_no_backend_regex_is_quadratic():
    run = subprocess.run([sys.executable, HELPER], capture_output=True, text=True, timeout=600, cwd=os.path.dirname(os.path.dirname(HELPER)))
    assert run.returncode == 0, run.stderr[-2000:]
    report = json.loads(run.stdout.strip().splitlines()[-1])
    offenders = report["offenders"]
    assert report["count"] > 100          # le scanner a bien trouvé les motifs du backend
    assert offenders == [], "motifs à temps super-linéaire : " + "; ".join(
        f"{item['label']} {item['pattern'][:70]!r} -> " + (f"BLOQUÉ sur {item['hung_on']}" if item["hung_on"] else f"{item['slow'][:2]}")
        for item in offenders
    )


def test_the_match_only_allowlist_is_really_match_only():
    assert len(MATCH_ONLY) == 4
    """Un motif de la liste d'exceptions ne doit être appelé que par `.match` : sinon l'exception cache un vrai défaut."""
    base = os.path.dirname(os.path.dirname(HELPER))
    checks = {
        "crew_guardrails.py": ("_ADJACENT_PATH_SEPARATOR", "_LIST_SEPARATOR"),
        "delivery_guards.py": ("_SHELL_AT_START",),
        "delivery.py": ("_REQUEST_HEADER_AT_START",),
    }
    for filename, names in checks.items():
        source = open(os.path.join(base, filename), encoding="utf-8").read()
        for name in names:
            for method in ("search", "sub", "finditer", "findall", "split"):
                assert f"{name}.{method}(" not in source, f"{name} appelé avec .{method}"
    assert "separator.search(" not in open(os.path.join(base, "crew_guardrails.py"), encoding="utf-8").read()
