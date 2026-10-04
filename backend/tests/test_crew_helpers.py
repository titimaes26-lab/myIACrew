import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GEMINI_API_KEY", "test")

cq = pytest.importorskip("crewquestion")


def output(raw):
    return type("O", (), {"raw": raw})()


def new_crew(owner="", repo="", branch="feature/x"):
    crew = cq.AppDevelopmentCrew()
    crew._reset_execution_state({"repo_owner": owner, "repo_name": repo, "work_branch": branch})
    return crew


@pytest.mark.parametrize("text, expected", [
    ("Verdict final (après revue complète) : GO", "GO"),
    ("**Verdict** : NO GO", "NO GO"),
    ("Verdict : ✅ GO", "GO"),
    ("Verdict : GO avec réserves", "GO avec réserves"),
    ("Verdict : `NO_GO`", "NO_GO"),
    ("## Verdict\n\n**GO**", "GO"),
    ("Verdict\nGO_AVEC_RESERVES", "GO_AVEC_RESERVES"),
    ("Verdict — GO", "GO"),
    ("Verdict : NON GO", "NON GO"),
    ("Verdict : GO ✅", "GO"),
    ("Verdict : NO_GO, 3 bloquants", "NO_GO"),
    ("Verdict : NO_GO car les tests échouent", "NO_GO"),
    ("Verdict : GO avec réserves mineures", "GO"),
    ("Verdict : GO\r\n", "GO"),
    ("Verdict : go", "go"),
    ("Verdict : Go ✅", "Go"),
    ("Verdict : No go, 2 bloquants", "No go"),
    ("| Verdict | GO |", "GO"),
    ("Verdict (2 échecs mineurs | tolérés) : GO", "GO"),
])
def test_qa_verdict_variants(text, expected):
    match = cq.QA_VERDICT.search(text)
    assert (match.group("eol") or match.group("upper")) == expected


def test_qa_guardrail_adds_missing_verdict():
    ok, out = cq._qa_verdict_guardrail(output("rapport sans conclusion"))
    assert ok and "NON FOURNI" in out


def test_coerce_analysis_report_fixes_common_model_mistakes():
    report = cq._coerce_analysis_report(
        {"request_type": "bugfix", "confidence": 85, "alternative_type": "AUCUNE", "is_clear": True}
    )
    assert report.request_type == "BUGFIX"
    assert report.confidence == pytest.approx(0.85)
    assert report.alternative_type is None
    assert cq._coerce_analysis_report({"summary": "sans type"}) is None
    assert cq._coerce_analysis_report({"request_type": "FEATURE", "confidence": 8}).confidence == pytest.approx(0.8)


def test_is_clear_string_false_is_false():
    report = cq._coerce_analysis_report({"request_type": "FEATURE", "confidence": 0.8, "is_clear": "false"})
    assert report.is_clear is False


def test_confidence_is_required_in_structured_output():
    with pytest.raises(Exception):
        cq.AnalysisReport(summary="s", request_type="FEATURE", is_clear=True)


def test_qualification_fallback_is_flagged_outside_llm_schema():
    result = cq.QualificationResult(summary="s", request_type="BUGFIX", confidence=0.0, is_clear=True)
    assert result.fallback is False
    assert "fallback" not in cq.AnalysisReport.model_json_schema()["properties"]


def test_low_confidence_forces_clarification_question():
    report = cq._enforce_confidence_threshold(cq.AnalysisReport(
        summary="s", request_type="FEATURE", alternative_type="BUGFIX", confidence=0.4, is_clear=True,
    ))
    assert not report.is_clear and "correction d'un bug" in report.questions[0]


def test_qualification_prompt_contains_examples_context_and_repo_line():
    with_repo = cq._build_qualification_prompt("corrige ça", "Tour 1 : ajout d'un filtre", has_repo_target=True)
    without_repo = cq._build_qualification_prompt("corrige ça")
    assert cq.QUALIFICATION_EXAMPLES in with_repo
    assert "Tour 1 : ajout d'un filtre" in with_repo
    assert "Un repository GitHub cible est fourni." in with_repo
    assert "Aucun repository GitHub cible n'est fourni." in without_repo
    assert "Aucun échange précédent." in without_repo


def test_qualification_examples_use_valid_request_types():
    for line in cq.QUALIFICATION_EXAMPLES.splitlines():
        if "->" in line:
            assert line.split("->")[1].split()[0].strip(",") in cq._REQUEST_TYPES


def test_qualify_endpoint_forwards_has_repo_target(monkeypatch):
    import asyncio
    import schemas
    import routes_execute

    calls = []

    async def fake_analyze(user_request, conversation_context, has_repo_target):
        calls.append((user_request, conversation_context, has_repo_target))
        return cq.QualificationResult(summary="s", request_type="BUGFIX", confidence=0.9, is_clear=True)

    monkeypatch.setattr(routes_execute.crew_instance, "analyze_user_request", fake_analyze)
    monkeypatch.setattr(routes_execute.crew_instance, "save_analysis_report", lambda *a, **k: None)

    asyncio.run(routes_execute.qualify_request(schemas.UserRequestInput(user_request="x", has_repo_target=True), {"id": "u"}))
    asyncio.run(routes_execute.qualify_request(schemas.UserRequestInput(user_request="y"), {"id": "u"}))
    assert [c[2] for c in calls] == [True, False]


def test_qualification_prompt_asks_for_repo_form_not_chat():
    prompt = cq._build_qualification_prompt("corrige le bug")
    assert "Repository cible" in prompt and "« local »" in prompt


GOOD_SPEC = """## Besoin
x
## Utilisateurs
y
## Fonctionnalités (MoSCoW)
- F1 [Must] Ajouter une tâche
- F2 [Should] Filtrer
## Règles et cas limites
z
## Critères d'acceptation
- AC-F1-1 Étant donné une liste vide / Quand j'ajoute / Alors elle s'affiche en moins de 1 seconde
## Hypothèses retenues
h
"""


def test_design_spec_issues_complete_spec_has_none():
    assert cq._design_spec_issues(GOOD_SPEC) == []


def test_design_spec_issues_flags_must_without_criterion():
    issues = cq._design_spec_issues(GOOD_SPEC.replace("- F2 [Should] Filtrer", "- F2 [Must] Filtrer"))
    assert any("F2 [Must]" in i for i in issues) and not any("F1 [Must]" in i for i in issues)


def test_design_spec_issues_flags_vague_criterion_but_not_measurable_one():
    vague = cq._design_spec_issues(GOOD_SPEC + "- AC-F1-2 Alors l'écran s'affiche rapidement\n")
    assert any("Critère vague" in i for i in vague)
    assert cq._design_spec_issues(GOOD_SPEC + "- AC-F1-3 Alors l'écran s'affiche rapidement en moins de 2 s\n") == []


def test_design_spec_issues_flags_missing_sections():
    issues = cq._design_spec_issues("## Besoin\nseulement ça")
    assert any("Utilisateurs" in i for i in issues) and any("Hypothèses retenues" in i for i in issues)


def test_design_spec_issues_accepts_typographic_apostrophe():
    assert cq._design_spec_issues(GOOD_SPEC.replace("d'acceptation", "d\u2019acceptation")) == []


def test_design_spec_issues_accepts_alternative_id_formats():
    spec = GOOD_SPEC.replace("- F1 [Must] Ajouter une tâche", "- [Must] **F1** Ajouter une tâche").replace("AC-F1-1", "AC-F1.1")
    assert cq._design_spec_issues(spec) == []
    missing = cq._design_spec_issues(spec.replace("AC-F1.1", "critère"))
    assert any("F1 [Must]" in i for i in missing)


def test_design_spec_issues_context_digit_does_not_excuse_vague_outcome():
    spec = GOOD_SPEC + "- AC-F1-2 Étant donné 3 tâches / Quand je filtre / Alors l'affichage est fluide\n"
    assert any("Critère vague" in i for i in cq._design_spec_issues(spec))
    ok = GOOD_SPEC + "- AC-F1-2 Étant donné 3 tâches / Quand je filtre / Alors l'affichage prend moins de 200 ms\n"
    assert cq._design_spec_issues(ok) == []


def test_design_spec_guardrail_never_fails_and_annotates_only_on_issues():
    class Out:
        raw = GOOD_SPEC
    out = Out()
    ok, result = cq._design_spec_guardrail(out)
    assert ok is True and result is out
    Out.raw = "## Besoin\nx"
    ok, result = cq._design_spec_guardrail(Out())
    assert ok is True and "## Contrôle automatique des specs" in result


def test_design_task_yaml_placeholders_are_known():
    import pathlib
    import string
    import yaml
    tasks = yaml.safe_load((pathlib.Path(cq.__file__).parent / "tasksquestion.yaml").read_text(encoding="utf-8"))
    known = {"user_request", "conversation_context", "repo_instructions", "repo_owner", "repo_name", "base_branch", "repo_snapshot", "previous_plan"}
    for name in ("design_task", "architecture_task", "diagnostic_task", "development_task"):
        fields = {f[1] for f in string.Formatter().parse(tasks[name]["description"]) if f[1]}
        assert fields <= known | {"work_branch"}, name


def test_architecture_prompt_keeps_its_static_instructions_before_the_variable_data():
    # Préfixe stable (consignes fixes) puis données variables : le cache de préfixe du fournisseur ne sert que si
    # RIEN de variable ne précède les consignes.
    import pathlib
    import string
    import yaml
    tasks = yaml.safe_load((pathlib.Path(cq.__file__).parent / "tasksquestion.yaml").read_text(encoding="utf-8"))
    description = tasks["architecture_task"]["description"]
    marker = "=== Données de cette demande ==="
    assert description.count(marker) == 1
    static, variable = description.split(marker)
    assert not [f[1] for f in string.Formatter().parse(static) if f[1]]
    assert {f[1] for f in string.Formatter().parse(variable) if f[1]} == {
        "user_request", "conversation_context", "repo_instructions", "repo_snapshot", "previous_plan"}
    assert "APERÇU DU REPOSITORY" in static


def test_architecture_prompt_stays_short_and_still_names_every_required_section():
    import pathlib
    import yaml
    tasks = yaml.safe_load((pathlib.Path(cq.__file__).parent / "tasksquestion.yaml").read_text(encoding="utf-8"))
    task = tasks["architecture_task"]
    assert len(task["description"].split()) <= 520
    text = task["description"] + task["expected_output"]
    for heading in cq.ARCHITECTURE_REQUIRED_HEADINGS:
        assert heading in text, heading
    assert "Ne propose jamais un paquet déjà couvert" in task["description"] and "200 lignes" in task["description"]
    assert "signatures seules" in task["description"] and "=== Données de cette demande ===" in task["description"]
    # Le format lu par le garde-fou (« - CRÉER|MODIFIER <chemin> : <rôle> ») doit rester demandé.
    assert "- CRÉER|MODIFIER <chemin> : <rôle" in task["description"]


def test_only_the_architect_output_is_capped_and_make_llm_omits_the_cap_by_default():
    assert cq.architect_llm.max_tokens == 8192
    for llm in (cq.qualification_llm, cq.designer_llm, cq.diagnostic_llm, cq.developer_llm, cq.qa_llm):
        assert llm.max_tokens is None
    assert cq._make_llm(0.1).max_tokens is None
    assert cq._make_llm(0.1, max_tokens=123).max_tokens == 123


GOOD_ARCH = """## Existant
Projet vide.
## Cible
src/ avec composants.
## Décisions
Décision : useState | Alternative écartée : Redux | Pourquoi : simple
## Dépendances à ajouter
aucune
## Contrats d'interface
- src/App.tsx : export default function App(): JSX.Element
- src/hooks/useCart.ts : export function useCart(): { items: Item[] }
## Fichiers à créer ou modifier
- CRÉER src/App.tsx : composant racine
- MODIFIER src/hooks/useCart.ts : logique du panier
## Couverture
| Exigence | Fichier(s) |
| F1 | src/App.tsx |
## Risques
- Taille : découper
"""


@pytest.mark.parametrize("env, expected", [(None, 8192), ("6000", 6000), ("abc", 8192), ("100", 8192), ("", 8192)])
def test_architect_output_cap_is_configurable_with_a_safe_default(monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("ARCHITECT_MAX_OUTPUT_TOKENS", raising=False)
    else:
        monkeypatch.setenv("ARCHITECT_MAX_OUTPUT_TOKENS", env)
    assert cq._architect_max_tokens() == expected


def test_architecture_issues_flag_a_plan_cut_by_the_token_limit():
    empty_last = GOOD_ARCH.replace("- Taille : découper\n", "")
    assert any("tronquée" in i for i in cq._architecture_issues(empty_last))
    cut = GOOD_ARCH.rstrip() + "\n- Dépendance : paquet,"
    assert any("tronquée" in i for i in cq._architecture_issues(cut))
    unclosed = GOOD_ARCH + "```ts\nexport const x ="
    assert any("tronquée" in i for i in cq._architecture_issues(unclosed))


def test_architecture_issues_complete_plan_has_none():
    assert cq._architecture_issues(GOOD_ARCH) == []


def test_architecture_issues_flags_missing_sections_and_empty_file_list():
    issues = cq._architecture_issues("## Existant\nrien")
    assert any("Cible" in i for i in issues) and any("Aucune ligne" in i for i in issues)


def test_architecture_issues_flags_malformed_duplicate_and_outside_paths():
    plan = GOOD_ARCH.replace(
        "- MODIFIER src/hooks/useCart.ts : logique du panier",
        "- MODIFIER src/hooks/useCart.ts : logique du panier\n- CRÉER src/App.tsx : doublon\n"
        "- CRÉER ../evil.ts : hors projet\n- CRÉER src/x.ts sans rôle",
    )
    issues = cq._architecture_issues(plan)
    assert any("plusieurs fois" in i for i in issues)
    assert any("hors du projet" in i for i in issues)
    assert any("mal formée" in i for i in issues)


def test_architecture_issues_flags_code_file_without_contract():
    plan = GOOD_ARCH.replace("- MODIFIER src/hooks/useCart.ts : logique du panier", "- MODIFIER src/hooks/useCart.ts : logique du panier\n- CRÉER src/utils/format.ts : formats")
    issues = cq._architecture_issues(plan)
    assert any("src/utils/format.ts n'a pas de contrat" in i for i in issues)
    assert not any("useCart" in i for i in issues)


def test_architecture_guardrail_never_fails_and_annotates_only_on_issues():
    class Out:
        raw = GOOD_ARCH
    out = Out()
    ok, result = cq._architecture_guardrail(out)
    assert ok is True and result is out
    Out.raw = "## Existant\nrien"
    ok, result = cq._architecture_guardrail(Out())
    assert ok is True and "## Contrôle automatique de l'architecture" in result


def test_architecture_issues_ignores_prose_bullets_outside_file_list():
    plan = GOOD_ARCH.replace("src/ avec composants.", "- Modifier App.tsx pour brancher le panier\n- Créer un hook useCart : état du panier")
    assert cq._architecture_issues(plan) == []


def test_architecture_issues_contract_section_survives_later_mentions_of_contrat():
    plan = GOOD_ARCH + "- Un contrat d'API flou entre composants : figer les props\n"
    assert cq._architecture_issues(plan) == []


def test_architecture_issues_contract_match_is_exact_path_not_substring():
    plan = GOOD_ARCH.replace("src/hooks/useCart.ts : export", "src/hooks/useCart.tsx : export")
    assert any("src/hooks/useCart.ts n'a pas de contrat" in i for i in cq._architecture_issues(plan))


def test_architecture_issues_accepts_bold_and_backticked_entries():
    plan = GOOD_ARCH.replace("- CRÉER src/App.tsx : composant racine", "- **CRÉER** `src/App.tsx` : composant racine")
    assert cq._architecture_issues(plan) == []


def test_architecture_task_has_a_non_retrying_guardrail():
    task = new_crew().architecture_task()
    assert task.guardrail is not None and task.guardrail_max_retries == 0


def _edit_block(path, search, replace):
    return (f"<<<MODIFICATION: {path}>>>\n<<<<<<< CHERCHER\n{search}\n=======\n{replace}\n"
            ">>>>>>> REMPLACER\n<<<FIN_MODIFICATION>>>\n")


def test_guardrail_resolves_a_targeted_edit_into_a_complete_committable_file():
    crew = new_crew()
    crew._base_readers = (lambda path: ("const a = 1;\nconst b = 2;\n", None), lambda d: None)
    ok, _ = crew._diagnostic_guardrail(output(_edit_block("src/x.ts", "const b = 2;", "const b = 3;")))
    assert ok and crew._analyst_files == [{"path": "src/x.ts", "content": "const a = 1;\nconst b = 3;\n"}]
    assert not crew._not_extracted


def test_guardrail_refuses_once_when_the_search_text_is_not_in_the_original():
    crew = new_crew()
    crew._base_readers = (lambda path: ("const a = 1;\n", None), lambda d: None)
    ok, message = crew._diagnostic_guardrail(output(_edit_block("src/x.ts", "inexistant", "x")))
    assert not ok and "introuvable" in message
    ok, _ = crew._diagnostic_guardrail(output(_edit_block("src/x.ts", "inexistant", "x")))
    assert ok and not crew._analyst_files and "src/x.ts" in crew._not_extracted


def _fake_fetcher(content=None, error=None, branch_missing=False):
    def fetch(path):
        return content, error
    fetch.branch_missing = branch_missing
    return fetch


def _repo_crew():
    crew = new_crew(owner="o", repo="r", branch="crewai/x")
    crew._base_branch = "main"
    return crew


def test_base_source_uses_the_work_branch_when_it_exists(monkeypatch):
    crew = _repo_crew()
    made = {}

    def fake_fetcher(owner, repo, branch):
        made[branch] = True
        return _fake_fetcher(content=f"version de {branch}")

    monkeypatch.setattr(cq, "make_file_fetcher", fake_fetcher)
    monkeypatch.setattr(cq, "make_dir_lister", lambda owner, repo, branch: (lambda d: {branch}))
    assert crew._read_base_file("src/x.ts") == ("version de crewai/x", None)
    assert crew._list_base_dir("src") == {"crewai/x"} and "main" not in made


def test_base_source_falls_back_to_main_only_when_the_work_branch_does_not_exist(monkeypatch):
    crew = _repo_crew()
    monkeypatch.setattr(
        cq, "make_file_fetcher",
        lambda owner, repo, branch: _fake_fetcher(error="ERREUR : branche introuvable", branch_missing=True)
        if branch == "crewai/x" else _fake_fetcher(content="version de main"),
    )
    monkeypatch.setattr(cq, "make_dir_lister", lambda owner, repo, branch: (lambda d: {branch}))
    assert crew._read_base_file("src/x.ts") == ("version de main", None)
    assert crew._list_base_dir("src") == {"main"}


def test_transient_error_on_the_work_branch_never_falls_back_to_main(monkeypatch):
    crew = _repo_crew()
    monkeypatch.setattr(
        cq, "make_file_fetcher",
        lambda owner, repo, branch: _fake_fetcher(error="ERREUR_GITHUB : rate limit exceeded")
        if branch == "crewai/x" else _fake_fetcher(content="ANCIENNE version de main"),
    )
    monkeypatch.setattr(cq, "make_dir_lister", lambda owner, repo, branch: (lambda d: None))
    assert crew._read_base_file("src/x.ts") == (None, "ERREUR_GITHUB : rate limit exceeded")
    ok, message = crew._diagnostic_guardrail(output(_edit_block("src/x.ts", "a", "b")))
    assert not ok and "modification impossible" in message


def test_local_dir_listing_distinguishes_absent_directory_from_unknown(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/App.tsx").write_text("x")
    assert cq._list_local_dir(tmp_path, "src") == {"App.tsx"}
    assert cq._list_local_dir(tmp_path, "src/components/Header") == set()
    assert cq._list_local_dir(tmp_path, "src/App.tsx") == set()
    assert cq._list_local_dir(tmp_path, "../dehors") is None


def test_github_dir_lister_maps_404_to_empty_and_other_errors_to_unknown(monkeypatch):
    import github_tools as gt
    from github import GithubException

    class Item:
        def __init__(self, name):
            self.name = name

    class FakeRepo:
        def get_contents(self, directory, ref=None):
            if directory == "src":
                return [Item("App.tsx")]
            if directory == "src/Header":
                raise GithubException(404, {}, {})
            if directory == "file.txt":
                return Item("file.txt")
            raise GithubException(403, {}, {})

    monkeypatch.setattr(gt, "_get_repo", lambda owner, repo: FakeRepo())
    list_dir = gt.make_dir_lister("o", "r", "main")
    assert list_dir("src") == {"App.tsx"}
    assert list_dir("src/Header") == set() and list_dir("file.txt") == set()
    assert list_dir("quota") is None


def test_unresolved_import_is_flagged_with_real_listing_semantics(tmp_path):
    from analyst_output import find_import_problems
    (tmp_path / "src").mkdir()
    (tmp_path / "src/App.tsx").write_text("x")
    files = [{"path": "src/Main.tsx", "content": "import Header from './components/Header';\n"}]
    problems = find_import_problems(files, list_dir=lambda d: cq._list_local_dir(tmp_path, d))
    assert len(problems) == 1 and "./components/Header" in problems[0]


def test_guardrail_refuses_once_on_inconsistent_imports_then_accepts_with_a_note():
    crew = new_crew()
    crew._base_readers = (lambda path: (None, "ABSENT"), lambda d: None)
    bad = (
        "<<<FICHIER: src/App.tsx>>>\n```tsx\nimport { total } from './cart';\nexport default 1;\n```\n<<<FIN_FICHIER>>>\n"
        "<<<FICHIER: src/cart.ts>>>\n```ts\nexport const sum = 1;\n```\n<<<FIN_FICHIER>>>\n"
    )
    ok, message = crew._diagnostic_guardrail(output(bad))
    assert not ok and "imports" in message and "'total'" in message
    ok, result = crew._diagnostic_guardrail(output(bad))
    assert ok and "Incohérences entre fichiers" in result
    assert {f["path"] for f in crew._analyst_files} == {"src/App.tsx", "src/cart.ts"}


def test_local_writes_are_confined_to_workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/App.tsx").write_text("old\n")
    rejections = {}
    message = cq._write_files_locally(tmp_path, [
        {"path": "src/App.tsx", "content": "export {};\n"},
        {"path": "main.py", "content": "x = 1\n"},
        {"path": "../escape.py", "content": "x = 1\n"},
    ], rejections)
    assert (tmp_path / "src/App.tsx").read_text() == "export {};\n"
    assert (tmp_path / "main.py").exists()  # dans l'espace de travail, jamais le backend
    assert set(rejections) == {"../escape.py"} and "REJETÉS (1)" in message
    assert cq._read_local_file(tmp_path, "src/App.tsx") == ("export {};\n", None)


def test_each_conversation_has_its_own_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(cq, "LOCAL_WORKSPACE_DIR", tmp_path)
    a, b = cq._conversation_workspace("1"), cq._conversation_workspace("2")
    assert a != b and tmp_path in a.parents and tmp_path in b.parents


def test_local_mode_commit_read_and_qa_share_the_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(cq, "LOCAL_WORKSPACE_DIR", tmp_path)
    crew = new_crew(branch="claude/conv-1")
    crew._analyst_files = [{"path": "src/a.ts", "content": "export {};\n"}, {"path": "conf.json", "content": "{ invalide"}]
    assert "REJETÉS" in crew._build_commit_analyst_files_tool().run(commit_message="m")
    assert crew._build_local_read_tool().run(file_path="src/a.ts") == "export {};\n"
    report = crew._build_qa_verify_tool().run()
    assert "IDENTIQUE" in report and "NON LIVRÉ" in report


def test_empty_owner_from_llm_cannot_divert_a_github_run(monkeypatch):
    crew = new_crew(owner="o", repo="r", branch="feature/x")
    crew._analyst_files = [{"path": "src/a.ts", "content": "export {};\n"}]
    calls = []
    monkeypatch.setattr(cq, "write_files_to_branch", lambda *a, **k: calls.append(a[:3]) or "OK : 1")
    monkeypatch.setattr(cq, "_write_files_locally", lambda *a, **k: pytest.fail("écriture locale en mode GitHub"))
    crew._build_commit_analyst_files_tool().run(commit_message="m")
    assert calls == [("o", "r", "feature/x")]
    assert "github_read_file" in crew._build_local_read_tool().run(file_path="src/a.ts")


def test_diagnostic_guardrail_excludes_shortcut_files_on_final_accept():
    crew = new_crew()
    raw = output(
        "<<<FICHIER: a.ts>>>\n```ts\n// ... reste du code\n```\n<<<FIN_FICHIER>>>\n"
        "<<<FICHIER: b.ts>>>\n```ts\nexport const b = 1;\n```\n<<<FIN_FICHIER>>>\n"
    )
    assert crew._diagnostic_guardrail(raw)[0] is False
    ok, out = crew._diagnostic_guardrail(raw)
    assert ok and "a.ts : NON réalisé" in out
    assert [f["path"] for f in crew._analyst_files] == ["b.ts"]
    assert "a.ts" in crew._not_extracted


def test_guardrail_retry_keeps_healthy_files_from_first_attempt():
    crew = new_crew()
    first = output(
        "<<<FICHIER: a.ts>>>\n// ... reste du code\n<<<FIN_FICHIER>>>\n"
        "<<<FICHIER: b.ts>>>\nexport const b = 1;\n<<<FIN_FICHIER>>>\n"
    )
    retry = output("<<<FICHIER: a.ts>>>\nexport const a = 1;\n<<<FIN_FICHIER>>>\n")
    assert crew._diagnostic_guardrail(first)[0] is False
    assert crew._diagnostic_guardrail(retry)[0] is True
    assert sorted(f["path"] for f in crew._analyst_files) == ["a.ts", "b.ts"]
    assert crew._not_extracted == {}


def test_retry_that_withdraws_a_file_removes_it():
    crew = new_crew()
    first = output(
        "<<<FICHIER: src/old.ts>>>\nexport const o = 1;\n<<<FIN_FICHIER>>>\n"
        "<<<FICHIER: src/b.ts>>>\n// ...\n<<<FIN_FICHIER>>>\n"
    )
    retry = output(
        "Plan : src/old.ts : NON réalisé (remplacé par src/new.ts). Fichiers NON réalisés : aucun autre\n"
        "<<<FICHIER: src/new.ts>>>\nexport const n = 1;\n<<<FIN_FICHIER>>>\n"
        "<<<FICHIER: src/b.ts>>>\nexport const b = 1;\n<<<FIN_FICHIER>>>\n"
    )
    crew._diagnostic_guardrail(first)
    crew._diagnostic_guardrail(retry)
    assert sorted(f["path"] for f in crew._analyst_files) == ["src/b.ts", "src/new.ts"]
    assert "src/old.ts" in crew._not_extracted
    assert not cq._withdrawn_paths("Fichiers src/b.ts — NON réalisés : aucun", ["src/b.ts"])


def test_retry_message_repeats_the_architecture_context():
    crew = new_crew()
    arch = crew.architecture_task()
    arch.output = type("Out", (), {"raw": "- CRÉER src/cart.ts : panier"})()
    crew.diagnostic_task().context = [arch]
    ok, message = crew._diagnostic_guardrail(output("aucun fichier"))
    assert not ok and "CRÉER src/cart.ts" in message


def test_commit_tool_forbids_committing_excluded_files():
    crew = new_crew()
    crew._not_extracted = {"src/App.tsx": "contenu incomplet"}
    message = crew._build_commit_analyst_files_tool().run(commit_message="m")
    assert "Ne les committe JAMAIS" in message and "github_write_files" not in message


def test_github_rejections_are_tracked_per_latest_commit(monkeypatch):
    crew = new_crew(owner="o", repo="r")
    crew._analyst_files = [
        {"path": "package.json", "content": "{ invalide"},
        {"path": "src/a.ts", "content": "export {};\n"},
    ]
    tool = crew._build_commit_analyst_files_tool()

    monkeypatch.setattr(cq, "write_files_to_branch", lambda *a, **k: "ERREUR : non fast-forward")
    tool.run(commit_message="m")
    assert "commit refusé" in crew._write_rejections["src/a.ts"]

    def partial_success(owner, repo, branch, message, files, sink):
        sink["package.json"] = "ERREUR_SYNTAXE : JSON invalide"
        return "OK : 1 fichier(s) écrit(s)"

    monkeypatch.setattr(cq, "write_files_to_branch", partial_success)
    tool.run(commit_message="m")
    assert crew._write_rejections == {"package.json": "ERREUR_SYNTAXE : JSON invalide"}


def test_local_workspace_is_per_conversation_even_without_branch(monkeypatch, tmp_path):
    monkeypatch.setattr(cq, "LOCAL_WORKSPACE_DIR", tmp_path)
    a, b = cq.AppDevelopmentCrew(), cq.AppDevelopmentCrew()
    a._reset_execution_state({"work_branch": "", "conversation_id": "1"})
    b._reset_execution_state({"work_branch": "", "conversation_id": "2"})
    assert a._workspace != b._workspace


@pytest.mark.parametrize("text, path, expected", [
    ("- src/big.ts — NON réalisé : fichier trop volumineux", "src/big.ts", True),
    ("- src/a.ts réalisé ; src/b.ts NON réalisé (trop gros)", "src/a.ts", False),
    ("- src/a.ts réalisé ; src/b.ts NON réalisé (trop gros)", "src/b.ts", True),
    ("Fichiers src/b.ts — NON réalisés : aucun", "src/b.ts", False),
    ("- src/App.tsx : réécrit en entier (rien de NON réalisé)", "src/App.tsx", False),
    ("- src/App.tsx.bak NON réalisé", "src/App.tsx", False),
    ("- Dockerfile — NON réalisé (trop long)", "Dockerfile", True),
    ("- app/(auth)/page.tsx — NON réalisé", "app/(auth)/page.tsx", True),
    ("- src/[id].tsx — NON réalisé", "src/[id].tsx", True),
    ("- src/a.ts, src/b.ts — NON réalisés (trop gros)", "src/a.ts", True),
    ("- src/a.ts : migration React 18.2 NON réalisée", "src/a.ts", True),
    ("- src/utils/casino.ts piano NON réalisé", "src/utils/casino.ts", True),
    ("| src/a.ts | NON réalisé | trop gros |", "src/a.ts", True),
    ("- ./src/a.ts — NON réalisé", "src/a.ts", True),
    ("Fichiers src/a.ts — NON réalisé(s) : aucun", "src/a.ts", False),
    ("Fichiers src/a.ts — tâches NON réalisées : aucune", "src/a.ts", False),
    ("- src/a.ts réalisé, src/b.ts NON réalisé", "src/a.ts", False),
    ("<<<FICHIER: src/a.ts>>>\n// src/a.ts NON réalisé\n<<<FIN_FICHIER>>>", "src/a.ts", False),
    # Chemin APRÈS la mention (pas seulement avant) : "NON réalisé : <chemin> (raison)".
    ("NON réalisé : src/b.ts (trop volumineux)", "src/b.ts", True),
    # Ordre inversé "mot-clé : chemin" (pas "chemin mot-clé") : le mot positif "Réalisé"
    # réclame IMMÉDIATEMENT son propre chemin, qui ne doit pas se faire retirer par la
    # mention négative suivante sur la même ligne.
    ("Réalisé : src/a.ts. NON réalisé : src/b.ts", "src/a.ts", False),
    ("Réalisé : src/a.ts. NON réalisé : src/b.ts", "src/b.ts", True),
    # La raison entre parenthèses ne doit jamais être prise pour un 2e chemin retiré.
    ("- src/old.ts : NON réalisé (remplacé par src/new.ts)", "src/new.ts", False),
    ("- src/old.ts : NON réalisé (remplacé par src/new.ts)", "src/old.ts", True),
    # Plusieurs chemins réclamés par un mot positif ("Réalisé : a, c") ne sont pas
    # récupérables par une mention négative plus loin sur la même ligne.
    ("Réalisé : src/a.ts, src/c.ts. NON réalisé : src/b.ts (trop complexe).", "src/a.ts", False),
    ("Réalisé : src/a.ts, src/c.ts. NON réalisé : src/b.ts (trop complexe).", "src/b.ts", True),
    # Plusieurs chemins listés APRÈS une même mention négative sont tous retirés.
    ("NON réalisé : src/b.ts, src/d.ts (trop gros)", "src/b.ts", True),
    # Aucune ponctuation entre deux fichiers : pas de "réclamation" par erreur du 2e.
    ("src/a.ts réalisé src/b.ts NON réalisé", "src/b.ts", True),
])
def test_is_withdrawn(text, path, expected):
    assert (path in cq._withdrawn_paths(text, [path])) is expected


def test_is_withdrawn_with_full_candidate_set():
    # _withdrawn_paths est TOUJOURS appelée en production avec l'ensemble des chemins connus
    # (voir _diagnostic_guardrail : list(merged) + sorted(delivered_now)) — ces deux cas de
    # liste ne peuvent être jugés correctement qu'avec les DEUX chemins comme candidats : un
    # chemin absent des candidats ne peut pas être "traversé" pour continuer la liste.
    withdrawn = cq._withdrawn_paths(
        "Réalisé : src/a.ts, src/c.ts. NON réalisé : src/b.ts (trop complexe).",
        ["src/a.ts", "src/b.ts", "src/c.ts"],
    )
    assert withdrawn == {"src/b.ts"}
    withdrawn = cq._withdrawn_paths("NON réalisé : src/b.ts, src/d.ts (trop gros)", ["src/b.ts", "src/d.ts"])
    assert withdrawn == {"src/b.ts", "src/d.ts"}
    # Liste à la virgule après un mot positif dont le DERNIER élément est en réalité la cible
    # de la mention négative qui le suit directement (avec ou sans virgule) : seul le test avec
    # les DEUX chemins candidats exerce vraiment le "regard en avant" de _consume_adjacent_paths
    # (un seul candidat laisserait la recherche brute sur `clause` donner la même réponse pour
    # une mauvaise raison, sans jamais passer par ce mécanisme).
    assert cq._withdrawn_paths("Réalisé : src/a.ts, src/b.ts NON réalisé", ["src/a.ts", "src/b.ts"]) == {"src/b.ts"}
    assert cq._withdrawn_paths("Réalisé : src/a.ts src/b.ts NON réalisé", ["src/a.ts", "src/b.ts"]) == {"src/b.ts"}
    # Un SEUL chemin (pas de liste à la virgule) suivi directement d'une mention négative : le
    # garde-fou du "regard en avant" doit s'appliquer dès le 1er chemin, pas seulement à partir
    # du 2e élément d'une liste.
    assert cq._withdrawn_paths("Réalisé : src/a.ts NON réalisé", ["src/a.ts"]) == {"src/a.ts"}


@pytest.mark.parametrize("text", [
    "verdict: No go-live possible sans tests",
    "Le verdict ne peut pas être rendu :\nGo figure",
])
def test_incidental_verdict_mentions_are_not_verdicts(text):
    assert cq.QA_VERDICT.search(text) is None


def test_local_reads_normalize_dot_slash_but_stay_confined(monkeypatch, tmp_path):
    monkeypatch.setattr(cq, "LOCAL_WORKSPACE_DIR", tmp_path)
    crew = new_crew()
    (crew._workspace / "src").mkdir(parents=True)
    (crew._workspace / "src/a.ts").write_text("export {};\n")
    assert cq._read_local_file(crew._workspace, "./src/a.ts") == ("export {};\n", None)
    assert crew._build_local_read_tool().run(file_path="./src/a.ts") == "export {};\n"
    assert cq._read_local_file(crew._workspace, "../x")[0] is None


def test_only_successful_commits_are_cached_and_errors_explain_how_to_retry(monkeypatch):
    crew = new_crew(owner="o", repo="r")
    crew._analyst_files = [{"path": "src/a.ts", "content": "export {};\n"}]
    commit, qa = crew._build_commit_analyst_files_tool(), crew._build_qa_verify_tool()
    assert commit.cache_function(None, "OK : 1 fichier") is True
    assert commit.cache_function(None, "ERREUR : non fast-forward") is False
    assert qa.cache_function() is False
    monkeypatch.setattr(cq, "write_files_to_branch", lambda *a, **k: "ERREUR : non fast-forward")
    assert "commit_message légèrement différent" in commit.run(commit_message="m")
    monkeypatch.setattr(cq, "write_files_to_branch", lambda *a, **k: "OK : 1 fichier(s)")
    assert "légèrement différent" not in commit.run(commit_message="m (2e essai)")


def test_local_mode_without_extracted_files_never_suggests_github():
    message = new_crew()._build_commit_analyst_files_tool().run(commit_message="m")
    assert "github_write_files" not in message and "espace de travail local" in message
    github_message = new_crew(owner="o", repo="r")._build_commit_analyst_files_tool().run(commit_message="m")
    assert "github_write_files" in github_message


def test_local_write_with_every_file_rejected_is_an_error(tmp_path):
    message = cq._write_files_locally(tmp_path, [{"path": "a.json", "content": "{ invalide"}], {})
    assert message.startswith("ERREUR")


def test_file_both_delivered_and_withdrawn_is_sent_back_then_delivered_content_wins():
    crew = new_crew()
    raw = output(
        "- src/App.tsx : ajout du bouton d'export qui était NON réalisé dans la version précédente\n"
        "<<<FICHIER: src/App.tsx>>>\nexport const App = 1;\n<<<FIN_FICHIER>>>\n"
    )
    ok, message = crew._diagnostic_guardrail(raw)
    assert not ok and "src/App.tsx" in message
    ok, _ = crew._diagnostic_guardrail(raw)
    # Ambiguïté non levée : exclu et signalé, jamais committé en silence ni perdu sans trace.
    assert ok and crew._analyst_files == []
    assert "ambiguïté" in crew._not_extracted["src/App.tsx"]


def test_clarified_retry_commits_the_delivered_file():
    crew = new_crew()
    crew._diagnostic_guardrail(output(
        "- src/App.tsx : ajout du bouton d'export qui était NON réalisé avant\n"
        "<<<FICHIER: src/App.tsx>>>\nexport const App = 1;\n<<<FIN_FICHIER>>>\n"
    ))
    ok, _ = crew._diagnostic_guardrail(output(
        "- src/App.tsx : ajout du bouton d'export\n"
        "<<<FICHIER: src/App.tsx>>>\nexport const App = 1;\n<<<FIN_FICHIER>>>\n"
    ))
    assert ok and [f["path"] for f in crew._analyst_files] == ["src/App.tsx"]


def test_file_withdrawn_in_the_same_response_is_not_committed():
    crew = new_crew()
    raw = output(
        "- src/App.tsx — NON réalisé (trop volumineux)\n"
        "<<<FICHIER: src/App.tsx>>>\nexport const partial = 1;\n<<<FIN_FICHIER>>>\n"
        "<<<FICHIER: src/b.ts>>>\nexport const b = 1;\n<<<FIN_FICHIER>>>\n"
    )
    crew._diagnostic_guardrail(raw)
    ok, out = crew._diagnostic_guardrail(raw)
    assert ok and [f["path"] for f in crew._analyst_files] == ["src/b.ts"]
    assert "src/App.tsx" in crew._not_extracted and "src/App.tsx" in out


def test_qualification_examples_show_how_scope_is_filled():
    examples = cq.QUALIFICATION_EXAMPLES
    assert "scope PETIT" in examples and examples.count("scope GRAND") >= 2
