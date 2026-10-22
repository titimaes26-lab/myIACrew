"""Les avis ignorés par le job « audit » de la CI ne le sont que sur une PRÉMISSE vérifiée ici, et la CI n'ignore que ceux-là :
- 4 avis `chromadb` : l'application n'utilise ni serveur Chroma ni mémoire/connaissances CrewAI ;
- `mcp` (CVE-2026-59951, fuite mémoire d'un SERVEUR MCP en HTTP « streamable » à état) : l'application n'importe pas `mcp` et ne
  sert aucun MCP ; CrewAI borne `mcp~=1.28.1`, le correctif (1.30) est donc inaccessible ;
- `uv` (PYSEC-2026-4321, racine TLS révoquée dans le magasin d'`uv`) : outil de ligne de commande de `crewai-cli` (borné
  `uv~=0.11.6`, correctif 0.13.4 inaccessible), jamais lancé par l'application ni par le déploiement.
Examinés le 2026-10-22. À revoir à chaque mise à jour de CrewAI."""
import glob
import os
import re

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BACKEND)
IGNORED = ["PYSEC-2026-311", "PYSEC-2026-3813", "PYSEC-2026-3814", "PYSEC-2026-3815", "CVE-2026-59951", "PYSEC-2026-4321"]
CHROMA_USE = re.compile(r"chromadb|\bmemory\s*=\s*True|\bMemory\(|knowledge_sources|\bKnowledge\(|\bembedder\s*=|chroma\s+run", re.IGNORECASE)


MCP_USE = re.compile(r"^\s*(?:import|from)\s+mcp\b|FastMCP|StreamableHTTPSessionManager|streamable_http_app|MCPServer|\bmcps\s*=")
UV_USE = re.compile(r"""^\s*(?:import|from)\s+uv\b|["']uv["']|\buv\s+(?:pip|run|sync|venv|tool)\b""")


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


def _offenders(pattern):
    found = []
    for path in _application_files():
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if pattern.search(line) and not line.lstrip().startswith("#"):
                    found.append(f"{os.path.basename(path)}:{number}")
    return found


def test_the_application_neither_imports_nor_serves_mcp():
    assert _offenders(MCP_USE) == [], "l'application utilise mcp : l'avis CVE-2026-59951 ignoré par la CI est à réexaminer"


def test_neither_the_application_nor_its_deployment_runs_uv():
    assert _offenders(UV_USE) == [], "l'application lance uv : l'avis PYSEC-2026-4321 ignoré par la CI est à réexaminer"
    for relative in ("backend/requirements.txt", "backend/requirements-dev.txt", ".github/workflows/ci.yml"):
        with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
            lines = [line for line in handle.read().splitlines() if not line.lstrip().startswith("#")]
        assert not [line for line in lines if re.search(r"(^|\s)uv\b(?!-)", line)], f"{relative} utilise uv"
