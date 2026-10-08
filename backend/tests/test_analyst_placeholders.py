"""Raccourcis laissés par le modèle (« // … reste du code ») : détection et faux positifs à éviter."""


from analyst_output import review_diagnostic_output
from analyst_placeholders import find_placeholders
from analyst_support import block, edit


def test_placeholder_comments_are_detected():
    files, issue, faulty, _ = review_diagnostic_output(block("src/a.ts", "const a = 1;\n// ... reste du code inchangé\n"))
    assert len(files) == 1
    assert issue and "src/a.ts ligne 2" in issue
    assert faulty == {"src/a.ts"}


def test_legitimate_comments_are_not_placeholders():
    files = [
        {"path": "a.ts", "content": "// ...args are forwarded to the logger\n// TODO: à compléter quand l'API sera prête\n"},
        {"path": "b.py", "content": "# Conserve le code existant pour compatibilité\n"},
        {"path": "README.md", "content": "## Intégration au code existant\n"},
        {"path": "style.css", "content": "#reste-du-code { color: red; }\n"},
    ]
    assert find_placeholders(files) == []
    shortcuts = [{"path": "c.ts", "content": (
        "// ...\n// ... reste inchangé\n/* ... existing code */\n// reste du code inchangé\n"
        "// Code inchangé si l'utilisateur n'est pas connecté\n// Le reste du composant gère l'affichage\n"
    )}]
    assert [n for _, n, _ in find_placeholders(shortcuts)] == [1, 2, 3, 4]
    assert [p for p, _, _ in find_placeholders([{"path": "app.py", "content": "# ... reste du code\n"}])] == ["app.py"]


def test_dotfiles_without_real_extension_are_scanned_as_hash_comments():
    # ".env", ".gitignore" etc. commencent par un point qui n'est PAS un séparateur d'extension :
    # ce sont des fichiers sans extension, et ils utilisent quasi toujours "#" comme commentaire.
    from analyst_blocks import extension

    assert extension(".env") == ""
    assert extension(".eslintrc.json") == "json"
    files = [{"path": ".env", "content": "API_KEY=1\n# ... reste inchangé\n"}]
    assert [p for p, _, _ in find_placeholders(files)] == [".env"]


def test_spread_style_comments_are_not_placeholders():
    files = [{"path": "a.tsx", "content": "// ...rest is forwarded to the input\n// ...same props as Button\n"}]
    assert find_placeholders(files) == []


def test_bracketed_shortcut_comments_are_detected():
    files = [{"path": "a.ts", "content": "// (reste du code inchangé)\n// [...]\n// … (code existant)\n// ...(args) forwarded\n"}]
    assert [n for _, n, _ in find_placeholders(files)] == [1, 2, 3]


def test_shortcuts_with_articles_and_state_verbs_are_detected():
    files = [{"path": "a.ts", "content": (
        "// ... le reste du fichier est inchangé\n"
        "// ... the rest remains unchanged\n"
        "// le reste du code est inchangé\n"
        "// Code inchangé si l'utilisateur n'est pas connecté\n"
    )}, {"path": "b.py", "content": "# ... les autres fonctions restent identiques\n"}]
    assert [(p, n) for p, n, _ in find_placeholders(files)] == [("a.ts", 1), ("a.ts", 2), ("a.ts", 3), ("b.py", 1)]


def test_elided_and_partitive_shortcuts_are_detected():
    files = [{"path": "a.ts", "content": "// ... l'existant\n// ... du code existant\n"}]
    assert [n for _, n, _ in find_placeholders(files)] == [1, 2]


def test_placeholder_check_only_looks_at_added_text_for_edits():
    base = "// ... reste du code historique\nconst a = 1;\n"
    ok = review_diagnostic_output(edit("src/x.ts", ("const a = 1;", "const a = 2;")), read_base=lambda p: (base, None))
    assert ok[1] is None
    bad = review_diagnostic_output(edit("src/x.ts", ("const a = 1;", "// ... reste du code\nconst a = 2;")), read_base=lambda p: (base, None))
    assert bad[1] and "src/x.ts" in bad[2]
