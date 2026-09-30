import asyncio
import unittest
from unittest.mock import patch

import httpx
from starlette.requests import Request
from starlette.responses import Response

import mcp_bridge


async def request(method: str, path: str, headers: dict[str, str] | None = None):
    transport = httpx.ASGITransport(app=mcp_bridge.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, headers=headers)


class McpBridgeAuthTests(unittest.TestCase):
    def test_health_is_public(self):
        with patch.object(mcp_bridge, "MCP_BRIDGE_TOKEN", "secret"):
            response = asyncio.run(request("GET", "/health"))

        self.assertEqual(response.status_code, 200)

    def test_mcp_requires_configured_bearer_token(self):
        with patch.object(mcp_bridge, "MCP_BRIDGE_TOKEN", "secret"):
            missing = asyncio.run(request("GET", "/mcp"))
            wrong = asyncio.run(request("GET", "/mcp", {"Authorization": "Bearer wrong"}))

            scope = {
                "type": "http",
                "method": "GET",
                "path": "/mcp",
                "headers": [(b"authorization", b"Bearer secret")],
            }

            async def pass_through(_request):
                return Response(status_code=204)

            valid = asyncio.run(
                mcp_bridge.BearerTokenMiddleware(mcp_bridge.app).dispatch(
                    Request(scope), pass_through
                )
            )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(valid.status_code, 204)


if __name__ == "__main__":
    unittest.main()