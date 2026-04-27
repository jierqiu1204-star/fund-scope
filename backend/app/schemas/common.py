from __future__ import annotations

from pydantic import BaseModel


class PaginatedResponse(BaseModel):
    total: int
    page: int = 1
    size: int = 50
