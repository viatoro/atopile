# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

import faebryk.core.node as fabll
import faebryk.library._F as F


class is_transmission_line(fabll.Node):
    """
    Marks an ElectricSignal as a transmission line.
    """

    is_trait = fabll.Traits.MakeEdge(fabll.ImplementsTrait.MakeChild().put_on_type())

    def get_electric_signal(self) -> "F.ElectricSignal":
        return fabll.Traits(self).get_obj_raw().cast(F.ElectricSignal)

    def get_characteristic_impedance(self) -> F.Literals.Numbers:
        signal = self.get_electric_signal()
        impedance = signal.characteristic_impedance.get().try_extract_superset()
        if impedance:
            return impedance
        raise ValueError(
            "No characteristic impedance value set for "
            f"{signal.get_name(accept_no_parent=True)}"
        )

    def get_differential_pair(
        self,
    ) -> "F.DifferentialPair | None":
        """
        Get the differential pair if this transmission line is part of a
        differential pair.
        """
        return self.get_parent_of_type(F.DifferentialPair)
