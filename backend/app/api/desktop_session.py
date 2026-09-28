"""Per-launch desktop API authentication, including WebSocket upgrades.

The desktop main process supplies the token out of band. It must never appear
in a URL, frontend JavaScript, an error body, or persisted application settings.
"""
from __future__ import annotations

import hmac

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class DesktopSessionMiddleware:
    def __init__(self, app: ASGIApp, *, token: str, origins: tuple[str, ...]) -> None:
        if len(token) < 32 or not token.isascii():
            raise ValueError("desktop session requires a fresh ASCII token of at least 32 characters")
        if not origins or "*" in origins:
            raise ValueError("desktop session requires explicit application origins")
        self.app = app
        self._token = token.encode("ascii")
        self._origins = tuple(origin.encode("ascii") for origin in origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers", [])
        tokens = [value for key, value in headers if key.lower() == b"x-mars-desktop-token"]
        origins = [value for key, value in headers if key.lower() == b"origin"]
        authenticated = len(tokens) == 1 and hmac.compare_digest(tokens[0], self._token)
        # Internal HTTP health requests can omit Origin. Browser WebSockets
        # require it, and all supplied origins must match the owned gateway.
        origin_allowed = (
            len(origins) == 1 and origins[0] in self._origins
        ) or (not origins and scope["type"] == "http")
        if not authenticated or not origin_allowed:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                response = JSONResponse(
                    {"detail": "Desktop session required" if not authenticated else "Origin denied"},
                    status_code=401 if not authenticated else 403,
                    headers={"Cache-Control": "no-store"},
                )
                await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
