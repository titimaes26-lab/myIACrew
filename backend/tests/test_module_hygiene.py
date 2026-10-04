"""Garde-fou des découpages de modules : un renommage automatique `module.nom` ne doit jamais s'infiltrer dans un texte
envoyé à un agent ou à l'utilisateur (message d'outil, prompt, schéma attendu), seulement dans le code."""
import ast
import glob
import os
import re

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULES = sorted(
    os.path.basename(p)[:-3] for p in glob.glob(os.path.join(BACKEND, "*.py"))
    if os.path.basename(p) not in ("main.py", "conftest.py") and not os.path.basename(p).startswith("test_")
)
_QUALIFIED = re.compile(r"\b(" + "|".join(re.escape(m) for m in _MODULES) + r")\.[A-Za-z_]\w*")


def _docstring_ids(tree):
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
                ids.add(id(first.value))
    return ids


def test_no_module_qualified_name_leaks_into_a_string_literal():
    leaks = []
    for path in sorted(glob.glob(os.path.join(BACKEND, "*.py"))):
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        docstrings = _docstring_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                match = _QUALIFIED.search(node.value)
                # « schemas.py », « qa_report.md »… : un nom de fichier n'est pas une référence de code.
                if match and not re.match(r"(py|md|ya?ml|json|txt|log)\b", node.value[match.end() - len(match.group(0).split(".")[-1]):]):
                    leaks.append(f"{os.path.basename(path)}:{node.lineno} {node.value[max(0, match.start() - 30):match.end() + 20]!r}")
    assert not leaks, "références `module.nom` dans des textes destinés aux agents/utilisateurs :\n" + "\n".join(leaks)
