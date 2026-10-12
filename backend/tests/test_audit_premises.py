"""Les 4 avis `chromadb` ignorés par le job « audit » de la CI ne le sont que parce que l'application n'utilise ni serveur Chroma ni
mémoire/connaissances CrewAI : ces tests vérifient cette prémisse, et que la CI ignore bien ces avis (et eux seuls)."""
import glob
import os
import re

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BACKEND)
IGNORED = ["PYSEC-2026-311", "PYSEC-2026-3813", "PYSEC-2026-3814", "PYSEC-2026-3815"]
CHROMA_USE = re.compile(r"chromadb|\bmemory\s*=\s*True|\bMemory\(|knowledge_sources|\bKnowledge\(|\bembedder\s*=|chroma\s+run", re.IGNORECASE)


def _application_files():
    return [p for p in glob.glob(os.path.join(BACKEND, "*.py")) if not os.path.basename(p).startswith("test_")]


def test_the_application_uses_neither_chroma_nor_crewai_memory_or_knowledge():
    users = []
    for path in _application_files():
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if CHROMA_USE.search(line) and not line.lstrip().startswith("#"):
                    users.append(f"{os.path.basename(path)}:{number}")
    assert users == [], (
        "chromadb ou la mémoire CrewAI est utilisé : les avis ignorés par le job audit (.github/workflows/ci.yml) ne sont plus "
        f"justifiés, à réexaminer : {users}")


def test_the_ci_audit_ignores_exactly_the_reviewed_advisories():
    with open(os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8") as handle:
        ci = handle.read()
    assert sorted(re.findall(r"--ignore-vuln\s+(\S+)", ci)) == sorted(IGNORED)
