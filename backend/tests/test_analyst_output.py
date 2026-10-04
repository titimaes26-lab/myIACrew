import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyst_output import FILE_ABSENT, PRESENT_UNREADABLE, build_delivery_report, review_diagnostic_output
from analyst_placeholders import find_placeholders
from analyst_imports import find_import_problems
from analyst_edits import apply_edits, parse_edit_sections
from analyst_blocks import parse_file_sections


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


def test_dotfiles_without_real_extension_are_scanned_as_hash_comments():
    # ".env", ".gitignore" etc. commencent par un point qui n'est PAS un séparateur d'extension :
    # ce sont des fichiers sans extension, et ils utilisent quasi toujours "#" comme commentaire.
    from analyst_blocks import _extension

    assert _extension(".env") == ""
    assert _extension(".eslintrc.json") == "json"
    files = [{"path": ".env", "content": "API_KEY=1\n# ... reste inchangé\n"}]
    assert [p for p, _, _ in find_placeholders(files)] == [".env"]


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


def test_text_after_closing_fence_is_flagged_even_for_prose_files():
    # Un fichier .md (PROSE_EXTENSIONS) avec une note d'Analyste après la clôture ``` doit être
    # signalé comme inexploitable, exactement comme un fichier de code — pas committé tel quel.
    text = "<<<FICHIER: README.md>>>\n```\n# Titre\n```\nNote hors sujet\n<<<FIN_FICHIER>>>\n"
    files, broken = parse_file_sections(text)
    assert files == [] and "README.md" in broken and "clôture" in broken["README.md"]


# --- Modifications ciblées -------------------------------------------------------------------

def edit(path, *pairs):
    body = "".join(f"<<<<<<< CHERCHER\n{a}\n=======\n{b}\n>>>>>>> REMPLACER\n" for a, b in pairs)
    return f"<<<MODIFICATION: {path}>>>\n{body}<<<FIN_MODIFICATION>>>\n"


def test_parse_edit_sections_reads_blocks_in_order():
    edits, broken = parse_edit_sections("Plan\n" + edit("src/a.ts", ("a = 1", "a = 2"), ("b", "c\nd")))
    assert edits == {"src/a.ts": [("a = 1", "a = 2"), ("b", "c\nd")]} and not broken


def test_parse_edit_sections_rejects_truncated_or_empty_blocks():
    truncated = "<<<MODIFICATION: src/a.ts>>>\n<<<<<<< CHERCHER\nx\n=======\ny\n"
    edits, broken = parse_edit_sections(truncated)
    assert not edits and "src/a.ts" in broken
    edits, broken = parse_edit_sections("<<<MODIFICATION: src/b.ts>>>\n<<<FIN_MODIFICATION>>>\n")
    assert not edits and "aucun bloc" in broken["src/b.ts"]


def test_apply_edits_replaces_a_unique_match_and_keeps_crlf():
    assert apply_edits("a\nb\nc\n", [("b", "B")]) == ("a\nB\nc\n", None)
    assert apply_edits("a\r\nb\r\n", [("a", "A")]) == ("A\r\nb\r\n", None)


def test_apply_edits_refuses_missing_ambiguous_or_empty_search():
    assert "introuvable" in apply_edits("a\n", [("zzz", "y")])[1]
    assert "2 fois" in apply_edits("x\nx\n", [("x", "y")])[1]
    assert "vide" in apply_edits("a\n", [("  ", "y")])[1]


def test_review_resolves_edits_into_complete_files():
    base = "const a = 1;\nconst b = 2;\n"
    files, issue, faulty, broken = review_diagnostic_output(
        edit("src/x.ts", ("const b = 2;", "const b = 3;")), read_base=lambda path: (base, None)
    )
    assert files == [{"path": "src/x.ts", "content": "const a = 1;\nconst b = 3;\n"}]
    assert issue is None and not broken


def test_review_flags_unreadable_base_and_double_delivery():
    files, issue, _, broken = review_diagnostic_output(
        edit("src/x.ts", ("a", "b")), read_base=lambda path: (None, "ABSENT : introuvable")
    )
    assert not files and "src/x.ts" in broken and "modification impossible" in broken["src/x.ts"]
    both = block("src/x.ts", "const a = 1;\n") + edit("src/x.ts", ("a", "b"))
    files, issue, _, broken = review_diagnostic_output(both, read_base=lambda path: ("a\n", None))
    assert not files and "à la fois" in broken["src/x.ts"]


def test_review_without_base_reader_cannot_apply_edits():
    _, issue, _, broken = review_diagnostic_output(edit("src/x.ts", ("a", "b")))
    assert "src/x.ts" in broken and issue


def test_placeholder_check_only_looks_at_added_text_for_edits():
    base = "// ... reste du code historique\nconst a = 1;\n"
    ok = review_diagnostic_output(edit("src/x.ts", ("const a = 1;", "const a = 2;")), read_base=lambda p: (base, None))
    assert ok[1] is None
    bad = review_diagnostic_output(edit("src/x.ts", ("const a = 1;", "// ... reste du code\nconst a = 2;")), read_base=lambda p: (base, None))
    assert bad[1] and "src/x.ts" in bad[2]


# --- Cohérence des imports -------------------------------------------------------------------

def f(path, content):
    return {"path": path, "content": content}


def test_import_of_missing_named_export_is_flagged():
    files = [f("src/App.tsx", "import { useCart, total } from './hooks/useCart';\n"),
             f("src/hooks/useCart.ts", "export function useCart() { return 1; }\n")]
    problems = find_import_problems(files)
    assert len(problems) == 1 and "'total'" in problems[0]


def test_valid_named_default_and_type_imports_are_accepted():
    files = [f("src/App.tsx", "import Cart, { type Item, useCart as uc } from './cart';\n"),
             f("src/cart.ts", "export default function Cart() {}\nexport interface Item {}\nexport const useCart = 1;\n")]
    assert find_import_problems(files) == []


def test_default_import_without_default_export_and_star_reexport():
    files = [f("src/App.tsx", "import Cart from './cart';\n"), f("src/cart.ts", "export const x = 1;\n")]
    assert "export par défaut" in find_import_problems(files)[0]
    files[1] = f("src/cart.ts", "export * from './other';\n")
    assert find_import_problems(files) == []


def test_export_list_with_alias_counts_as_export():
    files = [f("src/a.ts", "import { B } from './b';\n"), f("src/b.ts", "const x = 1;\nexport { x as B };\n")]
    assert find_import_problems(files) == []


def test_unresolved_relative_import_needs_a_directory_listing_and_stays_silent_on_unknown():
    files = [f("src/App.tsx", "import Header from './components/Header';\n")]
    assert find_import_problems(files) == []  # sans listing : jamais de signalement
    assert find_import_problems(files, list_dir=lambda d: None) == []  # inconnu : jamais de signalement
    problems = find_import_problems(files, list_dir=lambda d: {"App.tsx"} if d == "src" else set())
    assert len(problems) == 1 and "./components/Header" in problems[0]
    assert find_import_problems(files, list_dir=lambda d: {"Header.tsx"}) == []  # existe déjà dans le repo


def test_index_resolution_and_js_suffix_and_assets():
    files = [f("src/App.tsx", "import './App.css';\nimport u from './utils/index';\nimport x from './x.js';\n"),
             f("src/utils/index.ts", "export default 1;\n"), f("src/x.ts", "export default 2;\n")]
    assert find_import_problems(files, list_dir=lambda d: {"App.css"}) == []


def test_context_files_resolve_imports_without_being_checked():
    files = [f("src/App.tsx", "import { a } from './lib';\n")]
    context = [f("src/lib.ts", "export const a = 1;\n")]
    assert find_import_problems(files, context_files=context) == []
    assert find_import_problems(files, list_dir=lambda d: set()) != []  # sans contexte : introuvable


def test_legacy_imports_of_an_edited_file_are_not_checked_only_the_added_ones():
    base = "import Legacy from './legacy/Missing';\nconst a = 1;\n"
    listing = lambda d: {"x.ts"} if d == "src" else set()  # noqa: E731
    clean = review_diagnostic_output(
        edit("src/x.ts", ("const a = 1;", "const a = 2;")), read_base=lambda p: (base, None), list_dir=listing
    )
    assert clean[1] is None
    added = review_diagnostic_output(
        edit("src/x.ts", ("const a = 1;", "import Nouveau from './nouveau/Absent';\nconst a = 2;")),
        read_base=lambda p: (base, None), list_dir=listing,
    )
    assert added[1] and "./nouveau/Absent" in added[1] and "legacy" not in added[1]


def test_edited_file_exports_are_taken_from_the_complete_resolved_content():
    base_lib = "export const other = 1;\n"
    review = review_diagnostic_output(
        edit("src/lib.ts", ("export const other = 1;", "export const other = 1;\nexport const helper = 2;"))
        + block("src/App.tsx", "import { helper } from './lib';\nexport default 1;\n"),
        read_base=lambda p: (base_lib, None),
    )
    assert review[1] is None


def test_destructured_exports_count_as_exports():
    files = [f("src/a.ts", "import { x, renamed, first } from './b';\n"),
             f("src/b.ts", "export const { x, y: renamed } = obj;\nexport const [first, second] = list;\n")]
    assert find_import_problems(files) == []
