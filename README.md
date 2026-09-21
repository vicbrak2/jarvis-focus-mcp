# jarvis-focus-mcp

MCP server exposing [Jarvis FOCUS OS](https://jarvis-focus-os-production.up.railway.app)'s
task-manager REST API as MCP tools, over the `streamable-http` transport, so
a remote MCP client (e.g. the AI Edge Gallery app on Android) can list,
search, create, complete, and move tasks through an on-device LLM.

## Why a separate server

Jarvis FOCUS OS itself has no `/mcp` endpoint and no authentication on its
REST API. This server sits in front of it, translates MCP tool calls into
plain REST calls, and requires its own bearer token — otherwise anyone with
the MCP URL could read/write the user's real tasks.

## Tools

`list_tasks`, `search_tasks`, `create_task`, `complete_task`, `set_task_lane`,
`get_board`, `get_streak`, `what_to_do_now`, `get_plan`, `health`.

Deliberately not exposed (higher blast-radius / less commonly needed):
`complete-all`, `complete-selected`, calendar create/delete/import, and the
`sync/*` maintenance endpoints. Add them the same way if needed later.

## Run locally

```bash
pip install -r requirements.txt
MCP_AUTH_TOKEN=your-token uvicorn server:app --host 0.0.0.0 --port 8000
```

MCP endpoint: `POST /mcp` (streamable-http). Health check (no auth):
`GET /healthz`.

## Environment variables

See `.env.example`.

## Connecting from AI Edge Gallery

In the app: Agent Chat → MCP button → Add MCP Server, enter this server's
`/mcp` URL, and in the header name/value fields put:

- Header name: `X-MCP-Token`
- Header value: `MCP_AUTH_TOKEN`'s value (raw, no "Bearer " prefix)
