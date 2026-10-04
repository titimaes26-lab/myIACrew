"""Cas limites des modules analyst_* : balises à chemin invalide, fin de ligne, imports hors dépôt, diff tronqué."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analyst_blocks  # noqa: E402
import analyst_imports  # noqa: E402
from analyst_output import build_delivery_report, review_diagnostic_output  # noqa: E402

TRUNCATED = "balise <<<FIN_FICHIER>>> manquante : contenu probablement tronqué"
OK_FILE = "<<<FICHIER: src/a.ts>>>\nexport const a = 1;\n<<<FIN_FICHIER>>>\n"


def test_an_unterminated_block_with_an_invalid_path_is_reported_not_silently_dropped():
    files, broken = analyst_blocks.parse_file_sections("<<<FICHIER: ../../x>>>\ncontenu\n")
    assert files == [] and broken == {"../../x": TRUNCATED}


def test_an_invalid_path_block_cut_by_the_next_opening_is_reported_and_the_next_file_is_kept():
    files, broken = analyst_blocks.parse_file_sections("<<<FICHIER: ../../x>>>\nc\n" + OK_FILE)
    assert [f["path"] for f in files] == ["src/a.ts"] and broken == {"../../x": TRUNCATED}


def test_a_malformed_marker_inside_an_invalid_path_block_reports_both():
    files, broken = analyst_blocks.parse_file_sections("<<<FICHIER: ../../x>>>\nc\n<<<FICHIER mal>>>\nz\n<<<FIN_FICHIER>>>\n")
    assert files == []
    assert broken["../../x"].startswith("balise mal formée rencontrée avant") and "<<<FICHIER mal>>>" in broken


def test_file_content_always_ends_with_a_newline_unless_empty():
    text = "<<<FICHIER: a.ts>>>\nsans retour\n<<<FIN_FICHIER>>>\n<<<FICHIER: b.ts>>>\n<<<FIN_FICHIER>>>\n"
    files, broken = analyst_blocks.parse_file_sections(text)
    assert broken == {} and {f["path"]: f["content"] for f in files} == {"a.ts": "sans retour\n", "b.ts": ""}


def test_an_import_leaving_the_repository_root_resolves_to_nothing():
    assert analyst_imports._import_candidates("a.ts", "../x") == []
    assert analyst_imports._import_candidates("src/a.ts", "../x")        # reste dans le dépôt : des candidats existent


def test_no_file_found_is_reported_unless_the_analyst_declared_nothing_delivered_without_any_code():
    flagged = lambda text: "Aucun fichier exploitable" in (review_diagnostic_output(text)[1] or "")  # noqa: E731
    assert flagged("Voici ma réponse sans balise.")                              # ni fichier ni marqueur
    assert not flagged("Rien à livrer : NON réalisé — trop volumineux.")         # choix assumé
    assert flagged("NON réalisé, mais voici du code :\n```ts\nconst a = 1;\n```")  # du code hors balises reste un problème de format


def test_a_long_diff_is_truncated_with_the_number_of_hidden_lines():
    expected = "\n".join(f"ligne {i}" for i in range(100)) + "\n"
    actual = "\n".join(f"autre {i}" for i in range(100)) + "\n"
    long_report = build_delivery_report([{"path": "src/a.ts", "content": expected}], lambda path: (actual, None))
    assert "DIVERGENT" in long_report and "lignes de diff supplémentaires)" in long_report
    short = build_delivery_report([{"path": "src/a.ts", "content": "a\n"}], lambda path: ("b\n", None))
    assert "DIVERGENT" in short and "supplémentaires" not in short
