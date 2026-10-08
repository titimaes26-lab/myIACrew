"""Vérification statique d'un fichier avant commit (tools.check_syntax_content) : Python/JSON/YAML exacts, TS/JS heuristique."""
import glob
import os

import pytest

from delimiter_scan import scan_delimiters
from tools import check_syntax_content

BLOCKING = "ERREUR_SYNTAXE"


@pytest.mark.parametrize("content, path, blocking", [
    ("x = 1\n", "a.py", False), ("def f(:\n", "a.py", True),
    ('{"a": 1}', "a.json", False), ('{"a": ', "a.json", True),
    ("a: 1\nb: [1, 2]\n", "a.yaml", False), ("a: [1, 2\n", "b.yml", True),
])
def test_python_json_and_yaml_are_checked_exactly(content, path, blocking):
    assert check_syntax_content(content, path).startswith(BLOCKING) is blocking


@pytest.mark.parametrize("content", [
    "const a = {",                                           # objet jamais refermé
    "export function f() {\n  if (x) {\n    return 1;\n",   # fonction coupée
    "const a = `début ${b}",                                # template literal non terminé
    "const a = `x ${b",                                     # interpolation jamais refermée
    "const a = `${b ? `x` : ''}`;\nconst c = `fin",            # template imbriqué puis coupure
    "/* commentaire jamais fermé\nconst a = 1;",
    "const x = <div>{items.map((i) => (\n  <li>{i}</li>\n",
])
@pytest.mark.parametrize("path", ["a.ts", "a.tsx", "a.js", "a.jsx"])
def test_a_file_cut_at_the_end_is_blocked_for_every_script_extension(content, path):
    result = check_syntax_content(content, path)
    assert result.startswith(BLOCKING) and "tronqué" in result


@pytest.mark.parametrize("content", [
    "export const a = 1;\n",
    "// n'existe pas { dans un commentaire\nconst a = 1;\n",
    "const s = '{' + \"(\" + `[`;\n",                       # délimiteurs dans des chaînes
    "const re = /[{(]/g;\nconst ok = x.replace(/['\"]/g, '');\n",
    "function f() { return /\\}/.test(x); }\n",
    "const t = `a ${b} c ${d ? `e ${f}` : ''} g`;\n",       # interpolations et templates imbriqués
    "const j = <p>L'exécution continue, n'y touche pas.</p>;\n",   # apostrophes d'un texte JSX
    "const a = <p>d'un côté\net l'autre</p>;\n",
    "const x = a < b ? 1 : 2;\n",
    "/* a * { b */\nconst c = 1;\n",                       # une étoile ne ferme pas un commentaire bloc
    "const r = /[/{]/;\nconst s = 1;\n",                     # « / » et « { » dans une classe de regex
    'const a = "x" / 3 + (1 / 2);\n',                        # division après une chaîne, pas une regex
    'const a = <div>{cond && <p className="alpha\n  beta">texte</p>}</div>;\n',        # attribut JSX sur plusieurs lignes
    "const a = <p title='alpha\n  beta'>{x}</p>;\n",
    'const a = <p className="alpha\n  beta" onClick={() => { go(); }}>x</p>;\n',
])
def test_valid_script_code_is_never_blocked(content):
    result = check_syntax_content(content, "a.tsx")
    assert not result.startswith(BLOCKING) and not result.startswith("PROBLÈME"), result


def test_a_stray_closing_parenthesis_is_reported_but_never_blocks():
    # « 1) Premier » est un texte JSX valide : rapporté à l'agent comme indice, jamais comme refus de commit.
    result = check_syntax_content("const a = <p>1) Premier</p>;\n", "a.tsx")
    assert result.startswith("PROBLÈME") and not result.startswith(BLOCKING)


def test_unknown_extensions_are_not_checked():
    assert check_syntax_content("{{{", "a.css").startswith("INFO")


def test_the_scan_reports_truncation_only_for_a_cut_signature():
    assert scan_delimiters("const a = (1 + 2") == (["'(' jamais refermé."], False)      # parenthèse seule : indice
    assert scan_delimiters("const a = {")[1] is True
    assert scan_delimiters("const a = 1;\n") == ([], False)


def test_every_script_file_of_the_frontend_passes():
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend", "src")
    files = [f for f in glob.glob(os.path.join(root, "**", "*"), recursive=True) if f.endswith((".ts", ".tsx"))]
    if not files:
        pytest.skip("dossier frontend/src absent")
    for path in files:
        with open(path, encoding="utf-8") as handle:
            result = check_syntax_content(handle.read(), path)
        assert not result.startswith(BLOCKING), f"{path} : {result}"


def test_the_scan_stops_after_five_unexpected_closers_and_reports_their_line():
    issues, truncated = scan_delimiters(")" * 10)
    assert len(issues) == 5 and not truncated
    assert scan_delimiters("a\nb\n)")[0] == ["')' inattendu ligne 3 (aucune ouverture correspondante)."]
