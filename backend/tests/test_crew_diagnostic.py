"""Garde-fou du diagnostic et commit des fichiers de l'Analyste : modifications ciblées, imports, reprise, fichiers retirés."""

import pytest


cq = pytest.importorskip("crewquestion")
import crew_tools
import crew_guardrails
import crew_workspace
from crew_support import output, new_crew, _edit_block


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


def test_unresolved_import_is_flagged_with_real_listing_semantics(tmp_path):
    from analyst_imports import find_import_problems
    (tmp_path / "src").mkdir()
    (tmp_path / "src/App.tsx").write_text("x")
    files = [{"path": "src/Main.tsx", "content": "import Header from './components/Header';\n"}]
    problems = find_import_problems(files, list_dir=lambda d: crew_workspace._list_local_dir(tmp_path, d))
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
    # Depuis l'exécution 139 : App.tsx importe un nom que cart.ts n'exporte pas (le build échouerait) → il n'est plus committé.
    assert {f["path"] for f in crew._analyst_files} == {"src/cart.ts"} and "imports cassés" in crew._not_extracted["src/App.tsx"]


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
    assert not crew_guardrails._withdrawn_paths("Fichiers src/b.ts — NON réalisés : aucun", ["src/b.ts"])


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

    monkeypatch.setattr(crew_tools, "write_files_to_branch", lambda *a, **k: "ERREUR : non fast-forward")
    tool.run(commit_message="m")
    assert "commit refusé" in crew._write_rejections["src/a.ts"]

    def partial_success(owner, repo, branch, message, files, sink):
        sink["package.json"] = "ERREUR_SYNTAXE : JSON invalide"
        return "OK : 1 fichier(s) écrit(s)"

    monkeypatch.setattr(crew_tools, "write_files_to_branch", partial_success)
    tool.run(commit_message="m")
    assert crew._write_rejections == {"package.json": "ERREUR_SYNTAXE : JSON invalide"}


def test_only_successful_commits_are_cached_and_errors_explain_how_to_retry(monkeypatch):
    crew = new_crew(owner="o", repo="r")
    crew._analyst_files = [{"path": "src/a.ts", "content": "export {};\n"}]
    commit, qa = crew._build_commit_analyst_files_tool(), crew._build_qa_verify_tool()
    assert commit.cache_function(None, "OK : 1 fichier") is True
    assert commit.cache_function(None, "ERREUR : non fast-forward") is False
    assert qa.cache_function() is False
    monkeypatch.setattr(crew_tools, "write_files_to_branch", lambda *a, **k: "ERREUR : non fast-forward")
    assert "commit_message légèrement différent" in commit.run(commit_message="m")
    monkeypatch.setattr(crew_tools, "write_files_to_branch", lambda *a, **k: "OK : 1 fichier(s)")
    assert "légèrement différent" not in commit.run(commit_message="m (2e essai)")


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


# --- exécution 139 : un fichier dont les imports ne mènent à rien n'est PAS committé -------------------------------------------

def _file_block(path: str, body: str) -> str:
    return f"<<<FICHIER: {path}>>>\n{body}\n<<<FIN_FICHIER>>>\n"


APP_MISSING = "import { GameStatsPanel } from './components/GameStatsPanel';\nexport default function App() { return GameStatsPanel; }"
GOOD_COMPONENT = "export const GameStatsPanel = 1;\nexport const other = 2;\nexport const third = 3;"


def _crew_with_empty_repo():
    crew = new_crew()
    crew._base_readers = (lambda path: (None, "FILE_ABSENT"), lambda directory: set())     # aucun fichier existant : tout import doit être livré
    return crew


def test_an_accepted_answer_with_a_missing_import_target_is_not_committed_and_is_listed_as_not_delivered():
    crew = _crew_with_empty_repo()
    answer = output(_file_block("src/App.tsx", APP_MISSING))
    ok, message = crew._diagnostic_guardrail(answer)
    assert not ok and "GameStatsPanel" in message                                       # 1er passage : refus avec le défaut
    ok, result = crew._diagnostic_guardrail(answer)                                     # 2e passage : accepté, mais App.tsx n'est pas committable
    assert ok and crew._analyst_files == []
    assert "imports cassés" in crew._not_extracted["src/App.tsx"] and "exclu du commit" in result


def test_files_whose_imports_resolve_are_kept():
    crew = _crew_with_empty_repo()
    answer = output(_file_block("src/App.tsx", APP_MISSING) + _file_block("src/components/GameStatsPanel.tsx", GOOD_COMPONENT))
    ok, _ = crew._diagnostic_guardrail(answer)
    assert ok and {f["path"] for f in crew._analyst_files} == {"src/App.tsx", "src/components/GameStatsPanel.tsx"}
    assert crew._not_extracted == {}


def test_dropping_a_file_cascades_to_the_files_that_depended_on_it():
    crew = _crew_with_empty_repo()
    page = "import { Panel } from './Panel';\nexport const Page = Panel;\nexport const x = 1;"
    panel = "import { nothing } from './Missing';\nexport const Panel = nothing;\nexport const y = 2;"
    answer = output(_file_block("src/Page.tsx", page) + _file_block("src/Panel.tsx", panel))
    crew._diagnostic_guardrail(answer)
    ok, _ = crew._diagnostic_guardrail(answer)
    assert ok and crew._analyst_files == [] and set(crew._not_extracted) == {"src/Page.tsx", "src/Panel.tsx"}


def test_an_unknown_directory_never_drops_a_file():
    crew = new_crew()
    crew._base_readers = (lambda path: (None, "FILE_ABSENT"), lambda directory: None)  # panne GitHub : dans le doute, on ne retire rien
    answer = output(_file_block("src/App.tsx", APP_MISSING))
    ok, _ = crew._diagnostic_guardrail(answer)
    assert ok and [f["path"] for f in crew._analyst_files] == ["src/App.tsx"]


def test_a_targeted_edit_is_judged_on_the_added_text_only_not_on_imports_already_in_the_original_file():
    crew = new_crew()
    original = "import { old } from './legacy';\nexport const a = 1;\nexport const b = old;\n"
    crew._base_readers = (lambda path: (original, None) if path == "src/a.ts" else (None, "ABSENT"), lambda directory: set())
    answer = output(                                                                       # './legacy' manque, mais il était DÉJÀ dans le fichier d'origine
        _edit_block("src/a.ts", "export const a = 1;", "export const a = 2;")
        + _file_block("src/b.ts", "import { z } from './nope';\nexport const b = z;\nexport const c = 1;")     # défaut réel : force le chemin « accepté malgré le problème »
    )
    assert crew._diagnostic_guardrail(answer)[0] is False
    ok, _ = crew._diagnostic_guardrail(answer)
    assert ok and [f["path"] for f in crew._analyst_files] == ["src/a.ts"] and set(crew._not_extracted) == {"src/b.ts"}


# --- revue de l'exclusion des imports cassés ----------------------------------------------------------------------------------

import analyst_imports  # noqa: E402


@pytest.mark.parametrize("spec, present", [
    ("./styles", "src/styles.scss"), ("./styles", "src/styles.less"), ("./util", "src/util.mjs"), ("./Panel", "src/Panel.vue"),
    ("./icon.svg?react", "src/icon.svg"), ("./data.json?url", "src/data.json"), ("./font.woff2", "src/font.woff2"), ("./sound.mp3", "src/sound.mp3"),
])
def test_valid_relative_imports_outside_the_basic_extensions_are_never_reported(spec, present):
    files = [{"path": "src/App.tsx", "content": f"import x from '{spec}';\nexport default x;\nexport const a = 1;"}]
    names = {present.rsplit("/", 1)[1]}
    assert analyst_imports.files_with_import_problems(files, lambda directory: names) == {}


def test_a_withdrawn_file_that_was_already_imported_makes_the_importers_from_a_previous_attempt_broken():
    """Tentative 1 : A importe B, tout est livré. Tentative 2 : B retiré (« NON réalisé »), rien d'autre n'est renvoyé → le contrôle ne portait plus que
    sur les fichiers de la tentative en cours : A restait commité avec un import sans cible."""
    crew = _crew_with_empty_repo()
    first = output(_file_block("src/A.tsx", "import { b } from './B';\nexport const a = b;\nexport const z = 1;")
                   + _file_block("src/B.ts", "export const b = 1;\nexport const c = 2;\nexport const d = 3;"))
    assert crew._diagnostic_guardrail(first)[0] is True
    second = output("src/B.ts NON réalisé : abandonné.")
    ok, message = crew._diagnostic_guardrail(second)
    assert not ok and "src/A.tsx" in message and "'./B'" in message
    ok, result = crew._diagnostic_guardrail(second)
    assert ok and crew._analyst_files == [] and "src/A.tsx" in crew._not_extracted


def test_dropping_an_edited_existing_file_breaks_its_importers_for_the_names_it_added():
    crew = new_crew()
    store = "export const old = 1;\nexport const keep = 2;\nexport const more = 3;\n"
    crew._base_readers = (lambda path: (store, None) if path == "src/store.ts" else (None, "ABSENT"), lambda directory: {"store.ts"})
    answer = output(
        _edit_block("src/store.ts", "export const old = 1;", "import { nope } from './absent';\nexport const old = 1;\nexport const newThing = nope;")
        + _file_block("src/use.ts", "import { newThing } from './store';\nexport const used = newThing;\nexport const extra = 1;")
    )
    assert crew._diagnostic_guardrail(answer)[0] is False
    ok, _ = crew._diagnostic_guardrail(answer)
    assert ok and crew._analyst_files == [] and set(crew._not_extracted) == {"src/store.ts", "src/use.ts"}
    assert "'newThing'" in crew._not_extracted["src/use.ts"] and "retiré du commit" in crew._not_extracted["src/use.ts"]


def test_an_importer_of_names_the_original_file_already_exports_is_kept_when_the_edit_is_dropped():
    crew = new_crew()
    store = "export const old = 1;\nexport const keep = 2;\nexport const more = 3;\n"
    crew._base_readers = (lambda path: (store, None) if path == "src/store.ts" else (None, "ABSENT"), lambda directory: {"store.ts"})
    answer = output(
        _edit_block("src/store.ts", "export const old = 1;", "import { nope } from './absent';\nexport const old = 1;")
        + _file_block("src/use.ts", "import { keep } from './store';\nexport const used = keep;\nexport const extra = 1;")
    )
    crew._diagnostic_guardrail(answer)
    ok, _ = crew._diagnostic_guardrail(answer)
    assert ok and [f["path"] for f in crew._analyst_files] == ["src/use.ts"]


def test_a_stale_edit_scope_never_hides_a_broken_import_of_a_file_later_delivered_in_full():
    crew = _crew_with_empty_repo()
    crew._edit_scope = {"src/a.ts": "export const a = 2;"}                              # reste d'une tentative où a.ts n'était qu'une modification
    answer = output(_file_block("src/a.ts", "import { x } from './nowhere';\nexport const a = x;\nexport const b = 1;"))
    ok, message = crew._diagnostic_guardrail(answer)
    assert not ok and "'./nowhere'" in message and "src/a.ts" not in crew._edit_scope
    crew._edit_scope = {"src/a.ts": "stale"}
    crew._reset_for_replay({"diagnostic"})
    assert crew._edit_scope == {}


def test_the_reason_lists_every_missing_import_and_the_plan_gap_is_recomputed_after_the_drop():
    crew = _crew_with_empty_repo()
    crew._architecture_task_ref = type("T", (), {"output": type("O", (), {"raw": (
        "## Fichiers à créer ou modifier\n\n- MODIFIER src/App.tsx : racine\n- CRÉER src/A.tsx : a\n- CRÉER src/B.tsx : b\n- CRÉER src/C.tsx : c\n")})()})()
    app = "import { A } from './A';\nimport { B } from './B';\nimport { C } from './C';\nexport default function App() { return A + B + C; }"
    a = "export const A = 1;\nexport const a2 = 2;\nexport const a3 = 3;"
    answer = output(_file_block("src/App.tsx", app) + _file_block("src/A.tsx", a))
    assert crew._diagnostic_guardrail(answer)[0] is False                              # 2 fichiers sur 4 livrés : le plan n'est pas en cause, les imports si
    ok, result = crew._diagnostic_guardrail(answer)
    reason = crew._not_extracted["src/App.tsx"]
    assert ok and "'./B'" in reason and "'./C'" in reason and "src/App.tsx :" not in reason
    assert [f["path"] for f in crew._analyst_files] == ["src/A.tsx"]
    assert "1 fichier(s) du plan de l'Architecte sur 4" in result                      # écart dit APRÈS le retrait d'App.tsx


def test_directories_are_listed_only_once_across_the_cascade_passes():
    calls = []

    def lister(directory):
        calls.append(directory)
        return set()
    import analyst_import_drop
    merged = {                                                                              # tour 1 : F1 (cible absente) ; tour 2 : G (import PAR DÉFAUT de F1, retiré)
        "src/F1.tsx": {"path": "src/F1.tsx", "content": "import { n } from './Missing';\nexport default n;\nexport const y = 2;"},
        "src/G.tsx": {"path": "src/G.tsx", "content": "import F1 from './F1';\nexport const G = F1;\nexport const z = 3;"},
    }
    analyst_import_drop.drop_files_with_broken_imports(merged, {}, lister, lambda path: (None, "ABSENT"), {})
    assert merged == {} and len(calls) > 0 and len(calls) == len(set(calls))
