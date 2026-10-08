"""Suivi des échecs répétés d'édition : remise à zéro par un succès, comptage par fichier."""

import pytest


pytest.importorskip("github_write")  # saute le fichier si PyGithub/crewai ne sont pas installés
import github_edit_failures


def test_an_edit_success_resets_the_consecutive_failure_count_of_that_file():
    with github_edit_failures.track_edit_failures():
        first = github_edit_failures._record_edit_failure("o", "r", "a.ts", "b", "raison.", "relis.")
        github_edit_failures._record_edit_success("o", "r", "a.ts", "b")
        after_success = github_edit_failures._record_edit_failure("o", "r", "a.ts", "b", "raison.", "relis.")
        second = github_edit_failures._record_edit_failure("o", "r", "a.ts", "b", "raison.", "relis.")
    assert "ÉCHEC RÉPÉTÉ" not in first and "ÉCHEC RÉPÉTÉ" not in after_success   # le succès intermédiaire a remis le compte à 0
    assert "ÉCHEC RÉPÉTÉ (2x DE SUITE)" in second


def test_failures_are_counted_per_file_and_forgotten_outside_the_tracking_scope():
    with github_edit_failures.track_edit_failures():
        github_edit_failures._record_edit_failure("o", "r", "a.ts", "b", "raison.", "relis.")
        other = github_edit_failures._record_edit_failure("o", "r", "b.ts", "b", "raison.", "relis.")
    assert "ÉCHEC RÉPÉTÉ" not in other
    # Hors de la portée d'une exécution : jamais escaladé (rien ne permet de compter).
    for _ in range(3):
        assert "ÉCHEC RÉPÉTÉ" not in github_edit_failures._record_edit_failure("o", "r", "a.ts", "b", "raison.", "relis.")
