"""Bound multipart uploads while reading the ASGI stream, before file spooling."""

from starlette.middleware.body_limit import RequestBodyLimitMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from ..parsing import MAX_FILE_BYTES

MAX_UPLOAD_BODY_BYTES = MAX_FILE_BYTES + 64 * 1024


class UploadBodyLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app
        self.limited = RequestBodyLimitMiddleware(app, max_body_size=MAX_UPLOAD_BODY_BYTES)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (scope["type"] == "http" and scope["method"] == "POST"
                and path.startswith("/api/v1/cases/") and path.endswith("/documents")):
            await self.limited(scope, receive, send)
        else:
            await self.app(scope, receive, send)
