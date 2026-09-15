import asyncio

import pytest

from admin_ops import github


def test_pr_publication_refuses_stale_base(monkeypatch) -> None:
    requests = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"object": {"sha": "different-base"}}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def request(self, method, url, **kwargs):
            requests.append((method, url))
            return FakeResponse()

    monkeypatch.setattr(github.httpx, "AsyncClient", FakeClient)
    with pytest.raises(github.PublicationError, match="Base branch changed"):
        asyncio.run(
            github.create_draft_pr(
                proposal_id="abcdef12",
                proposal={"base_sha": "old-base", "files": {"src/ocr_platform/example.py": "VALUE=2\n"}},
                token="fake", repo="owner/repo", base_branch="main",
            )
        )
    assert len(requests) == 1
    assert requests[0][0] == "GET"


def test_approved_proposal_creates_draft_pr_only(monkeypatch) -> None:
    calls = []
    answers = iter(
        [
            {"object": {"sha": "base"}},
            {"tree": {"sha": "base-tree"}},
            {"sha": "blob"},
            {"sha": "new-tree"},
            {"sha": "new-commit"},
            {"ref": "refs/heads/codex/ops-abcdef12"},
            {"html_url": "https://github.com/owner/repo/pull/1", "number": 1},
        ]
    )

    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs.get("json")))
            return FakeResponse(next(answers))

    monkeypatch.setattr(github.httpx, "AsyncClient", FakeClient)
    details = asyncio.run(
        github.create_draft_pr(
            proposal_id="abcdef12",
            proposal={"base_sha": "base", "files": {"src/ocr_platform/example.py": "VALUE=2\n"}, "summary": "fix"},
            token="fake", repo="owner/repo", base_branch="main",
        )
    )
    assert details == {
        "url": "https://github.com/owner/repo/pull/1", "number": 1,
        "head_sha": "new-commit", "base_sha": "base",
    }
    assert len(calls) == 7
    assert calls[-1][0] == "POST"
    assert calls[-1][2]["draft"] is True
    assert calls[-1][2]["head"] == "codex/ops-abcdef12"
    tree_call = next(item for item in calls if item[1].endswith("/git/trees") and item[0] == "POST")
    assert tree_call[2]["tree"][0]["path"] == "OCR-document-parser/src/ocr_platform/example.py"
