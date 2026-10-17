import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

KEEPIT_URL = os.environ['KEEPIT_URL'].rstrip('/')
KEEPIT_EMAIL = os.environ['KEEPIT_EMAIL']
KEEPIT_PASSWORD = os.environ['KEEPIT_PASSWORD']
MCP_API_KEY = os.environ['MCP_API_KEY']
if not MCP_API_KEY:
    raise RuntimeError('MCP_API_KEY must not be empty')

class KeepITClient:
    def __init__(self) -> None:
        self.http = httpx.AsyncClient(base_url=KEEPIT_URL, timeout=20.0)
        self.token: str | None = None
        self.lock = asyncio.Lock()

    async def login(self) -> None:
        async with self.lock:
            response = await self.http.post('/api/auth/login', json={
                'email': KEEPIT_EMAIL, 'password': KEEPIT_PASSWORD
            })
            response.raise_for_status()
            payload = response.json()
            # The response field must be checked against the installed keepIT version.
            token = payload.get('accessToken')
            if not isinstance(token, str) or not token:
                raise ValueError('Login response lacks accessToken; inspect response field names')
            self.token = token

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self.token is None:
            await self.login()
        response = await self.http.request(method, path, headers={
            'Authorization': f'Bearer {self.token}', 'Accept': 'application/json'
        }, **kwargs)
        if response.status_code == 401:
            await self.login()
            response = await self.http.request(method, path, headers={
                'Authorization': f'Bearer {self.token}', 'Accept': 'application/json'
            }, **kwargs)
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self.http.aclose()

client = KeepITClient()
mcp = FastMCP(
    "keepit-mcp",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    ),
)

@mcp.tool()
async def list_notes() -> Any:
    """List notes from the connected keepIT account."""
    return await client.request('GET', '/api/notes')

@mcp.tool()
async def create_note(title: str, body: str) -> Any:
    """Create a keepIT note. Body supports Markdown."""
    return await client.request('POST', '/api/notes', json={'title': title, 'body': body})

class AuthenticatedMCP:
    """Require a shared Bearer secret on every MCP HTTP request."""
    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope['type'] == 'http':
            headers = dict(scope.get('headers', []))
            provided = headers.get(b'authorization', b'').decode('latin-1')
            expected = f'Bearer {MCP_API_KEY}'
            if not hmac.compare_digest(provided, expected):
                await send({'type': 'http.response.start', 'status': 401,
                            'headers': [(b'content-type', b'application/json')]})
                await send({'type': 'http.response.body', 'body': b'{"error":"Unauthorized"}'})
                return
        await self.app(scope, receive, send)

@asynccontextmanager
async def lifespan(app: Any):
    async with mcp.session_manager.run():
        try:
            yield
        finally:
            await client.close()

# Use the SDK's HTTP ASGI app, with its session manager lifespan.
from starlette.applications import Starlette
from starlette.routing import Mount

app = AuthenticatedMCP(Starlette(routes=[Mount('/', app=mcp.streamable_http_app())], lifespan=lifespan))

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=int(os.getenv('PORT', '8000')))
