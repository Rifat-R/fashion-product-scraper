from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ScanRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=120)
    cursor: Optional[str] = None
    page_size: int = Field(20, ge=1, le=100)


class Product(BaseModel):
    site: str
    name: str
    price: Optional[str] = None
    url: str
    sizes: List[str] = Field(default_factory=list)
    availability: Optional[str] = None
    description: Optional[str] = None


class ScanResponse(BaseModel):
    items: List[Product]
    next_cursor: Optional[str]
    total: int


class ScanStartResponse(BaseModel):
    scan_id: str
    sites_total: int


class ScanStatusResponse(ScanResponse):
    sites_total: int
    sites_done: int
    status: str
    logs: List[str]
