import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyst_output import (  # noqa: E402
    FILE_ABSENT,
    PRESENT_UNREADABLE,
    build_delivery_report,
    find_placeholders,
    parse_file_sections,
    review_diagnostic_output,
)


def parse_file_blocks(text):
    return parse_file_sections(text)[0]


def block(path, content, lang="ts"):
    return f"<<<FICHIER: {path}>>>\n```{lang}\n{content}```\n<<<FIN_FICHIER>>>\n"


def test_extracts_files_and_strips_outer_fence():
    text = "## Plan\n- src/App.tsx\n\n" + block("src/App.tsx", "const items = [...base];\n") + block("b.py", "x = 1\n", "python")
    assert parse_file_blocks(text) == [
        {"path": "src/App.tsx", "content": "const items = [...base];\n"},
        {"path": "b.py", "content": "x = 1\n"},
    ]


def test_content_without_fence_is_kept_verbatim():
    assert parse_file_blocks("<<<FICHIER: a.txt>>>\nligne\n<<<FIN_FICHIER>>>\n") == [
        {"path": "a.txt", "content": "ligne\n"}
    ]


def test_inner_fences_are_part_of_the_file():
    # README avec des blocs ``` sans langage, et template literal TS contenant ``` seul.
    readme = "# T\n```\nnpm i\n```\nFin\n"
    ts = "const md = `\n```\n`;\nexport default md;\n"
    files = parse_file_blocks(block("README.md", readme, "md") + block("a.ts", ts))
    assert files[0]["content"] == readme
    assert files[1]["content"] == ts


def test_text_after_the_end_marker_is_never_appended():
    text = block("docs/a.md", "# A\n", "markdown") + "Et ensuite :\n```bash\nnpm i\n```\n"
    assert parse_file_blocks(text) == [{"path": "docs/a.md", "content": "# A\n"}]


def test_headings_comments_and_quotes_outside_markers_are_ignored():
    text = (
        "### Fichier : src/big.tsx (extrait)\n```tsx\nconst old = 1;\n```\n"
        "**Auto-revue** : voir en fin\n"
        + block("src/utils.ts", "// Fichier : src/utils.ts\nexport const u = 1;\n")
    )
    assert parse_file_blocks(text) == [
        {"path": "src/utils.ts", "content": "// Fichier : src/utils.ts\nexport const u = 1;\n"}
    ]


def test_missing_end_marker_is_reported_not_committed():
    text = block("a.py", "x = 1\n", "python") + "<<<FICHIER: src/store.ts>>>\n```ts\nexport const a ="
    files, issue, _, _ = review_diagnostic_output(text)
    assert [f["path"] for f in files] == ["a.py"]
    assert issue and "src/store.ts" in issue and "FIN_FICHIER" in issue


def test_new_start_before_end_marks_previous_file_broken():
    text = "<<<FICHIER: a.ts>>>\nconst a = 1;\n" + block("b.ts", "const b = 1;\n")
    files, broken = parse_file_sections(text)
    assert [f["path"] for f in files] == ["b.ts"]
    assert set(broken) == {"a.ts"}


def test_last_version_wins_even_if_shorter():
    text = block("cart.ts", "const a = 1;\nconst b = 2;\nbug();\n") + block("cart.ts", "fix();\n")
    assert parse_file_blocks(text) == [{"path": "cart.ts", "content": "fix();\n"}]


def test_truncated_last_version_never_falls_back_to_earlier_one():
    text = block("a.ts", "export const old = 1;\n") + "<<<FICHIER: a.ts>>>\n```ts\nexport const fixed ="
    files, broken = parse_file_sections(text)
    assert files == []
    assert set(broken) == {"a.ts"}


def test_invalid_paths_are_rejected_and_decorations_stripped():
    text = block("src/App.tsx (extrait)", "x\n") + block("`./src/b.ts`", "y\n") + block("/src/c.ts", "z\n")
    files, broken = parse_file_sections(text)
    assert [f["path"] for f in files] == ["src/b.ts", "src/c.ts"]
    assert broken == {"src/App.tsx (extrait)": "chemin invalide"}


def test_output_without_files_is_rejected_unless_declared_not_delivered():
    assert review_diagnostic_output("Voici mon analyse sans code.")[1] is not None
    assert review_diagnostic_output("- big.tsx : NON réalisé (trop volumineux)")[1] is None
    # Du code hors balises : le marqueur "non réalisé" ne suffit plus.
    unmarked = "Fichiers NON réalisés : aucun\n### Fichier : src/App.tsx\n```tsx\nexport {}\n```\n"
    assert review_diagnostic_output(unmarked)[1] is not None


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


def test_delivery_report_flags_absent_divergent_identical_and_unverifiable():
    files = [
        {"path": "same.json", "content": '{"a": 1}\n'},
        {"path": "diff.py", "content": "x = 1\n"},
        {"path": "missing.ts", "content": "export {};\n"},
        {"path": "limited.ts", "content": "a\n"},
        {"path": "big.json", "content": "{}\n"},
        {"path": "refused.py", "content": "x = 1\n"},
    ]
    remote = {"same.json": '{"a": 1}', "diff.py": "x = 2\n", "refused.py": "ancien\n"}

    def fetch(path):
        if path in remote:
            return remote[path], None
        if path == "limited.ts":
            return None, "ERREUR_GITHUB : API rate limit exceeded"
        if path == "big.json":
            return None, f"{PRESENT_UNREADABLE} : trop volumineux"
        return None, f"{FILE_ABSENT} : introuvable"

    report = build_delivery_report(
        files, fetch,
        write_rejections={"refused.py": "ERREUR_SYNTAXE", "same.json": "échec d'un 1er commit"},
        not_extracted={"src/cart.ts": "contenu incomplet"},
    )
    sections = {s.split("\n", 1)[0]: s for s in report.split("### ")[1:]}
    assert "IDENTIQUE" in sections["same.json"]
    assert "DIVERGENT" in sections["diff.py"] and "+x = 2" in sections["diff.py"]
    assert "ABSENT [vérifié outil]" in sections["missing.ts"]
    assert "NON VÉRIFIABLE" in sections["limited.ts"] and "ABSENT [" not in sections["limited.ts"]
    assert "PRÉSENT" in sections["big.json"] and "NON VÉRIFIABLE" in sections["big.json"]
    assert "NON LIVRÉ" in sections["refused.py"] and "DIVERGENT" not in sections["refused.py"]
    # Refusé à un premier commit mais bien présent depuis : pas de faux NON LIVRÉ.
    assert "NON LIVRÉ" not in sections["same.json"]
    assert "jamais committable" in sections["src/cart.ts"]


def test_readme_starting_and_ending_with_fences_is_not_unwrapped():
    readme = "```bash\nnpm i\n```\ntexte\n```js\nx\n```\n"
    text = f"<<<FICHIER: README.md>>>\n{readme}<<<FIN_FICHIER>>>\n"
    assert parse_file_blocks(text)[0]["content"] == readme


def test_spread_style_comments_are_not_placeholders():
    files = [{"path": "a.tsx", "content": "// ...rest is forwarded to the input\n// ...same props as Button\n"}]
    assert find_placeholders(files) == []


def test_marker_variants_do_not_produce_bogus_paths():
    text = (
        "<<<FICHIER: src/App.tsx>>>>\nx\n<<<FIN_FICHIER>>>\n"
        "<<<FICHIER: <src/b.ts>>>>\ny\n<<<FIN FICHIER>>>\n"
    )
    assert [f["path"] for f in parse_file_blocks(text)] == ["src/App.tsx", "src/b.ts"]


def test_closing_fence_after_end_marker_is_not_committed():
    text = "<<<FICHIER: src/a.ts>>>\n```ts\nconst a = 1;\n<<<FIN_FICHIER>>>\n```\n"
    assert parse_file_blocks(text) == [{"path": "src/a.ts", "content": "const a = 1;\n"}]


def test_bracketed_shortcut_comments_are_detected():
    files = [{"path": "a.ts", "content": "// (reste du code inchangé)\n// [...]\n// … (code existant)\n// ...(args) forwarded\n"}]
    assert [n for _, n, _ in find_placeholders(files)] == [1, 2, 3]


def test_marker_with_trailing_text_and_malformed_markers_are_never_silent():
    text = (
        "<<<FICHIER: src/a.ts>>> (nouveau)\nexport const a = 1;\n<<<FIN_FICHIER>>>\n"
        "<<<FICHER: src/b.ts>>>\nexport const b = 1;\n<<<FIN_FICHIER>>>\n"
    )
    files, issue, _, broken = review_diagnostic_output(text)
    assert [f["path"] for f in files] == ["src/a.ts"]
    assert issue is not None and any("orpheline" in k or "FICHER" in k for k in broken)


def test_note_after_closing_fence_is_flagged_not_committed():
    text = "<<<FICHIER: a.py>>>\n```python\nx = 1\n```\n\nNote : ok\n<<<FIN_FICHIER>>>\n"
    files, broken = parse_file_sections(text)
    assert files == [] and "a.py" in broken


def test_shortcuts_with_articles_and_state_verbs_are_detected():
    files = [{"path": "a.ts", "content": (
        "// ... le reste du fichier est inchangé\n"
        "// ... the rest remains unchanged\n"
        "// le reste du code est inchangé\n"
        "// Code inchangé si l'utilisateur n'est pas connecté\n"
    )}, {"path": "b.py", "content": "# ... les autres fonctions restent identiques\n"}]
    assert [(p, n) for p, n, _ in find_placeholders(files)] == [("a.ts", 1), ("a.ts", 2), ("a.ts", 3), ("b.py", 1)]


def test_malformed_marker_inside_open_file_does_not_merge_files():
    text = "<<<FICHIER: a.py>>>\nx = 1\n<<<FICHIER b.py>>>\ny = 2\n<<<FIN_FICHIER>>>\n"
    files, broken = parse_file_sections(text)
    assert files == [] and "a.py" in broken
    assert "(balise orpheline)" not in broken


def test_end_marker_for_another_file_is_flagged():
    files, broken = parse_file_sections("<<<FICHIER: a.py>>>\nx = 1\n<<<FIN_FICHIER: b.py>>>\n")
    assert files == [] and "a.py" in broken


def test_elided_and_partitive_shortcuts_are_detected():
    files = [{"path": "a.ts", "content": "// ... l'existant\n// ... du code existant\n"}]
    assert [n for _, n, _ in find_placeholders(files)] == [1, 2]


def test_end_marker_repeating_the_path_is_accepted():
    text = "<<<FICHIER: src/a.ts>>>\nexport const a = 1;\n<<<FIN_FICHIER: src/a.ts>>>\n"
    assert parse_file_sections(text) == ([{"path": "src/a.ts", "content": "export const a = 1;\n"}], {})


def test_end_marker_variants_close_the_right_file():
    text = (
        "<<<FICHIER: src/components/App.tsx>>>\nx\n<<<FIN_FICHIER: App.tsx>>>\n"
        "<<<FICHIER: a.py>>>\ny = 1\n<<<FIN_FICHIER>>> (a.py)\n"
    )
    files, broken = parse_file_sections(text)
    assert [f["path"] for f in files] == ["src/components/App.tsx", "a.py"] and broken == {}


def test_not_delivered_none_is_not_a_deliberate_non_delivery():
    assert review_diagnostic_output("Fichiers NON réalisés : aucun")[1] is not None


def test_indented_markers_do_not_shift_file_content():
    text = (
        "1. Fichier principal :\n"
        "   <<<FICHIER: app.py>>>\n"
        "   ```python\n"
        "   import os\n"
        "   if True:\n"
        "       x = 1\n"
        "   ```\n"
        "   <<<FIN_FICHIER>>>\n"
    )
    files, broken = parse_file_sections(text)
    assert broken == {}
    assert files == [{"path": "app.py", "content": "import os\nif True:\n    x = 1\n"}]


def test_marker_only_indentation_keeps_content_as_written():
    text = "  <<<FICHIER: c.yaml>>>\na:\n  b: 1\n  c: 2\n  <<<FIN_FICHIER>>>\n"
    assert parse_file_sections(text)[0] == [{"path": "c.yaml", "content": "a:\n  b: 1\n  c: 2\n"}]


def test_tab_indented_marker_with_space_indented_content_is_dedented():
    text = "\t<<<FICHIER: app.py>>>\n    def f():\n        return 1\n\t<<<FIN_FICHIER>>>\n"
    assert parse_file_sections(text)[0] == [{"path": "app.py", "content": "def f():\n    return 1\n"}]


def test_withdrawal_mentions_inside_indented_file_bodies_are_ignored():
    from analyst_output import FILE_BLOCKS
    text = "   <<<FICHIER: README.md>>>\n   - src/b.ts : NON réalisé (suivi)\n   <<<FIN_FICHIER>>>\n"
    assert "NON réalisé" not in FILE_BLOCKS.sub("", text)


def test_bold_decorated_markers_are_recognized():
    text = "**<<<FICHIER: src/a.ts>>>**\nexport const a = 1;\n**<<<FIN_FICHIER>>>**\n"
    assert parse_file_sections(text) == ([{"path": "src/a.ts", "content": "export const a = 1;\n"}], {})


def test_backtick_decorated_markers_are_recognized():
    text = "`<<<FICHIER: a.py>>>`\nx = 1\n`<<<FIN_FICHIER>>>`\n"
    assert parse_file_sections(text) == ([{"path": "a.py", "content": "x = 1\n"}], {})


def test_normal_bold_content_line_is_not_treated_as_a_marker():
    # _undecorate ne s'applique qu'à la RECONNAISSANCE de balise : le contenu réel du fichier
    # (une ligne en gras ordinaire) reste inchangé.
    text = "<<<FICHIER: a.py>>>\n**bold content**\n<<<FIN_FICHIER>>>\n"
    assert parse_file_sections(text) == ([{"path": "a.py", "content": "**bold content**\n"}], {})


def test_decorated_malformed_marker_is_reported_not_silently_dropped():
    text = "**<<<FICHIER a.py>>>**\nx = 1\n"
    files, broken = parse_file_sections(text)
    assert files == [] and broken


def test_decorated_marker_with_trailing_note_is_recognized():
    text = "**<<<FICHIER: a.ts>>>** (nouveau)\nx=1\n**<<<FIN_FICHIER>>>**\n"
    assert parse_file_sections(text) == ([{"path": "a.ts", "content": "x=1\n"}], {})


def test_italic_underscore_marker_is_not_truncated_by_the_embedded_underscore():
    # "FIN_FICHIER" contient déjà un "_" : la clôture italique ne doit pas être confondue avec lui.
    text = "_<<<FICHIER: a.py>>>_\nx = 1\n_<<<FIN_FICHIER>>>_\n"
    assert parse_file_sections(text) == ([{"path": "a.py", "content": "x = 1\n"}], {})


def test_triple_asterisk_bold_italic_marker_is_recognized():
    text = "***<<<FICHIER: a.py>>>***\nx = 1\n***<<<FIN_FICHIER>>>***\n"
    assert parse_file_sections(text) == ([{"path": "a.py", "content": "x = 1\n"}], {})


def test_asymmetric_decoration_missing_closing_marker_is_recognized():
    # Ouverture décorée mais jamais refermée : FILE_START/FILE_END absorbent seuls le reste.
    text = "**<<<FICHIER: src/a.ts>>>\nx = 1\n<<<FIN_FICHIER>>>\n"
    assert parse_file_sections(text) == ([{"path": "src/a.ts", "content": "x = 1\n"}], {})
