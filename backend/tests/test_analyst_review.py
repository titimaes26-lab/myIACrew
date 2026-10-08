"""Contrôle d'une sortie du diagnostic : rapport de livraison, modifications ciblées résolues en fichiers complets."""


from analyst_output import build_delivery_report, review_diagnostic_output
from analyst_blocks import FILE_ABSENT, PRESENT_UNREADABLE
from analyst_edits import apply_edits, parse_edit_sections
from analyst_support import block, edit


def test_output_without_files_is_rejected_unless_declared_not_delivered():
    assert review_diagnostic_output("Voici mon analyse sans code.")[1] is not None
    assert review_diagnostic_output("- big.tsx : NON réalisé (trop volumineux)")[1] is None
    # Du code hors balises : le marqueur "non réalisé" ne suffit plus.
    unmarked = "Fichiers NON réalisés : aucun\n### Fichier : src/App.tsx\n```tsx\nexport {}\n```\n"
    assert review_diagnostic_output(unmarked)[1] is not None


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


def test_not_delivered_none_is_not_a_deliberate_non_delivery():
    assert review_diagnostic_output("Fichiers NON réalisés : aucun")[1] is not None


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
