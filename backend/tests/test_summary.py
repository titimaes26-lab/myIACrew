import crew_summary  # noqa: E402
from summary import delivery_facts, fallback_summary

DIAG = "Analyste Diagnostic Technique"
QA = "QA Engineer / Automated Tester"
FILES = "<<<FICHIER: src/a.ts>>>\nexport const a = 1\n<<<FIN FICHIER>>>\n<<<FICHIER: src/b.ts>>>\nx\n<<<FIN FICHIER>>>\n"
EDIT = "<<<MODIFICATION: src/c.ts>>>\n<<<<<<< CHERCHER\na\n=======\nb\n>>>>>>> REMPLACER\n<<<FIN MODIFICATION>>>\n"


def test_facts_from_code_only():
    assert delivery_facts([], "BUGFIX") == ""
    facts = delivery_facts([("Architecte Logiciel", "plan"), (QA, "Verdict : GO_AVEC_RESERVES")], "FEATURE", "PETIT")
    assert "type : FEATURE (petite)" in facts and "2 étapes terminées" in facts
    assert "verdict QA : GO avec réserves" in facts


def test_facts_without_files_or_verdict():
    facts = delivery_facts([("Architecte Logiciel", "plan")])
    assert facts == "**Faits :** 1 étape terminée"


def test_fallback_uses_first_meaningful_line_per_agent():
    text = fallback_summary([("Architecte", "# Titre\n\n**Plan global** en trois blocs\nsuite"), ("QA", "---\n```\n")])
    assert text.startswith("- **Architecte** : Plan global en trois blocs\n")
    assert "**QA**" not in text  # section sans texte exploitable ignorée
    assert "Résumé automatique" in text


def test_fallback_empty_when_nothing_usable():
    assert fallback_summary([("QA", "# seulement un titre")]) == ""


def test_prompt_demande_trois_blocs_sans_chiffres():
    prompt = crew_summary._build_summary_prompt("demande", "résultat")
    for title in ("### Ce qui a été fait", "### Pourquoi ces choix", "### À faire ensuite"):
        assert title in prompt
    assert "ne les répète pas" in prompt


def test_compose_body_prefers_model_summary_and_keeps_facts():
    sections = [("Architecte", "Plan"), (QA, "Verdict : GO")]
    body = crew_summary._compose_summary_body(sections, "ANALYSE_ONLY", None, "### Ce qui a été fait\nX")
    assert body.startswith("**Faits :**") and body.endswith("X") and "Résumé automatique" not in body


def test_compose_body_falls_back_without_model_summary():
    body = crew_summary._compose_summary_body([("Architecte", "Plan global")], "ANALYSE_ONLY", None, None)
    assert "**Faits :**" in body and "- **Architecte** : Plan global" in body and "Résumé automatique" in body


def test_compose_body_empty_without_sections_or_summary():
    assert crew_summary._compose_summary_body([], "BUGFIX", None, None) == ""


def test_facts_count_files_and_edits_by_unique_path():
    assert "2 fichiers produits" in delivery_facts([(DIAG, FILES)])
    assert "1 fichier produit" in delivery_facts([(DIAG, EDIT)])
    assert "3 fichiers produits" in delivery_facts([(DIAG, FILES + EDIT)])


def test_facts_report_unusable_file_without_closing_tag():
    facts = delivery_facts([(DIAG, "<<<FICHIER: src/a.ts>>>\nx\n")])
    assert "1 inexploitable" in facts


def test_fallback_skips_markers_and_raw_html():
    text = fallback_summary([(DIAG, FILES + "\nCorrection du bouton"), ("Architecte", "<div>x</div>\nPlan simple")])
    assert "<<<" not in text and "<div>" not in text
    assert "Correction du bouton" in text and "Plan simple" in text


def test_fallback_ignores_unclosed_code_block():
    text = fallback_summary([(DIAG, "Intro utile\n<<<FICHIER: src/a.ts>>>\nexport const a = 1\n")])
    assert "Intro utile" in text and "export const" not in text
    assert "export const" not in fallback_summary([(DIAG, "<<<FICHIER: src/a.ts>>>\nexport const a = 1\n")])


def test_partial_work_block_lists_agents_without_markers():
    from summary import PARTIAL_WORK_MARKER, partial_work_block
    block = partial_work_block([("Architecte", "## Architecte\n\nPlan simple"), (DIAG, FILES)])
    lines = block.split("\n")
    assert lines[0] == PARTIAL_WORK_MARKER and lines[1] == "2 étapes terminées avant l'échec"
    assert "- Architecte : Plan simple" in lines and f"- {DIAG}" in lines
    assert "<<<" not in block


def test_partial_work_block_empty_without_sections():
    from summary import partial_work_block
    assert partial_work_block([]) == ""
