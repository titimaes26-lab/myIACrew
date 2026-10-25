"""Réécritures en temps linéaire (revue n°16) : mêmes résultats que les anciennes expressions quadratiques, cas limites compris.
Les valeurs attendues viennent des ANCIENNES versions ; une comparaison aléatoire (> 400 000 cas, 0 différence) les a validées en plus."""
import pytest

import analyst_blocks
import analyst_imports
import crew_guardrails
import delivery
import qa_report

START, END = "<<<FICHIER: x.ts>>>", "<<<FIN_FICHIER>>>"


# --- blocs de fichiers ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    (f"a\n{START}\ncorps\n{END}\nb", "a\n\nb"),
    (f"{START}\n1\n{END}\nentre\n{START}\n2\n{END}", "\nentre\n"),
    (f"avant\n{START}\ncorps sans fin", f"avant\n{START}\ncorps sans fin"),                      # début sans fin : rien n'est retiré
    (f"{START}\n1\n{END}\n{START}\nsans fin", f"\n{START}\nsans fin"),                         # le 1er bloc part, le 2e (sans fin) reste
    (f"  {START}\n  - src/b.ts : NON réalisé\n  {END} suite\nreste", "\nreste"),
    ("rien à retirer", "rien à retirer"), ("", ""),
    (f"{END}\n{START}\nx", f"{END}\n{START}\nx"),                                              # une fin sans début ne ferme rien
])
def test_file_blocks_are_removed_whole_and_only_when_closed(text, expected):
    assert analyst_blocks.strip_file_blocks(text) == expected


def test_a_code_fence_is_found_after_any_number_of_blank_lines():
    assert analyst_blocks.ANY_FENCE.search("\n" * 5 + "   ```ts\ncode")
    assert analyst_blocks.ANY_FENCE.search("\n\n ~~~")
    assert not analyst_blocks.ANY_FENCE.search("\n\n  texte ```")


# --- imports ------------------------------------------------------------------------------------------------------------------------

def test_relative_imports_are_all_found():
    content = "import a from './a'\nimport type { B } from '../b'\nimport * as c from './c'\nimport d, { e } from './d'\n\n\n  import './side'\nimport x from 'pkg'\n"
    froms = [(m.group(3)) for m in analyst_imports._IMPORT_FROM.finditer(content)]
    assert froms == ["./a", "../b", "./c", "./d"]
    assert [m.group(1) for m in analyst_imports._IMPORT_SIDE_EFFECT.finditer(content)] == ["./side"]
    assert [m.groups() for m in analyst_imports._IMPORT_FROM.finditer("import type from './t'\nimport from './u'")] == [(None, None, "./t"), (None, None, "./u")]


# --- plan de l'Architecte -----------------------------------------------------------------------------------------------------------

def test_architecture_file_lines_are_all_found():
    text = "## Fichiers\n- CRÉER `src/a.ts` : nouveau\n\n\n  * **MODIFIER** src/b.ts : x\n-\n CRÉER src/c.ts\n- Autre chose\n"
    assert [m.group(1) for m in crew_guardrails._ARCH_FILE_LINE.finditer(text)] == ["`src/a.ts` : nouveau", "src/b.ts : x", "src/c.ts"]


# --- enveloppe de la demande --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("prompt,expected", [
    ("Demande initiale : corrige le bug\nType d'exécution : BUGFIX\nPrécisions apportées : Aucune.", "corrige le bug"),
    ("Demande initiale : corrige le bug\nType d'exécution : BUGFIX\nPrécisions apportées : sur la page A", "corrige le bug — Précisions : sur la page A"),
    ("Demande initiale :\nType d'exécution : BUGFIX", ""),                                                  # demande vide : reste vide
    ("Demande initiale: \nType d'exécution\nDemande initiale : x\n Type d'exécution : B", "Type d'exécution\nDemande initiale : x"),
    ("Demande initiale : ligne 1\n\n\n   ligne 2\n\n   Type d'exécution : BUGFIX", "ligne 1\n\n\n   ligne 2"),
    ("Demande initiale : sans enveloppe de type", "sans enveloppe de type"),
    ("Demande initiale : a Précisions apportées : b", "a — Précisions : b"),
    ("pas d'enveloppe du tout", "pas d'enveloppe du tout"), ("  Demande initiale : x   ", "x"), ("Demande initiale  \n  :", ""), ("", ""),
])
def test_the_user_request_is_extracted_from_the_envelope(prompt, expected):
    assert delivery.extract_user_request(prompt) == expected


@pytest.mark.parametrize("text,changed", [
    ("fixes #12", True), ("Closes   #4", True), ("resolved org/repo#9", True), ("fix : #3", True), ("fix:  #5", True), ("fix\n#6", True),
    ("fix:#5", False), ("fixing #7", False), ("prefix #8", False), ("fix #", False),
])
def test_closing_keywords_are_neutralised_in_the_same_forms_as_before(text, changed):
    assert (delivery.sanitize_for_github(text) != text) is changed


# --- rapport QA ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("cell,expected", [
    ("OK (heuristique)", "OK"), ("✅ **OK**", "OK"), ("KO", "KO"), ("NON VÉRIFIABLE (manuel)", "NV"), ("OK ((a)", "OK"),
    ("OK (a) b", None), ("Statut OK/KO", None), ("OK (a))", None), ("OK  (x)  ", "OK"),
])
def test_status_cells_tolerate_a_trailing_parenthesis(cell, expected):
    assert qa_report._status_of(cell) == expected


@pytest.mark.parametrize("proof,found", [
    ("src/App.tsx:12", True), ("voir a.b:3", True), ("ligne 4", True), ("Ligne  5", True), ("x.y:z", False), (".ts:9", False),
    ("a.toolong:1", False), ("a.ts:", False), ("chemin\\fichier.js:7", True), ("aucun repère", False),
])
def test_file_line_references_are_recognised(proof, found):
    assert bool(qa_report._FILE_LINE_REF.search(proof)) is found


@pytest.mark.parametrize("value,none", [("Aucune", True), ("  néant ", True), ("- rien", True), ("rien du tout", False), ("-", True), ("— ", True), ("n/a", True), ("**Aucun**", True), ("fix it", False), ("", True)])
def test_empty_none_values_are_recognised(value, none):
    assert bool(qa_report._NONE_VALUE.match(value)) is none
