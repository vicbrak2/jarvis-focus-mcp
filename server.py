"""Jarvis FOCUS OS MCP server.

Exposes the Jarvis FOCUS OS task-manager REST API (https://jarvis-focus-os-production.up.railway.app)
as MCP tools, so an MCP client (e.g. the AI Edge Gallery app) can list, search,
create, complete, and move tasks through an on-device LLM.

Transport: streamable-http, so it can be reached over a network URL rather than
spawned as a local stdio subprocess.

Jarvis FOCUS OS itself has no authentication on its REST API, so this server
requires its own bearer token (MCP_AUTH_TOKEN) in front of it — otherwise
anyone with the MCP URL could read/write the user's real tasks.

Run:
    uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.streamable_http import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

JARVIS_BASE_URL = os.getenv("JARVIS_BASE_URL", "https://jarvis-focus-os-production.up.railway.app").rstrip("/")
MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN")
REQUEST_TIMEOUT_SECONDS = 15.0

mcp = MCPServer(
    name="jarvis-focus-os",
    version="0.1.0",
    instructions=(
        "Herramientas para gestionar las tareas personales del usuario en Jarvis FOCUS OS "
        "(un gestor de tareas con matriz de prioridad tipo Eisenhower: q1=urgente+importante, "
        "q2=importante no urgente, q3=urgente no importante, q4=ni urgente ni importante). "
        "Usalas cuando el usuario pida ver, buscar, crear, completar o mover sus propias tareas, "
        "o pregunte qué hacer ahora / cuál es su racha / su plan del día."
    ),
)


async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.get(f"{JARVIS_BASE_URL}{path}", params=params)
        response.raise_for_status()
        return response.json()


async def _post(path: str, json: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post(f"{JARVIS_BASE_URL}{path}", json=json)
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def list_tasks() -> Any:
    """Lista todas las tareas del usuario (pendientes y completadas)."""
    return await _get("/tasks")


@mcp.tool()
async def search_tasks(query: str = "", status: str = "all", priority: str = "all", limit: int = 50) -> Any:
    """Busca tareas por texto y filtra por estado/prioridad.

    status: "all" | "active" | "completed"
    priority: "all" | "q1" | "q2" | "q3" | "q4"
    """
    return await _get("/tasks/search", params={"q": query, "status": status, "priority": priority, "limit": limit})


@mcp.tool()
async def create_task(title: str, quadrant: str | None = None, due_date: str | None = None) -> Any:
    """Crea una tarea nueva.

    quadrant: "q1" (urgente+importante) | "q2" (importante) | "q3" (urgente) | "q4" (ninguno), opcional.
    due_date: fecha límite en formato ISO (YYYY-MM-DD), opcional.
    """
    return await _post("/tasks/new", json={"title": title, "quadrant": quadrant, "due_date": due_date})


@mcp.tool()
async def complete_task(task_id: str) -> Any:
    """Marca una tarea individual como completada, por su id."""
    return await _post(f"/tasks/{task_id}/complete")


@mcp.tool()
async def set_task_lane(task_id: str, lane: str) -> Any:
    """Mueve una tarea a otro carril del tablero (por id)."""
    return await _post(f"/tasks/{task_id}/lane", json={"lane": lane})


@mcp.tool()
async def get_board() -> Any:
    """Devuelve el tablero de tareas organizado por carril/columna."""
    return await _get("/board")


@mcp.tool()
async def get_streak() -> Any:
    """Devuelve la racha actual de días consecutivos completando tareas."""
    return await _get("/streak")


@mcp.tool()
async def what_to_do_now() -> Any:
    """Sugiere qué tarea hacer ahora, según prioridad y contexto."""
    return await _get("/now")


@mcp.tool()
async def get_plan() -> Any:
    """Devuelve el plan/agenda del usuario."""
    return await _get("/plan")


@mcp.tool()
async def health() -> Any:
    """Chequea si el backend de Jarvis FOCUS OS está online."""
    return await _get("/health")


class BearerAuthMiddleware:
    """Requires 'Authorization: Bearer <MCP_AUTH_TOKEN>' on every request when configured.

    Left open (no check) if MCP_AUTH_TOKEN is unset, matching the same
    opt-in-auth convention used by llm-gateway-platform.
    """

    def __init__(self, app: ASGIApp, token: str | None) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # /healthz must stay open for Railway's own healthcheck prober, which
        # never sends a bearer token.
        if scope["type"] != "http" or not self.token or scope.get("path") == "/healthz":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        auth_header = request.headers.get("authorization", "")
        expected = f"Bearer {self.token}"
        if auth_header != expected:
            response = JSONResponse({"error": "invalid or missing bearer token"}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "jarvis-focus-mcp"})


mcp_app: Starlette = mcp.streamable_http_app(
    # DNS-rebinding protection is aimed at browser clients; this server's
    # real access control is the bearer token below, and the MCP client here
    # is a native mobile HTTP client whose Host/Origin headers aren't
    # predictable in advance.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
mcp_app.add_route("/healthz", health_check, methods=["GET"])
app = BearerAuthMiddleware(mcp_app, MCP_AUTH_TOKEN)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
