"""Constantes et fabriques communes aux cas limites GitHub."""
"""Cas limites des modules github_* : 404, dossiers, occurrences, branche absente, syntaxe, cache de lecture."""

import pytest


pytest.importorskip("github_write")
from github import GithubException


BRANCH = "crewai/a"


def _http(status, message="x"):
    return GithubException(status, {"message": message}, None)
