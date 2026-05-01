"""Pre-check readiness computations for the autolayout UI.

Pure functions over the preflight summary dict — no service state,
no I/O. The service exposes thin wrappers that pass in its stored
preflight snapshot.
"""

from __future__ import annotations

from typing import Any

from atopile.autolayout.models import PreCheckItem


def placement_readiness(preflight: dict[str, Any] | None) -> list[PreCheckItem]:
    """Pre-checks that must pass before submitting a placement job."""
    if preflight is None:
        return [
            PreCheckItem(label="Board", passed=False, detail="Waiting for build"),
            PreCheckItem(label="Components", passed=False, detail="Waiting for build"),
        ]
    board_area = preflight.get("boardAreaMm2")
    has_outline = board_area is not None and board_area > 0
    width = preflight.get("boardWidthMm")
    height = preflight.get("boardHeightMm")
    outside_count = int(preflight.get("componentsOutsideBoard") or 0)
    component_count = int(preflight.get("componentCount") or 0)
    has_components_outside = outside_count > 0

    board_detail = (
        f"{width:.1f} x {height:.1f} mm"
        if has_outline and width is not None and height is not None
        else "No outline found"
    )
    if has_components_outside:
        components_detail = f"{outside_count} outside board"
    elif component_count > 0:
        components_detail = "All placed on board"
    else:
        components_detail = "None found"

    return [
        PreCheckItem(label="Board", passed=has_outline, detail=board_detail),
        PreCheckItem(
            label="Components",
            passed=has_components_outside,
            detail=components_detail,
        ),
    ]


def routing_readiness(preflight: dict[str, Any] | None) -> list[PreCheckItem]:
    """Pre-checks that must pass before submitting a routing job."""
    if preflight is None:
        return [
            PreCheckItem(label="Board", passed=False, detail="Waiting for build"),
            PreCheckItem(label="Components", passed=False, detail="Waiting for build"),
        ]
    board_area = preflight.get("boardAreaMm2")
    has_outline = board_area is not None and board_area > 0
    width = preflight.get("boardWidthMm")
    height = preflight.get("boardHeightMm")
    inside_count = int(preflight.get("componentsInsideBoard") or 0)
    outside_count = int(preflight.get("componentsOutsideBoard") or 0)
    all_inside = inside_count > 0 and outside_count == 0

    board_detail = (
        f"{width:.1f} x {height:.1f} mm"
        if has_outline and width is not None and height is not None
        else "No outline found"
    )
    if all_inside:
        components_detail = f"{inside_count} placed on board"
    elif outside_count > 0:
        components_detail = f"{outside_count} outside board"
    else:
        components_detail = "None found"

    return [
        PreCheckItem(label="Board", passed=has_outline, detail=board_detail),
        PreCheckItem(label="Components", passed=all_inside, detail=components_detail),
    ]
