"""Streamable HTTP transport for TrainingPeaks MCP.

Pacific Personal Assistant opens this URL with header::

    X-TP-Auth-Cookie: <Production_tpAuth value>

The cookie is bound to the MCP session so tool calls in that chat use it.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from tp_mcp.auth.request_cookie import (
    COOKIE_HEADER,
    COOKIE_HEADER_ALT,
    SESSION_HEADER,
    cookie_for_session,
    forget_session_cookie,
    remember_session_cookie,
    reset_request_cookie,
    set_request_cookie,
)
from tp_mcp.server import server

logger = logging.getLogger("tp-mcp")


def _header_map(scope: Scope) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in scope.get("headers") or []:
        out[key.decode("latin-1").lower()] = value.decode("latin-1")
    return out


class CookieContextMiddleware:
    """Bind X-TP-Auth-Cookie (or a remembered session cookie) for each request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = _header_map(scope)
        session_id = headers.get(SESSION_HEADER)
        cookie = (
            headers.get(COOKIE_HEADER)
            or headers.get(COOKIE_HEADER_ALT)
            or cookie_for_session(session_id)
            or os.environ.get("TP_AUTH_COOKIE")
        )
        token = set_request_cookie(cookie)

        async def send_wrapper(message: dict) -> None:
            if (
                message["type"] == "http.response.start"
                and cookie
            ):
                raw_headers = message.get("headers") or []
                for key, value in raw_headers:
                    if key.decode("latin-1").lower() == SESSION_HEADER:
                        remember_session_cookie(
                            value.decode("latin-1"), cookie
                        )
                        break
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            reset_request_cookie(token)


async def health(_: Request) -> Response:
    return JSONResponse({"ok": True, "service": "trainingpeaks-mcp"})


def create_app() -> Starlette:
    # DNS rebinding checks break behind Railway's Host headers.
    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
        stateless=False,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        logger.info("Starting TrainingPeaks MCP HTTP server")
        async with session_manager.run():
            yield
        # Drop session cookies when the process stops.
        from tp_mcp.auth import request_cookie as rc

        rc._session_cookies.clear()
        logger.info("TrainingPeaks MCP HTTP server stopped")

    async def handle_mcp(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Mount("/mcp", app=handle_mcp),
            # Root also accepts MCP so clients can use the bare public URL.
            Mount("/", app=handle_mcp),
        ],
        lifespan=lifespan,
        middleware=[Middleware(CookieContextMiddleware)],
    )


def run_http_server() -> int:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    # Skip stdio-style startup auth — cookie arrives per request from Pacific.
    os.environ.setdefault("TP_MCP_SKIP_STARTUP_VALIDATION", "1")
    uvicorn.run(
        create_app(),
        host=host,
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info"),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    return 0


# Imported only so DELETE session cleanup can be extended later.
def drop_session(session_id: str) -> None:
    forget_session_cookie(session_id)
