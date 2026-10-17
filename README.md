# keepit-mcp

A Streamable HTTP MCP server for keepITCore v1, implemented against the provided OpenAPI 3.1.1 contract.

This package extends the original [wlmirand/keepit-mcp](https://github.com/wlmirand/keepit-mcp) concept with note editing, search, lists, reminders, sharing, and media operations. The server logs in to keepIT using email and password, caches the access token, and automatically logs in again when the token expires or keepIT returns HTTP 401.

## Configuration

1. Copy `.env.example` to `.env` and set `KEEPIT_URL`, `KEEPIT_EMAIL`, `KEEPIT_PASSWORD`, and `MCP_API_KEY`.
2. Change `keepit_network` in `compose.yaml` to the Docker network shared with keepIT. Check existing networks with `docker network ls`.
3. Run `docker compose up -d --build`.
4. The MCP endpoint is `http://127.0.0.1:8000/mcp` from the Docker host or `http://keepit-mcp:8000/mcp` from another container on the same network.
5. Configure MCP clients with the `Authorization: Bearer <MCP_API_KEY>` header.

The Compose file binds the host port to loopback only. Use an HTTPS reverse proxy with access controls for remote clients. Do not expose port 8000 directly to the Internet.

## Available tools (22)

- Notes: `list_notes`, `search_notes`, `get_note`, `create_note`, `update_note`, `delete_note`, `update_note_state`
- Reminders: `set_note_reminder`, `delete_note_reminder`
- Note lists and trash: `set_note_lists`, `empty_trash`
- Lists: `list_lists`, `create_list`, `update_list`, `delete_list`
- Sharing: `list_note_shares`, `share_note`, `update_note_share`, `delete_note_share`
- Media: `upload_note_media`, `get_note_media`, `delete_note_media`

## Important behavior

- **Automatic authentication:** configure `KEEPIT_EMAIL` and `KEEPIT_PASSWORD`. The MCP server obtains a JWT from `POST /api/auth/login`, caches it until 60 seconds before expiration, and reauthenticates once after a 401. A static `KEEPIT_TOKEN` remains available as an alternative, but requires manual renewal. Credentials take precedence when both are set. Two-factor login is not implemented; accounts requiring 2FA need a separate integration. Do not commit `.env` to version control.
- `search_notes` retrieves notes through the REST API and searches titles, bodies, and checklist items in memory. It is not a server-side search endpoint.
- `update_note` uses the OpenAPI `UpdateNoteDto.fields` mechanism for selective updates. Explicit `null` values to clear a field are not supported by the current tool signature.
- Note, list, and media deletion, share revocation, and trash emptying require `confirm=True`.
- Media upload/download uses base64 and has a default 8 MiB size limit.
- `listId` values are sent as repeated query parameters.
- Supported note types: `Text`, `Checklist`, `Image`; sharing roles: `Viewer`, `Editor`; reminder recurrence: `None`, `Daily`, `Weekly`, `Monthly`, `Yearly`.
- FastMCP DNS rebinding protection is disabled for container/reverse-proxy compatibility, but the ASGI wrapper enforces a bearer key. Only use this on trusted networks behind HTTPS when remote.

## Validation

The Python source was syntax-checked, but no integration tests were run against a live keepIT deployment. Test the MCP tools on non-critical notes before enabling destructive actions.

## Updating an existing deployment

Replace the project files, keep your existing Docker network name, set `KEEPIT_EMAIL` and `KEEPIT_PASSWORD` in `.env`, and run `docker compose up -d --build`. No keepIT image changes are required.
