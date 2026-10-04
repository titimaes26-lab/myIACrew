"""Balises de fichiers : extraction, clôtures, chemins, marqueurs décorés ou mal formés, indentation."""


from analyst_output import review_diagnostic_output
from analyst_blocks import parse_file_sections
from analyst_support import parse_file_blocks, block


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


def test_readme_starting_and_ending_with_fences_is_not_unwrapped():
    readme = "```bash\nnpm i\n```\ntexte\n```js\nx\n```\n"
    text = f"<<<FICHIER: README.md>>>\n{readme}<<<FIN_FICHIER>>>\n"
    assert parse_file_blocks(text)[0]["content"] == readme


def test_marker_variants_do_not_produce_bogus_paths():
    text = (
        "<<<FICHIER: src/App.tsx>>>>\nx\n<<<FIN_FICHIER>>>\n"
        "<<<FICHIER: <src/b.ts>>>>\ny\n<<<FIN FICHIER>>>\n"
    )
    assert [f["path"] for f in parse_file_blocks(text)] == ["src/App.tsx", "src/b.ts"]


def test_closing_fence_after_end_marker_is_not_committed():
    text = "<<<FICHIER: src/a.ts>>>\n```ts\nconst a = 1;\n<<<FIN_FICHIER>>>\n```\n"
    assert parse_file_blocks(text) == [{"path": "src/a.ts", "content": "const a = 1;\n"}]


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


def test_malformed_marker_inside_open_file_does_not_merge_files():
    text = "<<<FICHIER: a.py>>>\nx = 1\n<<<FICHIER b.py>>>\ny = 2\n<<<FIN_FICHIER>>>\n"
    files, broken = parse_file_sections(text)
    assert files == [] and "a.py" in broken
    assert "(balise orpheline)" not in broken


def test_end_marker_for_another_file_is_flagged():
    files, broken = parse_file_sections("<<<FICHIER: a.py>>>\nx = 1\n<<<FIN_FICHIER: b.py>>>\n")
    assert files == [] and "a.py" in broken


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
    from analyst_blocks import FILE_BLOCKS
    text = "   <<<FICHIER: README.md>>>\n   - src/b.ts : NON réalisé (suivi)\n   <<<FIN_FICHIER>>>\n"
    assert "NON réalisé" not in FILE_BLOCKS.sub("", text)


def test_bold_decorated_markers_are_recognized():
    text = "**<<<FICHIER: src/a.ts>>>**\nexport const a = 1;\n**<<<FIN_FICHIER>>>**\n"
    assert parse_file_sections(text) == ([{"path": "src/a.ts", "content": "export const a = 1;\n"}], {})


def test_backtick_decorated_markers_are_recognized():
    text = "`<<<FICHIER: a.py>>>`\nx = 1\n`<<<FIN_FICHIER>>>`\n"
    assert parse_file_sections(text) == ([{"path": "a.py", "content": "x = 1\n"}], {})


def test_normal_bold_content_line_is_not_treated_as_a_marker():
    # undecorate ne s'applique qu'à la RECONNAISSANCE de balise : le contenu réel du fichier
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


def test_text_after_closing_fence_is_flagged_even_for_prose_files():
    # Un fichier .md (PROSE_EXTENSIONS) avec une note d'Analyste après la clôture ``` doit être
    # signalé comme inexploitable, exactement comme un fichier de code — pas committé tel quel.
    text = "<<<FICHIER: README.md>>>\n```\n# Titre\n```\nNote hors sujet\n<<<FIN_FICHIER>>>\n"
    files, broken = parse_file_sections(text)
    assert files == [] and "README.md" in broken and "clôture" in broken["README.md"]
