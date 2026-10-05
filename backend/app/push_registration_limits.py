"""Bound registration input before JSON parsing without restricting legacy cleanup."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

BODY_BYTES = 16 * 1024
ENDPOINT_BYTES = 4 * 1024
KEY_BYTES = 1024


def endpoint_bytes(value: str) -> str:
    if len(value.encode("utf-8")) > ENDPOINT_BYTES:
        raise ValueError(f"Push endpoint must be at most {ENDPOINT_BYTES} UTF-8 bytes")
    return value


def key_bytes(value: str) -> str:
    if len(value.encode("utf-8")) > KEY_BYTES:
        raise ValueError(f"Push key must be at most {KEY_BYTES} UTF-8 bytes")
    return value


class PushRegistrationBodyLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if (scope["type"] != "http" or scope["method"] != "POST"
                or scope["path"].rstrip("/") != "/api/push/subscribe"):
            await self.app(scope, receive, send)
            return

        async def reject():
            await JSONResponse(
                {"detail": f"Push registration body must be at most {BODY_BYTES} bytes"},
                status_code=413,
            )(scope, receive, send)

        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length" and value.isdigit():
                significant = value.lstrip(b"0") or b"0"
                if len(significant) > 5 or int(significant) > BODY_BYTES:
                    await reject()
                    return

        # Never trust Content-Length as the actual size. Keep at most BODY_BYTES
        # buffered, including for chunked requests or understated lengths.
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > BODY_BYTES:
                await reject()
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
