import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import crewquestion as cq  # noqa: E402
from analyst_output import build_delivery_report  # noqa: E402
from qa_report import (  # noqa: E402
    declared_verdict,
    has_blocking_problems,
    normalize_verdict,
    parse_criteria,
    qa_report_issues,
    reconcile_verdict,
    required_verdict,
)

TABLE = """## Critères d'acceptation
| Critère | Statut | Preuve | Correctif suggéré |
|---|---|---|---|
| AC-F1-1 total avec remise | OK | `src/cart.ts:12` [vérifié outil] | |
| AC-F1-2 panier vide | KO | `src/cart.ts:30` retourne NaN | Retourner 0 quand items est vide dans total() |
| AC-F1-3 export CSV | NON VÉRIFIABLE | Pas d'environnement d'exécution ; test manuel : ouvrir la page et cliquer sur Exporter | |
## Problèmes bloquants
- aucun
## Problèmes mineurs
- nommage
Verdict : GO
"""


def kwargs(**overrides):
    base = dict(delivery_gaps={}, analyst_file_count=2, committed_count=2, commit_tool_used=True)
    base.update(overrides)
    return base


@pytest.mark.parametrize("token, expected", [
    ("GO", "GO"), ("go", "GO"), ("NO_GO", "NO_GO"), ("NO GO", "NO_GO"), ("NON GO", "NO_GO"),
    ("GO_AVEC_RESERVES", "GO_AVEC_RESERVES"), ("GO avec réserves", "GO_AVEC_RESERVES"),
])
def test_normalize_verdict(token, expected):
    assert normalize_verdict(token) == expected


def test_declared_verdict_uses_the_last_mention():
    assert declared_verdict("| Verdict | GO |\n...\nVerdict : NO_GO") == "NO_GO"
    assert declared_verdict("rien") is None


def test_parse_criteria_reads_status_proof_and_fix_and_skips_headers():
    rows = parse_criteria(TABLE)
    assert [r["status"] for r in rows] == ["OK", "KO", "NV"]
    assert rows[1]["proof"].startswith("`src/cart.ts:30`") and rows[1]["fix"].startswith("Retourner 0")
    assert rows[2]["fix"] == ""


def test_parse_criteria_ignores_tables_outside_the_criteria_section():
    raw = "## Fichiers\n| Fichier | Statut | Preuve |\n| a.ts | OK | x |\n## Critères d'acceptation\n| c | KO | p | f |\n"
    assert [r["criterion"] for r in parse_criteria(raw)] == ["c"]


def test_has_blocking_problems_ignores_none_values():
    assert has_blocking_problems("## Problèmes bloquants\n- aucun\n## Suite\nx") is False
    assert has_blocking_problems("## Problèmes bloquants\nAucune.\n") is False
    assert has_blocking_problems("## Problèmes bloquants\n- Import cassé\n") is True
    assert has_blocking_problems("rien") is False


def test_blocking_section_stops_at_following_plain_text_headings_and_the_verdict_line():
    assert has_blocking_problems("## Problèmes bloquants\nAucun\nVerdict : GO") is False
    assert has_blocking_problems("## Problèmes bloquants\nAucun\nProblèmes mineurs : nommage\nVerdict : GO") is False
    assert has_blocking_problems("## Problèmes bloquants\n- Import cassé\nVerdict : NO_GO") is True


def test_required_verdict_is_driven_by_facts():
    assert required_verdict(TABLE, **kwargs())[0] == "NO_GO"  # un KO
    ok_table = "## Critères d'acceptation\n| c | OK | p | |\n## Problèmes bloquants\nAucun\nVerdict : GO"
    assert required_verdict(ok_table, **kwargs()) == ("GO", [])
    verdict, reasons = required_verdict(ok_table, **kwargs(delivery_gaps={"src/big.ts": "trop gros"}))
    assert verdict == "GO_AVEC_RESERVES" and "src/big.ts" in reasons[0]
    nv = ok_table.replace("| OK |", "| NON VÉRIFIABLE |")
    assert required_verdict(nv, **kwargs())[0] == "GO_AVEC_RESERVES"
    assert required_verdict(ok_table.replace("Aucun", "- Import cassé"), **kwargs())[0] == "NO_GO"
    assert required_verdict("pas de tableau\nVerdict : GO", **kwargs())[0] == "GO_AVEC_RESERVES"


def test_nothing_committed_is_no_go_only_when_the_commit_tool_was_used():
    ok_table = "## Critères d'acceptation\n| c | OK | p | |\nVerdict : GO"
    assert required_verdict(ok_table, **kwargs(committed_count=0))[0] == "NO_GO"
    assert required_verdict(ok_table, **kwargs(committed_count=0, commit_tool_used=False))[0] == "GO"
    assert required_verdict(ok_table, **kwargs(committed_count=0, analyst_file_count=0))[0] == "GO"


def test_reconcile_verdict_rewrites_only_more_lenient_mentions_in_place():
    raw, origin = reconcile_verdict("| Verdict | GO |\nTexte\nVerdict : GO", "NO_GO")
    assert origin == "GO" and raw == "| Verdict | NO_GO |\nTexte\nVerdict : NO_GO"
    same, origin = reconcile_verdict("Verdict : NO_GO", "GO_AVEC_RESERVES")
    assert origin is None and same == "Verdict : NO_GO"  # plus sévère : conservé
    assert reconcile_verdict("Verdict : GO", "GO")[1] is None


def test_report_issues_flag_missing_proof_fix_and_manual_test():
    assert qa_report_issues(TABLE) == []
    bad = TABLE.replace("`src/cart.ts:30` retourne NaN", "ça plante").replace(
        "Retourner 0 quand items est vide dans total()", "").replace(
        "Pas d'environnement d'exécution ; test manuel : ouvrir la page et cliquer sur Exporter", "inconnu")
    issues = qa_report_issues(bad)
    assert any("sans preuve localisée" in i for i in issues)
    assert any("sans correctif suggéré" in i for i in issues)
    assert any("sans raison ni test manuel" in i for i in issues)
    assert "Aucun tableau" in qa_report_issues("Verdict : GO")[0]


# --- Rapport de livraison : périmètre et imports ----------------------------------------------

def test_delivery_report_appends_scope_and_import_sections():
    files = [{"path": "src/a.ts", "content": "export const a = 1;\n"}]
    fetch = lambda path: ("export const a = 1;\n", None)  # noqa: E731
    report = build_delivery_report(files, fetch, scope_notes=["Fichiers livrés HORS du plan : src/x.ts"], import_notes=[])
    assert "### Périmètre (plan de l'Architecte) [vérifié outil]\n- Fichiers livrés HORS du plan : src/x.ts" in report
    assert "Aucune incohérence détectée" in report
    clean = build_delivery_report(files, fetch, scope_notes=[], import_notes=["src/a.ts : import cassé"])
    assert "Conforme" in clean and "- src/a.ts : import cassé" in clean
    plain = build_delivery_report(files, fetch)
    assert "Périmètre" not in plain and "imports" not in plain


# --- Plan de l'Architecte et périmètre --------------------------------------------------------

PLAN = """## Fichiers à créer ou modifier
- CRÉER src/App.tsx : racine
- MODIFIER `src/hooks/useCart.ts` : panier
## Couverture
- CRÉER src/ignore.ts : hors section
"""


def test_planned_paths_reads_only_the_files_section():
    assert cq._planned_paths(PLAN) == {"src/App.tsx", "src/hooks/useCart.ts"}
    assert cq._planned_paths("") == set()


def test_scope_notes_flag_unplanned_and_missing_files_and_stay_silent_without_plan():
    planned = {"src/App.tsx", "src/hooks/useCart.ts"}
    notes = cq._scope_notes(planned, {"src/App.tsx", "src/extra.ts"}, set())
    assert any("HORS du plan" in n and "src/extra.ts" in n for n in notes)
    assert any("PRÉVUS" in n and "src/hooks/useCart.ts" in n for n in notes)
    assert cq._scope_notes(planned, {"src/App.tsx", "src/hooks/useCart.ts"}, set()) == []
    assert cq._scope_notes(planned, {"src/App.tsx"}, {"src/hooks/useCart.ts"}) == []  # déjà signalé non livré
    assert cq._scope_notes(set(), {"a.ts"}, set()) == []


# --- Guardrail et outil de vérification du crew -----------------------------------------------

def qa_crew(commit_tool_used=True):
    crew = cq.AppDevelopmentCrew()
    crew._reset_execution_state({"repo_owner": "o", "repo_name": "r", "work_branch": "crewai/x"})
    crew._commit_tool_used = commit_tool_used
    return crew


def out(raw):
    return type("O", (), {"raw": raw})()


def test_guardrail_downgrades_a_lenient_verdict_and_explains_why():
    crew = qa_crew()
    crew._analyst_files = [{"path": "src/a.ts", "content": "x"}]
    crew._committed_paths = {"src/a.ts"}
    ok, report = crew._qa_report_guardrail(out(TABLE))
    assert ok is True and "Verdict : NO_GO" in report and "Verdict : GO\n" not in report
    assert "## Contrôle automatique du rapport QA" in report and "Verdict ajusté de GO à NO_GO" in report


def test_guardrail_leaves_a_consistent_report_untouched():
    crew = qa_crew()
    crew._analyst_files = [{"path": "src/a.ts", "content": "x"}]
    crew._committed_paths = {"src/a.ts"}
    good = "## Critères d'acceptation\n| c | OK | `a.ts:1` ok | |\n## Problèmes bloquants\nAucun\nVerdict : GO"
    task_output = out(good)
    ok, result = crew._qa_report_guardrail(task_output)
    assert ok is True and result is task_output


def test_guardrail_uses_delivery_gaps_and_keeps_a_stricter_verdict():
    crew = qa_crew()
    crew._analyst_files = [{"path": "src/a.ts", "content": "x"}]
    crew._committed_paths = {"src/a.ts"}
    crew._not_extracted = {"src/big.ts": "trop volumineux"}
    good = "## Critères d'acceptation\n| c | OK | `a.ts:1` ok | |\n## Problèmes bloquants\nAucun\nVerdict : GO"
    _, report = crew._qa_report_guardrail(out(good))
    assert "Verdict : GO_AVEC_RESERVES" in report and "src/big.ts" in report
    _, strict = crew._qa_report_guardrail(out(good.replace("Verdict : GO", "Verdict : NO_GO")))
    strict_text = getattr(strict, "raw", strict)
    assert "Verdict : NO_GO" in strict_text and "Verdict ajusté" not in strict_text


def test_guardrail_still_adds_the_missing_verdict_marker_and_never_retries():
    crew = qa_crew()
    ok, report = crew._qa_report_guardrail(out("rapport sans conclusion"))
    assert ok is True and "Verdict : NON FOURNI" in report
    assert crew.qa_task().guardrail_max_retries == 0


def test_qa_verify_tool_reports_scope_and_import_problems(monkeypatch):
    crew = qa_crew()
    crew._analyst_files = [
        {"path": "src/App.tsx", "content": "import { total } from './cart';\nexport default 1;\n"},
        {"path": "src/cart.ts", "content": "export const sum = 1;\n"},
        {"path": "src/extra.ts", "content": "export const e = 1;\n"},
    ]
    architecture = crew.architecture_task()
    architecture.output = type("Out", (), {"raw": "## Fichiers à créer ou modifier\n- MODIFIER src/App.tsx : racine\n- CRÉER src/cart.ts : panier\n- CRÉER src/other.ts : prévu\n"})()
    contents = {f["path"]: f["content"] for f in crew._analyst_files}
    monkeypatch.setattr(cq, "make_file_fetcher", lambda owner, repo, branch: (lambda path: (contents.get(path), None)))
    monkeypatch.setattr(cq, "make_dir_lister", lambda owner, repo, branch: (lambda directory: {"App.tsx", "cart.ts", "extra.ts"}))
    report = crew._build_qa_verify_tool().run()
    assert "HORS du plan" in report and "src/extra.ts" in report
    assert "PRÉVUS" in report and "src/other.ts" in report
    assert "'total' est importé depuis './cart'" in report


def test_qa_verify_tool_checks_only_added_imports_of_edited_files(monkeypatch):
    crew = qa_crew()
    crew._analyst_files = [{"path": "src/x.ts", "content": "import Legacy from './legacy/Missing';\nconst a = 2;\n"}]
    crew._edit_scope = {"src/x.ts": "const a = 2;"}
    monkeypatch.setattr(cq, "make_file_fetcher", lambda owner, repo, branch: (lambda path: (crew._analyst_files[0]["content"], None)))
    monkeypatch.setattr(cq, "make_dir_lister", lambda owner, repo, branch: (lambda directory: {"x.ts"} if directory == "src" else set()))
    report = crew._build_qa_verify_tool().run()
    assert "Aucune incohérence détectée" in report and "legacy" not in report
