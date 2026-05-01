"""Pydantic models for PCB diff computation."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from atopile.server.domains.layout_models import PointXY, RenderModel


class DiffStatus(str, Enum):
    unchanged = "unchanged"
    added = "added"
    deleted = "deleted"
    moved = "moved"
    modified = "modified"


class DiffConfig(BaseModel):
    position_tolerance: float = 0.01
    angle_tolerance: float = 0.1


class DiffElementStatus(BaseModel):
    uuid_a: str | None = None
    uuid_b: str | None = None
    element_type: str
    status: DiffStatus
    reference: str | None = None
    name: str | None = None
    value: str | None = None
    net: int | None = None
    net_name: str | None = None
    position_a: PointXY | None = None
    position_b: PointXY | None = None


class DiffResult(BaseModel):
    model_a: RenderModel
    model_b: RenderModel
    elements: list[DiffElementStatus]
    net_names: dict[int, str] = Field(default_factory=dict)
    summary: dict[str, int] = Field(default_factory=dict)
