import os
import re

ROOT = os.path.dirname(os.path.dirname(__file__))
PIN = re.compile(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;#]+)")


def _pins(name):
    pins = {}
    with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
        for line in handle:
            match = PIN.match(line.strip())
            if match:
                pins[re.sub(r"[-_.]+", "-", match.group(1)).lower()] = match.group(2)
    return pins


def test_every_direct_dependency_is_pinned_and_matches_the_lock_file():
    direct, locked = _pins("requirements.txt"), _pins("requirements.lock")
    assert len(direct) >= 10
    mismatched = {name: (version, locked.get(name)) for name, version in direct.items() if locked.get(name) != version}
    # Une dépendance directe modifiée sans régénérer le verrou ferait échouer le déploiement : on le voit ici d'abord.
    assert mismatched == {}, f"requirements.txt et requirements.lock divergent : {mismatched}"


def test_requirements_apply_the_lock_file_as_constraints():
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as handle:
        assert any(line.strip() == "-c requirements.lock" for line in handle)


def test_the_lock_file_is_fully_pinned_sorted_and_without_duplicates():
    with open(os.path.join(ROOT, "requirements.lock"), encoding="utf-8") as handle:
        lines = [line.strip() for line in handle if line.strip() and not line.startswith("#")]
    assert lines and all(PIN.match(line) and line.count("==") == 1 for line in lines)
    raw = [line.split("==")[0].lower() for line in lines]                     # tri du générateur : nom en minuscules
    names = [re.sub(r"[-_.]+", "-", name) for name in raw]
    assert len(set(names)) == len(names) and raw == sorted(raw)
