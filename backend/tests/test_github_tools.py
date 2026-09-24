import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

gt = pytest.importorskip("github_tools")
from github import GithubException  # noqa: E402


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


def test_decode_content_file_uses_direct_content_when_available():
    content, error = gt._decode_content_file(FakeRepo(), FakeContentFile(decoded=b"hello\n"), "a.txt")
    assert content == "hello\n" and error is None


def test_decode_content_file_falls_back_to_blob_above_1mb():
    repo = FakeRepo(blob_content="grand fichier\n".encode("utf-8"))
    content, error = gt._decode_content_file(repo, FakeContentFile(decoded=None), "big.txt")
    assert content == "grand fichier\n" and error is None


def test_decode_content_file_reports_non_utf8_as_present_unreadable():
    from analyst_output import PRESENT_UNREADABLE

    bad = FakeContentFile(decoded=b"\xff\xfe\x00\x01")
    content, error = gt._decode_content_file(FakeRepo(), bad, "bin.dat")
    assert content is None and error.startswith(PRESENT_UNREADABLE)


def test_decode_content_file_blob_fetch_error_is_not_reported_as_unreadable():
    from analyst_output import PRESENT_UNREADABLE

    repo = FakeRepo(blob_error=RuntimeError("panne réseau"))
    content, error = gt._decode_content_file(repo, FakeContentFile(decoded=None), "big.txt")
    assert content is None and not error.startswith(PRESENT_UNREADABLE)


def test_github_read_file_uses_blob_fallback_for_large_files(monkeypatch):
    repo = FakeRepo(blob_content="grand fichier\n".encode("utf-8"))
    monkeypatch.setattr(gt, "_get_repo", lambda owner, r: repo)
    monkeypatch.setattr(repo, "get_contents", lambda path, ref: FakeContentFile(decoded=None), raising=False)
    result = gt.github_read_file.run(owner="o", repo="r", path="big.txt", branch="main")
    assert result == "grand fichier\n"


def test_github_read_file_does_not_double_prefix_blob_fallback_errors(monkeypatch):
    repo = FakeRepo(blob_error=GithubException(403, {"message": "API rate limit exceeded"}, None))
    monkeypatch.setattr(gt, "_get_repo", lambda owner, r: repo)
    monkeypatch.setattr(repo, "get_contents", lambda path, ref: FakeContentFile(decoded=None), raising=False)
    result = gt.github_read_file.run(owner="o", repo="r", path="big.txt", branch="main")
    assert result.count("ERREUR") == 1 and "ERREUR : ERREUR" not in result
