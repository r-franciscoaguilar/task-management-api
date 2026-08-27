"""One error envelope for every failure:

    {"error": "<machine_code>", "message": "<human text>", ...context}

Domain code raises AppError and never builds a response, so services stay free
of HTTP concerns. FastAPI's own validation errors and stray HTTPExceptions are
normalized into the same shape, so clients parse one format.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """Base for expected, client-explainable failures.

    Subclasses pin the status and machine code. Extra keyword arguments become
    top-level response fields explaining why -- e.g. `current_status`.
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
    """A lifecycle move the state machine forbids.

    Always carries `current_status`, which is how a client that retried after a
    timeout reconciles its view despite transitions being non-idempotent.
    """

    error_code = "invalid_state_transition"

    def __init__(self, message: str, *, current_status: str, **context: Any) -> None:
        super().__init__(message, current_status=current_status, **context)


# Framework-generated HTTPExceptions (e.g. unmatched route) need a code too.
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
        # FastAPI's default is {"detail": [...]}; reshaped to match the rest.
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
