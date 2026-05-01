# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

import faebryk.core.node as fabll
import faebryk.library._F as F


class RectangularBoardShape(fabll.Node):
    """
    Basic rectangular board outline.

    Emits lines and arcs on the Edge.Cuts layer, centered on the PCB origin.
    """

    _is_module = fabll.Traits.MakeEdge(fabll.is_module.MakeChild())
    _has_part_removed = fabll.Traits.MakeEdge(F.has_part_removed.MakeChild())

    x = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Meter)
    y = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Meter)
    corner_radius = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Meter)

    usage_example = fabll.Traits.MakeEdge(
        F.has_usage_example.MakeChild(
            example="""
            import RectangularBoardShape

            board = new RectangularBoardShape
            board.x = 20mm
            board.y = 45mm
            board.corner_radius = 2mm
            """,
            language=F.has_usage_example.Language.ato,
        ).put_on_type()
    )
