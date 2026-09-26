import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

log = logging.getLogger("voice_agent")


def register_error_handlers(app: FastAPI) -> None:
    """Routes keep raising HTTPException for expected failures (`{"detail": ...}`).
    These cover the rest. Neither response says anything about the cause: details go to the log."""

    @app.exception_handler(OperationalError)
    async def database_unavailable(request: Request, error: OperationalError) -> JSONResponse:
        # Class name only: the message can carry connection details.
        log.error("Database unavailable on %s: %s", _route(request), type(error.orig).__name__)
        return JSONResponse({"detail": "Database unavailable"}, status_code=503)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception) -> JSONResponse:
        log.exception("Unhandled error on %s", _route(request))
        return JSONResponse({"detail": "Internal server error"}, status_code=500)


def _route(request: Request) -> str:
    """Method and route template, never the concrete path: call ids in it are bearer tokens."""
    route = request.scope.get("route")
    return f"{request.method} {getattr(route, 'path', '(unmatched)')}"
