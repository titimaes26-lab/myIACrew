"""Attributs d'exécution partagés par les mixins de AppDevelopmentCrew (déclarés ici pour le typage ; posés par
CrewChecksMixin._reset_execution_state au début de chaque exécution)."""
from typing import Any, Callable


class CrewExecutionState:
    # Fichiers committables fusionnés au fil des tentatives de l'Analyste, et leurs lacunes / refus d'écriture.
    _analyst_files: list[dict[str, Any]]
    _not_extracted: dict[str, str]
    _write_rejections: dict[str, str]
    # Faits de livraison constatés par les OUTILS.
    _committed_paths: set[str]
    _edit_scope: dict[str, str]
    _pull_request_urls: list[str]
    # Tâche de diagnostic (méthode @task de la classe concrète), lue par le rappel de contexte.
    diagnostic_task: Callable[[], Any]
