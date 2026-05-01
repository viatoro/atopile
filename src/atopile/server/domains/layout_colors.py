"""Shared backend color definitions for layout rendering."""

from __future__ import annotations

Color = tuple[float, float, float, float]

LAYER_COLOR_OVERRIDES: dict[str, Color] = {
    "F.Cu": (0.86, 0.23, 0.22, 0.88),
    "B.Cu": (0.16, 0.28, 0.47, 0.88),
    "In1.Cu": (0.70, 0.58, 0.24, 0.78),
    "In2.Cu": (0.53, 0.40, 0.70, 0.78),
    "F.SilkS": (0.92, 0.90, 0.62, 0.95),
    "B.SilkS": (0.78, 0.86, 0.87, 0.92),
    "F.Mask": (0.70, 0.35, 0.48, 0.42),
    "B.Mask": (0.12, 0.19, 0.34, 0.38),
    "F.Paste": (0.90, 0.80, 0.60, 0.48),
    "B.Paste": (0.66, 0.74, 0.86, 0.48),
    "F.Fab": (0.95, 0.62, 0.45, 0.90),
    "B.Fab": (0.62, 0.73, 0.90, 0.90),
    "F.CrtYd": (0.91, 0.91, 0.91, 0.62),
    "B.CrtYd": (0.80, 0.85, 0.93, 0.62),
    "Edge.Cuts": (0.93, 0.95, 0.95, 1.00),
    "Dwgs.User": (0.70, 0.70, 0.72, 0.65),
    "Cmts.User": (0.74, 0.66, 0.84, 0.65),
}


def layer_color(layer_id: str, kind: str) -> Color:
    if layer_id in LAYER_COLOR_OVERRIDES:
        return LAYER_COLOR_OVERRIDES[layer_id]
    if kind in {"Nets", "PadNumbers"}:
        return (1.0, 1.0, 1.0, 1.0)
    if kind == "Drill":
        return (0.89, 0.82, 0.15, 1.0)
    return (0.50, 0.50, 0.50, 0.50)
