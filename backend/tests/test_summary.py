import crewquestion as cq
from summary import delivery_facts, fallback_summary

DIAG = "Analyste Diagnostic Technique"
QA = "QA Engineer / Automated Tester"
FILES = "<<<FILE: src/a.ts>>>\nexport const a = 1\n<<<END_FILE>>>\n"


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
    prompt = cq._build_summary_prompt("demande", "résultat")
    for title in ("### Ce qui a été fait", "### Pourquoi ces choix", "### À faire ensuite"):
        assert title in prompt
    assert "ne les répète pas" in prompt


def test_compose_body_prefers_model_summary_and_keeps_facts():
    sections = [("Architecte", "Plan"), (QA, "Verdict : GO")]
    body = cq._compose_summary_body(sections, "ANALYSE_ONLY", None, "### Ce qui a été fait\nX")
    assert body.startswith("**Faits :**") and body.endswith("X") and "Résumé automatique" not in body


def test_compose_body_falls_back_without_model_summary():
    body = cq._compose_summary_body([("Architecte", "Plan global")], "ANALYSE_ONLY", None, None)
    assert "**Faits :**" in body and "- **Architecte** : Plan global" in body and "Résumé automatique" in body


def test_compose_body_empty_without_sections_or_summary():
    assert cq._compose_summary_body([], "BUGFIX", None, None) == ""
