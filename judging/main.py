"""Application factory. `uvicorn app:app` (root shim) or `uvicorn judging.main:app`."""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import AdminAuth
from .auth import router as admin_router
from .body_limit import BodySizeLimitMiddleware
from .draft import DraftStore
from .live import LiveHub
from .routes import router
from .services import ServiceError
from .settings import Settings
from .store import StateStore
from .tm import TMError, TMService, TMSource

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
    if kind == "extra_forbidden" and loc:
        return f"Unknown field: {'.'.join(loc)}."
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


async def _tm_error_handler(_: Request, exc: TMError) -> JSONResponse:
    # `message` is built from fixed text, the TM URL and the division: never a secret.
    return JSONResponse(status_code=502, content={"detail": exc.message})


def create_app(
    settings: Settings | None = None,
    *,
    rng: random.Random | None = None,
    tm_source: TMSource | None = None,
) -> FastAPI:
    """`tm_source` replaces the HTTP TM client (tests); TM must still be configured in settings."""
    settings = settings or Settings()
    hub = LiveHub()
    auth = AdminAuth(settings)  # reads/creates the PIN and key files on first use, not here
    store = StateStore(settings)
    draft = DraftStore(settings)
    tm = TMService(settings, draft, store, source=tm_source)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        line = auth.startup_line()  # resolve the secrets now so a bad data dir fails startup
        logger = logging.getLogger("uvicorn.error")
        if auth.log_on_startup:
            logger.info(line)
        if tm.log_on_startup:
            logger.info(tm.startup_line())
        hub.attach(asyncio.get_running_loop())
        tm.open()
        poller = asyncio.create_task(tm.run_poller(), name="tm-poller") if tm.configured else None
        try:
            yield
        finally:
            hub.close()  # end open event streams so shutdown is not held up by them
            tm.close()
            if poller is not None:
                # The TM request itself runs in a daemon thread: this never waits on the network.
                poller.cancel()
                await asyncio.gather(poller, return_exceptions=True)

    app = FastAPI(title="VEX Judging Scheduler", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.draft = draft
    app.state.hub = hub
    app.state.auth = auth
    app.state.tm = tm
    app.state.rng = rng
    store.add_listener(hub.publish_state)
    store.add_listener(tm.on_state)  # printing a schedule stops its TM auto-update
    draft.add_listener(hub.publish_draft)
    tm.add_listener(hub.publish_tm)
    hub.publish_tm(tm.status())  # the hub sends the latest TM status to each new stream
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ServiceError, _service_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(TMError, _tm_error_handler)  # type: ignore[arg-type]
    app.add_middleware(BodySizeLimitMiddleware)
    app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")
    app.include_router(admin_router)
    app.include_router(router)
    return app


app = create_app()
