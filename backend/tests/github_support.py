"""Faux dépôt GitHub des tests : contenus, blobs, lecteur qui compte ses appels."""

import github_client
import pytest


pytest.importorskip("github_write")  # saute le fichier si PyGithub/crewai ne sont pas installés
from github import GithubException


class FakeContentFile:
    """Reproduit le comportement de PyGithub : `decoded_content` lève au-delà de 1 Mo."""

    def __init__(self, decoded=None, sha="deadbeef"):
        self._decoded = decoded
        self.sha = sha

    @property
    def decoded_content(self):
        if self._decoded is None:
            raise AssertionError("contenu trop volumineux (simulé, comme le fait PyGithub)")
        return self._decoded


class FakeBlob:
    def __init__(self, content: str):
        self.content = content


class FakeRepo:
    def __init__(self, blob_content: bytes | None = None, blob_error: Exception | None = None):
        self._blob_content = blob_content
        self._blob_error = blob_error

    def get_git_blob(self, sha):
        if self._blob_error:
            raise self._blob_error
        import base64

        return FakeBlob(base64.b64encode(self._blob_content).decode("ascii"))


class _Entry:
    def __init__(self, path, kind="file"):
        self.path, self.type = path, kind


class _CountingRepo:
    """Faux repo : compte les lectures et sert un petit projet. `files` : {(branche, chemin): bytes}."""

    def __init__(self, files, dirs):
        self.files, self.dirs, self.reads = files, dirs, 0

    def get_contents(self, path, ref="main"):
        self.reads += 1
        if (ref, path) in self.dirs:
            return [_Entry(p, kind) for p, kind in self.dirs[(ref, path)]]
        if (ref, path) in self.files:
            return FakeContentFile(self.files[(ref, path)])
        raise GithubException(404, {"message": "Not Found"}, None)


def make_counting_repo(monkeypatch):
    """Faux GitHub qui sert un petit projet et compte les lectures (`repo.reads`) et les créations de client (`repo.created`)."""
    repo = _CountingRepo(
        files={("main", "package.json"): b'{"name": "app"}', ("main", "tsconfig.json"): b'{"compilerOptions": {"strict": true}}'},
        dirs={("main", ""): [("package.json", "file"), ("tsconfig.json", "file"), ("src", "dir")],
              ("main", "src"): [("src/App.tsx", "file")]},
    )
    created = []

    class FakeGithub:
        def __init__(self, auth=None):
            pass

        def get_repo(self, name):
            created.append(name)
            return repo

    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(github_client, "Github", FakeGithub)
    repo.created = created
    return repo
