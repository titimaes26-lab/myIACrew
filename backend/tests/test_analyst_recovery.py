"""Fichiers livrés dans un bloc ``` sans balises <<<FICHIER>>> (exécution 139) : récupérés seulement sans ambiguïté."""
import pytest

import analyst_output
import analyst_recovery

CODE = "import React from 'react';\nconst a = 1;\nexport default a;"


def _answer(intro: str, code: str = CODE, lang: str = "tsx") -> str:
    return f"### Rapport\n- **Besoin** : x\n\n{intro}\n\n```{lang}\n{code}\n```\n\n**Task status**: Completed successfully."


def test_the_code_of_execution_139_is_recovered_from_its_untagged_block():
    text = _answer("Voici le code mis à jour de `src/App.tsx` (et le cas échéant l'intégration dans les vues de jeu si nécessaire) :")
    assert analyst_recovery.untagged_files(text) == [{"path": "src/App.tsx", "content": CODE + "\n"}]


@pytest.mark.parametrize("intro", [
    "Voici `src/App.tsx` et `src/main.tsx` :",                  # deux chemins : ambigu
    "Voici le code mis à jour :",                              # aucun chemin
    "Voici le fichier README.md :",                            # extension non code
    "Voir http://example.com/x : ",                            # pas un fichier
])
def test_an_ambiguous_or_pathless_block_is_never_guessed(intro):
    assert analyst_recovery.untagged_files(_answer(intro)) == []


def test_a_short_snippet_or_a_path_described_by_two_blocks_is_ignored():
    assert analyst_recovery.untagged_files(_answer("Voici `src/App.tsx` :", code="const a = 1;")) == []
    two = _answer("Voici `src/App.tsx` :") + "\n\nEncore `src/App.tsx` :\n\n```tsx\n" + CODE + "\n```\n"
    assert analyst_recovery.untagged_files(two) == []


def test_two_distinct_files_are_both_recovered():
    other = "export const b = 2;\nexport const c = 3;\nexport const d = 4;"
    text = _answer("Fichier `src/a.ts` :", CODE, "ts") + "\n\nFichier `src/b.ts` :\n\n```ts\n" + other + "\n```\n"
    assert {f["path"] for f in analyst_recovery.untagged_files(text)} == {"src/a.ts", "src/b.ts"}


def test_review_recovers_untagged_code_only_when_there_is_no_tagged_file():
    files, issue, _, broken = analyst_output.review_diagnostic_output(_answer("Voici `src/App.tsx` :"))
    assert [f["path"] for f in files] == ["src/App.tsx"] and not broken and not (issue or "").startswith("Aucun fichier")
    tagged = "<<<FICHIER: src/x.ts>>>\n" + CODE + "\n<<<FIN_FICHIER>>>\n\n" + _answer("Voici `src/App.tsx` :")
    files, *_ = analyst_output.review_diagnostic_output(tagged)
    assert [f["path"] for f in files] == ["src/x.ts"]                       # un fichier balisé existe : rien n'est récupéré


def test_without_any_path_the_refusal_stays_and_it_explains_that_a_code_block_is_not_a_file():
    files, issue, _, _ = analyst_output.review_diagnostic_output(_answer("Voici le code :"))
    assert files == [] and "Aucun fichier exploitable" in issue and "pas de ```" in issue
    assert "\n<<<FICHIER" not in issue                                      # aucune balise en début de ligne : l'écho du refus ne ressemble pas à un fichier
