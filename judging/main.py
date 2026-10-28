"""Application factory. `uvicorn app:app` (root shim) or `uvicorn judging.main:app`."""
from __future__ import annotations

import random
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .routes import router
from .services import ServiceError
from .settings import Settings
from .store import StateStore

_VALUE_ERROR_PREFIX = "Value error, "


def _describe_validation_error(error: dict[str, Any]) -> str:
    kind = error.get("type")
    loc = [str(part) for part in error.get("loc", ()) if part != "body"]
    if kind == "json_invalid":
        return "Request body must be valid JSON."
    if kind == "missing" and not loc:
        return "Request body is required."
    if kind in {"model_attributes_type", "dict_type", "model_type"} and not loc:
        return "Request body must be a JSON object."
    message = str(error.get("msg", "Invalid value."))
    if message.startswith(_VALUE_ERROR_PREFIX):
        return message[len(_VALUE_ERROR_PREFIX):]
    return f"{'.'.join(loc)}: {message}" if loc else message


async def _validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    # The frontend shows `detail` as text, so flatten pydantic's list into one string (400).
    messages: list[str] = []
    for error in exc.errors():
        text = _describe_validation_error(error)
        if text not in messages:
            messages.append(text)
    return JSONResponse(status_code=400, content={"detail": " ".join(messages) or "Invalid request."})


async def _service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def create_app(settings: Settings | None = None, *, rng: random.Random | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="VEX Judging Scheduler")
    app.state.settings = settings
    app.state.store = StateStore(settings)
    app.state.rng = rng
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ServiceError, _service_error_handler)  # type: ignore[arg-type]
    app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")
    app.include_router(router)
    return app


app = create_app()
