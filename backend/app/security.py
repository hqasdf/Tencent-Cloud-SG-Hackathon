"""Small HTTP boundary for the protected synthetic demo; no policy decisions."""
import re

from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

MAX_REQUEST_BYTES = 32 * 1024
_HOST = re.compile(rb"(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(?::[0-9]{1,5})?\Z")


async def redacted_validation_error(_request, error: RequestValidationError):
    # Pydantic's default `input` and `ctx` can echo complete interview bodies.
    fields = {"body", "path", "query", "header", "party", "content", "tripId", "trip_id", "replayMode", "replay_mode", "case_id", "intake_case_id", "source_case_id"}
    details = [{"type": item["type"], "loc": [part if isinstance(part, int) or part in fields else "field" for part in item["loc"]], "msg": "Invalid request value"}
               for item in error.errors()]
    return JSONResponse({"detail": details}, status_code=422)


class RequestBoundary:
    def __init__(self, app, max_bytes=MAX_REQUEST_BYTES):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        if not _HOST.fullmatch(headers.get(b"host", b"localhost")):
            return await JSONResponse({"detail": "Invalid Host header"}, status_code=400)(scope, receive, send)
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = -1
        if length < 0:
            return await JSONResponse({"detail": "Invalid content length"}, status_code=400)(scope, receive, send)
        if length > self.max_bytes:
            return await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)
        # Buffer only within the ceiling; this also covers missing/false length
        # and chunked bodies, before JSON parsing or any paid provider operation.
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > self.max_bytes:
                return await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)
            chunks.append(body)
            if not message.get("more_body", False):
                break
        consumed = False

        async def bounded_receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
