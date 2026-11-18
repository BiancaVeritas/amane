import httpx2
import pytest

from amane.api.routes import oshash_submissions as oshash_api
from amane.config.manager import SiteConfig
from amane.db.models import OshashSubmissionStatus
from amane.enums import SiteName


def _mock_http(monkeypatch, handler):
    """所有请求均由 MockTransport 处理，不访问真实网络。"""

    def mock_client(*, proxy=None, timeout=None, follow_redirects=False):
        return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    monkeypatch.setattr(oshash_api, "AsyncClient", mock_client)


def _configure_token(app, token="test-token"):
    app.state.runtime.config.hot.scraping.site_config[SiteName.THEPORNDB] = SiteConfig(api_token=token)


async def _enqueue(repo, uuid="scene-uuid", oshash="hash-value"):
    await repo.enqueue_oshash_submission(
        theporndb_uuid=uuid,
        oshash=oshash,
        duration=3600,
        title="Test scene",
        path="/video/test.mp4",
    )
    rows, total = await repo.list_oshash_submissions(offset=0, limit=100)
    assert total >= 1
    return next(row for row in rows if row.theporndb_uuid == uuid and row.oshash == oshash)


@pytest.mark.asyncio(loop_scope="function")
async def test_submit_success_deletes_submission(app, client, seed_library, stop_worker, monkeypatch):
    _configure_token(app)
    row = await _enqueue(seed_library)

    captured = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = request.read()
        return httpx2.Response(200, json={"success": True})

    _mock_http(monkeypatch, handler)

    response = await client.post(f"oshash-submissions/{row.id}/submit")

    assert response.status_code == 200
    assert response.json() == {"submitted": True}
    assert captured["url"] == (f"https://api.theporndb.net/scenes/{row.theporndb_uuid}/hash")
    assert captured["authorization"] == "Bearer test-token"
    assert httpx2.Request("POST", "https://example.com").method == "POST"

    import json

    body = json.loads(captured["body"])
    assert body == {
        "type": "OSHASH",
        "hash": row.oshash,
        "duration": row.duration,
    }
    assert await seed_library.get_oshash_submission(row.id) is None


@pytest.mark.asyncio(loop_scope="function")
async def test_http_4xx_returns_submission_to_pending(app, client, seed_library, stop_worker, monkeypatch):
    _configure_token(app)
    row = await _enqueue(seed_library)

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(429, json={"error": "rate limited"})

    _mock_http(monkeypatch, handler)

    response = await client.post(f"oshash-submissions/{row.id}/submit")

    assert response.status_code == 502
    current = await seed_library.get_oshash_submission(row.id)
    assert current is not None
    assert current.status == OshashSubmissionStatus.PENDING
    assert current.last_error is not None
    assert "429" in current.last_error


@pytest.mark.asyncio(loop_scope="function")
async def test_http_5xx_marks_uncertain_and_prevents_resubmit(app, client, seed_library, stop_worker, monkeypatch):
    _configure_token(app)
    row = await _enqueue(seed_library)
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(500, json={"error": "server error"})

    _mock_http(monkeypatch, handler)

    first = await client.post(f"oshash-submissions/{row.id}/submit")
    second = await client.post(f"oshash-submissions/{row.id}/submit")

    assert first.status_code == 502
    assert second.status_code == 409
    assert calls == 1

    current = await seed_library.get_oshash_submission(row.id)
    assert current is not None
    assert current.status == OshashSubmissionStatus.UNCERTAIN


@pytest.mark.asyncio(loop_scope="function")
async def test_request_error_marks_uncertain(app, client, seed_library, stop_worker, monkeypatch):
    _configure_token(app)
    row = await _enqueue(seed_library)

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("network unavailable", request=request)

    _mock_http(monkeypatch, handler)

    response = await client.post(f"oshash-submissions/{row.id}/submit")

    assert response.status_code == 502
    current = await seed_library.get_oshash_submission(row.id)
    assert current is not None
    assert current.status == OshashSubmissionStatus.UNCERTAIN


@pytest.mark.asyncio(loop_scope="function")
async def test_missing_token_returns_pending(app, client, seed_library, stop_worker, monkeypatch):
    app.state.runtime.config.hot.scraping.site_config[SiteName.THEPORNDB] = SiteConfig()
    row = await _enqueue(seed_library)

    def handler(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("HTTP client must not be called without an API token")

    _mock_http(monkeypatch, handler)

    response = await client.post(f"oshash-submissions/{row.id}/submit")

    assert response.status_code == 503
    current = await seed_library.get_oshash_submission(row.id)
    assert current is not None
    assert current.status == OshashSubmissionStatus.PENDING


@pytest.mark.asyncio(loop_scope="function")
async def test_batch_continues_after_one_submission_fails(app, client, seed_library, stop_worker, monkeypatch):
    _configure_token(app)
    first = await _enqueue(seed_library, uuid="scene-fail")
    second = await _enqueue(seed_library, uuid="scene-success")
    requested = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested.append(str(request.url))
        if "scene-fail" in str(request.url):
            return httpx2.Response(500, json={"error": "server error"})
        return httpx2.Response(200, json={"success": True})

    _mock_http(monkeypatch, handler)

    response = await client.post(
        "oshash-submissions/batch",
        json={"ids": [first.id, second.id]},
    )

    assert response.status_code == 200
    assert response.json()["succeeded"] == 1
    assert response.json()["failed"] == 1
    assert len(requested) == 2

    first_current = await seed_library.get_oshash_submission(first.id)
    assert first_current is not None
    assert first_current.status == OshashSubmissionStatus.UNCERTAIN
    assert await seed_library.get_oshash_submission(second.id) is None
