# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

import logging
from enum import Enum, auto
from typing import Any

import faebryk.core.faebrykpy as fbrk
import faebryk.core.graph as graph
import faebryk.core.node as fabll
import faebryk.library._F as F

logger = logging.getLogger(__name__)


class is_data_interface(fabll.Node):
    """Marks an interface as data interfaces."""

    is_trait = fabll.Traits.MakeEdge(fabll.ImplementsTrait.MakeChild().put_on_type())
    is_immutable = fabll.Traits.MakeEdge(fabll.is_immutable.MakeChild()).put_on_type()

    @staticmethod
    def get_data_interface_groups(
        g: graph.GraphView, tg: fbrk.TypeGraph
    ) -> list[set[fabll.Node]]:
        """
        Discover all is_data_interface implementors, group their owner interfaces into
        data interface groups, and return sets of data interface group members.
        """
        implementors = list(
            fabll.Traits.get_implementors(is_data_interface.bind_typegraph(tg), g=g)
        )

        if not implementors:
            return []

        # Group implementors by their owner interface
        owners: set[fabll.Node] = set()
        for impl in implementors:
            owners.add(fabll.Traits(impl).get_obj_raw())

        # Group interfaces into buses
        buses = fabll.is_interface.group_into_buses(owners)

        result: list[set[fabll.Node]] = []
        processed: set[frozenset[fabll.Node]] = set()
        for bus_interfaces in buses.values():
            bus_id = frozenset(bus_interfaces)
            if bus_id in processed:
                continue
            processed.add(bus_id)
            result.append(bus_interfaces)

        return result


class has_data_interface_role(fabll.Node):
    """
    Role marker for data interface interfaces.

    Marks an individual data interface instance with its role
    (e.g., CONTROLLER, TARGET, NODE).
    """

    is_trait = fabll.Traits.MakeEdge(fabll.ImplementsTrait.MakeChild().put_on_type())
    is_immutable = fabll.Traits.MakeEdge(fabll.is_immutable.MakeChild()).put_on_type()

    class Role(Enum):
        CONTROLLER = auto()
        TARGET = auto()
        NODE = auto()
        END_NODE = auto()
        PASSIVE = auto()

    role_ = F.Parameters.EnumParameter.MakeChild(enum_t=Role)

    @classmethod
    def MakeChild(cls, role: str | list[Role]) -> fabll._ChildField[Any]:
        # From ato: role="CONTROLLER" or role="CONTROLLER,TARGET"
        # From Python: role=[BusRole.CONTROLLER]
        if isinstance(role, str):
            role = [cls.Role[r.strip()] for r in role.split(",")]
        out = fabll._ChildField(cls)
        out.add_dependant(
            F.Literals.AbstractEnums.MakeChild_SetSuperset(
                [out, cls.role_],
                *role,
            )
        )
        return out

    def get_roles(self) -> set[Role]:
        lit = self.role_.get().try_extract_superset()
        if lit is None:
            return set()
        return set(lit.get_values_typed(self.Role))
