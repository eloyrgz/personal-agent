"""MCP bridge for Personal Agent.

Exposes a single MCP tool, `ask_personal_agent`, that forwards messages to the
existing OpenAI-compatible router (app.py) running on this same machine and
returns its reply.

The bridge requires a bearer token and can also sit behind a reverse proxy.
"""

import os
from hmac import compare_digest

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

MCP_BRIDGE_PORT = int(os.getenv("MCP_BRIDGE_PORT", "8102"))
PERSONAL_AGENT_URL = os.getenv(
    "PERSONAL_AGENT_URL", "http://127.0.0.1:8101/v1/chat/completions"
)
MCP_BRIDGE_TOKEN = os.getenv("MCP_BRIDGE_TOKEN", "")

mcp = FastMCP(
    "personal-agent-bridge",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "127.0.0.1",
            "127.0.0.1:*",
            "192.168.0.2",
            "192.168.0.2:*",
            "webui.err-hass.duckdns.org",
            "webui.err-hass.duckdns.org:*",
        ],
        allowed_origins=[
            "https://webui.err-hass.duckdns.org",
        ],
    ),
)


@mcp.tool()
async def ask_personal_agent(message: str) -> str:
    """Send a message to my Personal Agent router and return its reply.

    The router itself decides whether this is a training question, a home
    automation request, or general conversation, and answers accordingly.
    """
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            PERSONAL_AGENT_URL,
            json={"messages": [{"role": "user", "content": message}]},
        )

    response.raise_for_status()
    data = response.json()
    choices = data.get("choices", [])

    if not choices:
        return "Personal Agent no devolvió ninguna respuesta."

    return choices[0]["message"]["content"]


async def health(request):
    return JSONResponse(
        {"status": "ok", "service": "personal-agent-mcp-bridge"}
    )


app = mcp.streamable_http_app()
app.add_route("/health", health, methods=["GET"])


class BearerTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health" or not MCP_BRIDGE_TOKEN:
            return await call_next(request)

        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not compare_digest(token, MCP_BRIDGE_TOKEN):
            return JSONResponse({"detail": "Invalid or missing bearer token"}, status_code=401)

        return await call_next(request)


app.add_middleware(BearerTokenMiddleware)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=MCP_BRIDGE_PORT)
