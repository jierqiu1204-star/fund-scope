from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def healthcheck(request: Request) -> dict[str, str]:
    await request.app.state.db.ping()
    return {"app": "ok", "db": "ok"}
