import json

import httpx
import pytest
from starlette.testclient import TestClient

import server


def _fake_task(i: int) -> dict:
    return {
        "id": f"t{i}",
        "text": f"Tarea {i}",
        "quadrant": "q2",
        "done": False,
        "energy": "alto",
        "createdAt": "2026-06-13T15:38:35.424Z",
        "updatedAt": "2026-06-15T04:53:10.526Z",
        "minutes": 40,
    }


class _Recorder:
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/tasks":
            tasks = [_fake_task(i) for i in range(20)]
            return httpx.Response(200, json={"total": 20, "pending": 18, "tasks": tasks})
        if path == "/tasks/search":
            results = [_fake_task(i) for i in range(20)]
            return httpx.Response(200, json={"count": 20, "results": results})
        if path == "/board":
            return httpx.Response(
                200,
                json={
                    "focus": [_fake_task(i) for i in range(15)],
                    "quick": [_fake_task(i) for i in range(3)],
                    "counts": {"focus": 15, "quick": 3},
                },
            )
        return httpx.Response(200, json={"ok": True, "path": path})


@pytest.fixture
def mock_jarvis(monkeypatch):
    recorder = _Recorder()
    transport = httpx.MockTransport(recorder.handler)

    real_client_cls = httpx.AsyncClient

    class PatchedClient(real_client_cls):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)
    return recorder


@pytest.mark.anyio
async def test_list_tasks_calls_expected_path(mock_jarvis):
    await server.list_tasks()
    assert mock_jarvis.requests[0].method == "GET"
    assert mock_jarvis.requests[0].url.path == "/tasks"


@pytest.mark.anyio
async def test_list_tasks_caps_count_and_strips_verbose_fields(mock_jarvis):
    # 20 fake tasks exist upstream; the on-device model's context is small,
    # so the tool must cap the count and drop non-essential fields.
    result = await server.list_tasks(limit=5)
    assert result["total"] == 20
    assert result["shown"] == 5
    assert len(result["tasks"]) == 5
    assert set(result["tasks"][0].keys()) == {"id", "text", "quadrant", "done"}


@pytest.mark.anyio
async def test_search_tasks_passes_query_params(mock_jarvis):
    await server.search_tasks(query="reunion", status="active", priority="q1", limit=5)
    req = mock_jarvis.requests[0]
    assert req.url.path == "/tasks/search"
    assert req.url.params["q"] == "reunion"
    assert req.url.params["status"] == "active"
    assert req.url.params["priority"] == "q1"
    assert req.url.params["limit"] == "5"


@pytest.mark.anyio
async def test_search_tasks_strips_verbose_fields(mock_jarvis):
    result = await server.search_tasks(query="tarea")
    assert result["count"] == 20
    assert set(result["results"][0].keys()) == {"id", "text", "quadrant", "done"}


@pytest.mark.anyio
async def test_get_board_caps_per_lane_and_strips_verbose_fields(mock_jarvis):
    result = await server.get_board(limit_per_lane=5)
    assert len(result["focus"]) == 5
    assert len(result["quick"]) == 3
    assert result["counts"] == {"focus": 15, "quick": 3}
    assert set(result["focus"][0].keys()) == {"id", "text", "quadrant", "done"}


@pytest.mark.anyio
async def test_create_task_posts_expected_body(mock_jarvis):
    await server.create_task(title="Comprar café", quadrant="q2", due_date="2026-10-01")
    req = mock_jarvis.requests[0]
    assert req.method == "POST"
    assert req.url.path == "/tasks/new"
    body = json.loads(req.content)
    assert body == {"title": "Comprar café", "quadrant": "q2", "due_date": "2026-10-01"}


@pytest.mark.anyio
async def test_complete_task_posts_to_task_id_path(mock_jarvis):
    await server.complete_task("abc123")
    req = mock_jarvis.requests[0]
    assert req.method == "POST"
    assert req.url.path == "/tasks/abc123/complete"


@pytest.mark.anyio
async def test_set_task_lane_posts_lane_body(mock_jarvis):
    await server.set_task_lane("abc123", "done")
    req = mock_jarvis.requests[0]
    assert req.url.path == "/tasks/abc123/lane"
    assert json.loads(req.content) == {"lane": "done"}


@pytest.mark.anyio
async def test_all_tools_registered():
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "list_tasks", "search_tasks", "create_task", "complete_task",
        "set_task_lane", "get_board", "get_streak", "what_to_do_now",
        "get_plan", "health",
    }


def test_healthz_is_open_without_token(monkeypatch):
    monkeypatch.setattr(server, "MCP_AUTH_TOKEN", "secret-token")
    app = server.BearerAuthMiddleware(server.mcp_app, "secret-token")
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "jarvis-focus-mcp"}


def test_mcp_endpoint_rejects_missing_token():
    app = server.BearerAuthMiddleware(server.mcp_app, "secret-token")
    client = TestClient(app)
    response = client.post("/mcp", json={})
    assert response.status_code == 401


def test_mcp_endpoint_rejects_wrong_token():
    app = server.BearerAuthMiddleware(server.mcp_app, "secret-token")
    client = TestClient(app)
    response = client.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_mcp_endpoint_accepts_valid_tokens_bearer_and_raw_header():
    # Reaching the real MCP session manager (not just the 401 short-circuit)
    # needs the ASGI lifespan started, hence the context manager. mcp_app is
    # a module-level singleton whose session manager can only run() once per
    # process, so both checks live in a single `with` block rather than two
    # separate tests each starting/stopping their own lifespan.
    app = server.BearerAuthMiddleware(server.mcp_app, "secret-token")
    with TestClient(app) as client:
        bearer_response = client.post("/mcp", json={}, headers={"Authorization": "Bearer secret-token"})
        # AI Edge Gallery's MCP UI is a raw "header name / header value" pair,
        # not an Authorization-scheme picker — support that shape too.
        raw_response = client.post("/mcp", json={}, headers={"X-MCP-Token": "secret-token"})
        # A trailing newline from copy-pasting the token (e.g. from a chat
        # code block) shouldn't break auth either.
        whitespace_response = client.post("/mcp", json={}, headers={"X-MCP-Token": "  secret-token\n"})
    assert bearer_response.status_code != 401
    assert raw_response.status_code != 401
    assert whitespace_response.status_code != 401


def test_mcp_endpoint_rejects_wrong_raw_token_header():
    app = server.BearerAuthMiddleware(server.mcp_app, "secret-token")
    client = TestClient(app)
    response = client.post("/mcp", json={}, headers={"X-MCP-Token": "wrong"})
    assert response.status_code == 401


def test_auth_disabled_when_token_unset_allows_through():
    # With no token configured, requests should reach the wrapped app (not be
    # rejected at the auth layer) — same opt-in-auth convention as the other
    # services in this account.
    app = server.BearerAuthMiddleware(server.mcp_app, None)
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
