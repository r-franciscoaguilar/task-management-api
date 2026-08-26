"""One error envelope for every failure the API can produce.

The business asked that invalid actions "fail in a way a client application
could explain to a user". That means two things: a machine-readable code the
client can branch on, and a human-readable message it can surface. Every
response below has the same shape:

    {"error": "<machine_code>", "message": "<human text>", ...context}

Domain code raises an AppError subclass and never builds an HTTP response
itself, so services stay free of web-framework concerns. The handlers
registered here also normalize FastAPI's own validation errors and any stray
HTTPException into the same envelope, so a client never has to parse two
different error formats.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """Base for every expected, client-explainable failure.

    Subclasses pin the HTTP status and the default machine code. Extra keyword
    arguments become additional top-level fields in the response body, which is
    how a caller learns *why* something was rejected (for example the
    `current_status` that made a transition invalid).
    """

    status_code: int = 500
    error_code: str = "internal_error"

    def __init__(
        self, message: str, *, error_code: str | None = None, **context: Any
    ) -> None:
        super().__init__(message)
        self.message = message
        if error_code is not None:
            self.error_code = error_code
        self.context = context

    def to_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "message": self.message, **self.context}


class UnauthenticatedError(AppError):
    """The caller could not be identified at all."""

    status_code = 401
    error_code = "unauthenticated"


class ForbiddenError(AppError):
    """The caller is known, but not allowed to do this."""

    status_code = 403
    error_code = "forbidden"


class NotFoundError(AppError):
    """The resource does not exist, or the caller may not know that it does."""

    status_code = 404
    error_code = "not_found"


class ConflictError(AppError):
    """The request is well-formed but conflicts with current state."""

    status_code = 409
    error_code = "conflict"


class ValidationError(AppError):
    """The request is syntactically valid but semantically wrong."""

    status_code = 422
    error_code = "validation_error"


class InvalidStateTransitionError(ConflictError):
    """A lifecycle move that the state machine does not allow.

    Always carries `current_status` so a client can reconcile its own view --
    which is also what makes a retried transition recoverable despite these
    operations being deliberately non-idempotent.
    """

    error_code = "invalid_state_transition"

    def __init__(self, message: str, *, current_status: str, **context: Any) -> None:
        super().__init__(message, current_status=current_status, **context)


# Stray HTTPExceptions (mostly framework-generated, e.g. an unmatched route)
# get a sensible code so the envelope stays uniform.
_HTTP_ERROR_CODES = {
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_body())

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # FastAPI's default body is {"detail": [...]}. Reshaped here so clients
        # see the same envelope for a malformed body as for a domain rejection.
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "message": "Request validation failed.",
                "details": jsonable_encoder(exc.errors()),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": _HTTP_ERROR_CODES.get(exc.status_code, "http_error"),
                "message": str(exc.detail),
            },
        )
