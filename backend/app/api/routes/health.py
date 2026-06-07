from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def healthcheck(request: Request) -> dict[str, str]:
    await request.app.state.db.ping()
    return {"app": "ok", "db": "ok"}


@router.get("/api/health")
async def api_healthcheck(request: Request) -> dict[str, str]:
    return await healthcheck(request)
