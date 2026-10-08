"""Cohérence des imports relatifs des fichiers livrés."""


from analyst_output import review_diagnostic_output
from analyst_imports import find_import_problems
from analyst_support import block, edit, f


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
