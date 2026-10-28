"""HTTP endpoints. Thin: validate the body, run a service inside a store transaction."""
from __future__ import annotations

import random
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from . import services
from .models import (
    ActiveScheduleRequest,
    GenerateNoShowRequest,
    GenerateRequest,
    SnapshotRequest,
    TeamRequest,
)
from .settings import Settings
from .store import StateStore, StateView

router = APIRouter()


@dataclass(frozen=True)
class AppContext:
    settings: Settings
    store: StateStore
    rng: random.Random | None


def get_context(request: Request) -> AppContext:
    state = request.app.state
    return AppContext(settings=state.settings, store=state.store, rng=state.rng)


@router.get("/", include_in_schema=False)
def index(ctx: AppContext = Depends(get_context)) -> FileResponse:
    return FileResponse(ctx.settings.static_dir / "index.html")


@router.get("/api/state", response_model=None)
def get_state(ctx: AppContext = Depends(get_context)) -> StateView:
    return ctx.store.view()


@router.post("/api/generate", response_model=None)
def generate(payload: GenerateRequest, ctx: AppContext = Depends(get_context)) -> StateView:
    tz = ctx.settings.event_tz
    return ctx.store.update(lambda state: services.generate(state, payload, tz, ctx.rng))


@router.post("/api/checkoff", response_model=None)
def checkoff(payload: TeamRequest, ctx: AppContext = Depends(get_context)) -> StateView:
    return ctx.store.update(lambda state: services.checkoff(state, payload.team))


@router.post("/api/noshow", response_model=None)
def noshow(payload: TeamRequest, ctx: AppContext = Depends(get_context)) -> StateView:
    return ctx.store.update(lambda state: services.noshow(state, payload.team))


@router.post("/api/not-competing", response_model=None)
def not_competing(payload: TeamRequest, ctx: AppContext = Depends(get_context)) -> StateView:
    return ctx.store.update(lambda state: services.mark_not_competing(state, payload.team))


@router.post("/api/active-schedule", response_model=None)
def set_active_schedule(
    payload: ActiveScheduleRequest, ctx: AppContext = Depends(get_context)
) -> StateView:
    return ctx.store.update(lambda state: services.set_active_schedule(state, payload.schedule_id))


@router.post("/api/snapshot-print", response_model=None)
def snapshot_print(
    payload: SnapshotRequest | None = None, ctx: AppContext = Depends(get_context)
) -> StateView:
    label = (payload or SnapshotRequest()).label
    return ctx.store.update(lambda state: services.snapshot_print(state, label))


@router.post("/api/reset", response_model=None)
def reset_all(ctx: AppContext = Depends(get_context)) -> StateView:
    return ctx.store.reset()


@router.post("/api/generate-noshow", response_model=None)
def generate_no_show(
    payload: GenerateNoShowRequest | None = None, ctx: AppContext = Depends(get_context)
) -> StateView:
    req = payload or GenerateNoShowRequest()
    tz = ctx.settings.event_tz
    return ctx.store.update(lambda state: services.generate_no_show(state, req, tz, ctx.rng))
