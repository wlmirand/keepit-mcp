# keepit-mcp

Self-hosted MCP bridge for [Richy1989/keepIT](https://github.com/Richy1989/keepIT).

## Verified keepIT API operations

- `POST /api/auth/login` with `email`, `password` JSON
- `GET /api/notes` (Bearer JWT)
- `POST /api/notes` with `title`, `body` JSON (Bearer JWT)

The login response is assumed to contain `accessToken`. If your version uses a different property, change `payload.get('accessToken')` in `src/keepit_mcp/server.py`.

## Build and run

```bash
docker build -t keepit-mcp:local .
docker run --rm -p 127.0.0.1:8090:8000 \
  -e KEEPIT_URL=https://notes.example.com \
  -e KEEPIT_EMAIL=you@example.com \
  -e KEEPIT_PASSWORD='your-password' \
  -e MCP_API_KEY='your-long-random-secret' \
  keepit-mcp:local
```

Or copy `stack.env.example` to `stack.env`, fill it in, then run:

```bash
docker compose --env-file stack.env up --build -d
```

Portainer can use `compose.yaml` and equivalent stack environment variables, but `build: .` requires a build context available to the Docker engine. Alternatively, build the image first and change `build: .` to `image: keepit-mcp:local`.

## MCP client

Connect via Streamable HTTP at `http://HOST:8090/mcp` with header:

```
Authorization: Bearer <MCP_API_KEY>
```

Use HTTPS via your reverse proxy for any remote connection. Do not expose the MCP endpoint without authentication. MCP_API_KEY is **not** keepIT's JWT signing key.

## Available tools

- `list_notes`
- `create_note(title, body)`

## Limitations

This is an initial implementation. It has not been integration-tested against your deployed keepIT version. The `accessToken` response property and MCP SDK compatibility should be verified in a local build. JWT refresh is performed on HTTP 401. No support yet for editing/deleting notes or per-agent permissions. Keep this service private until tested.
