# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

"""
Trait marking a numeric parameter as being drawn from an E-series of preferred values.

When attached to a parameter (e.g. Resistor.resistance), it tells the E-series solver
that this parameter should be resolved to a discrete standard value rather than a
continuous range.

Each instance carries:
- series: which E-series (E6, E12, E24, E48, E96, E192)
- tolerance: fractional tolerance for the series (e.g. 0.01 for 1%)
- practical_range: sensible bounds for enumeration when the solver hasn't
  propagated tighter constraints (e.g. 1Ω–10MΩ for resistors)
"""

import math
from collections.abc import Iterator
from enum import IntEnum, auto

import faebryk.core.node as fabll
import faebryk.library._F as F

# ---------------------------------------------------------------------------
# E-series base value tables (one decade, 1.0 – 9.x)
# ---------------------------------------------------------------------------

# fmt: off
E6_BASE = (1.0, 1.5, 2.2, 3.3, 4.7, 6.8)

E12_BASE = (
    1.0, 1.2, 1.5, 1.8, 2.2, 2.7,
    3.3, 3.9, 4.7, 5.6, 6.8, 8.2,
)

E24_BASE = (
    1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
    3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1,
)

E48_BASE = (
    1.00, 1.05, 1.10, 1.15, 1.21, 1.27, 1.33, 1.40, 1.47, 1.54, 1.62, 1.69,
    1.78, 1.87, 1.96, 2.05, 2.15, 2.26, 2.37, 2.49, 2.61, 2.74, 2.87, 3.01,
    3.16, 3.32, 3.48, 3.65, 3.83, 4.02, 4.22, 4.42, 4.64, 4.87, 5.11, 5.36,
    5.62, 5.90, 6.19, 6.49, 6.81, 7.15, 7.50, 7.87, 8.25, 8.66, 9.09, 9.53,
)

E96_BASE = (
    1.00, 1.02, 1.05, 1.07, 1.10, 1.13, 1.15, 1.18, 1.21, 1.24, 1.27, 1.30,
    1.33, 1.37, 1.40, 1.43, 1.47, 1.50, 1.54, 1.58, 1.62, 1.65, 1.69, 1.74,
    1.78, 1.82, 1.87, 1.91, 1.96, 2.00, 2.05, 2.10, 2.15, 2.21, 2.26, 2.32,
    2.37, 2.43, 2.49, 2.55, 2.61, 2.67, 2.74, 2.80, 2.87, 2.94, 3.01, 3.09,
    3.16, 3.24, 3.32, 3.40, 3.48, 3.57, 3.65, 3.74, 3.83, 3.92, 4.02, 4.12,
    4.22, 4.32, 4.42, 4.53, 4.64, 4.75, 4.87, 4.99, 5.11, 5.23, 5.36, 5.49,
    5.62, 5.76, 5.90, 6.04, 6.19, 6.34, 6.49, 6.65, 6.81, 6.98, 7.15, 7.32,
    7.50, 7.68, 7.87, 8.06, 8.25, 8.45, 8.66, 8.87, 9.09, 9.31, 9.53, 9.76,
)

E192_BASE = (
    1.00, 1.01, 1.02, 1.04, 1.05, 1.06, 1.07, 1.09, 1.10, 1.11, 1.13, 1.14,
    1.15, 1.17, 1.18, 1.20, 1.21, 1.23, 1.24, 1.26, 1.27, 1.29, 1.30, 1.32,
    1.33, 1.35, 1.37, 1.38, 1.40, 1.42, 1.43, 1.45, 1.47, 1.49, 1.50, 1.52,
    1.54, 1.56, 1.58, 1.60, 1.62, 1.64, 1.65, 1.68, 1.69, 1.72, 1.74, 1.76,
    1.78, 1.80, 1.82, 1.84, 1.87, 1.89, 1.91, 1.93, 1.96, 1.98, 2.00, 2.03,
    2.05, 2.08, 2.10, 2.13, 2.15, 2.18, 2.21, 2.23, 2.26, 2.29, 2.32, 2.34,
    2.37, 2.40, 2.43, 2.46, 2.49, 2.52, 2.55, 2.58, 2.61, 2.64, 2.67, 2.71,
    2.74, 2.77, 2.80, 2.84, 2.87, 2.91, 2.94, 2.98, 3.01, 3.05, 3.09, 3.12,
    3.16, 3.20, 3.24, 3.28, 3.32, 3.36, 3.40, 3.44, 3.48, 3.52, 3.57, 3.61,
    3.65, 3.70, 3.74, 3.79, 3.83, 3.88, 3.92, 3.97, 4.02, 4.07, 4.12, 4.17,
    4.22, 4.27, 4.32, 4.37, 4.42, 4.48, 4.53, 4.59, 4.64, 4.70, 4.75, 4.81,
    4.87, 4.93, 4.99, 5.05, 5.11, 5.17, 5.23, 5.30, 5.36, 5.42, 5.49, 5.56,
    5.62, 5.69, 5.76, 5.83, 5.90, 5.97, 6.04, 6.12, 6.19, 6.26, 6.34, 6.42,
    6.49, 6.57, 6.65, 6.73, 6.81, 6.90, 6.98, 7.06, 7.15, 7.23, 7.32, 7.41,
    7.50, 7.59, 7.68, 7.77, 7.87, 7.96, 8.06, 8.16, 8.25, 8.35, 8.45, 8.56,
    8.66, 8.76, 8.87, 8.98, 9.09, 9.20, 9.31, 9.42, 9.53, 9.65, 9.76, 9.88,
)
# fmt: on

SERIES_TABLES: dict["is_eseries_value.Series", tuple[float, ...]] = {}


def eseries_values_in_range(
    series: "is_eseries_value.Series", tolerance: float, low: float, high: float
) -> Iterator[tuple[float, float, float]]:
    """
    Yield E-series nominal values whose tolerance bands overlap [low, high].

    Each item is (nominal, min, max).
    """
    base = SERIES_TABLES[series]

    # Find the decade range we need to cover
    if low <= 0 or high <= 0:
        return

    min_decade = int(math.floor(math.log10(low / base[-1])))
    max_decade = int(math.ceil(math.log10(high / base[0])))

    yield from (
        (m, m * (1.0 - tolerance), m * (1.0 + tolerance))
        for decade in range(min_decade, max_decade + 1)
        for nominal_base in base
        if (m := nominal_base * (10.0**decade)) * (1.0 - tolerance) >= low
        and m * (1.0 + tolerance) <= high
    )


class is_eseries_value(fabll.Node):
    """
    Marks a numeric parameter as drawn from a standard E-series.

    Attached to parameters like Resistor.resistance or Capacitor.capacitance
    to indicate the solver should resolve them to discrete preferred values.
    """

    class Series(IntEnum):
        E6 = auto()
        E12 = auto()
        E24 = auto()
        E48 = auto()
        E96 = auto()
        E192 = auto()

    is_trait = fabll.Traits.MakeEdge(fabll.ImplementsTrait.MakeChild().put_on_type())

    series_ = F.Parameters.EnumParameter.MakeChild(enum_t=Series)
    tolerance_ = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Dimensionless)
    practical_range_ = F.Parameters.NumericParameter.MakeChild(
        unit=F.Units.Dimensionless
    )

    @property
    def series(self) -> Series:
        return self.series_.get().force_extract_singleton_typed(self.Series)

    @property
    def tolerance(self) -> float:
        return (
            self.tolerance_.get()
            .is_parameter_operatable.get()
            .force_extract_superset()
            .switch_cast()
            .cast(F.Literals.Numbers)
            .get_single()
        )

    @property
    def practical_min(self) -> float:
        return (
            self.practical_range_.get()
            .is_parameter_operatable.get()
            .force_extract_superset()
            .switch_cast()
            .cast(F.Literals.Numbers)
            .get_min_value()
        )

    @property
    def practical_max(self) -> float:
        return (
            self.practical_range_.get()
            .is_parameter_operatable.get()
            .force_extract_superset()
            .switch_cast()
            .cast(F.Literals.Numbers)
            .get_max_value()
        )

    @classmethod
    def MakeChild(
        cls, series: Series, tolerance: float, practical_range: tuple[float, float]
    ) -> fabll._ChildField["is_eseries_value"]:
        out = fabll._ChildField(is_eseries_value)
        out.add_dependant(
            F.Literals.AbstractEnums.MakeChild_SetSuperset([out, cls.series_], series)
        )
        out.add_dependant(
            F.Literals.Numbers.MakeChild_SetSuperset(
                [out, cls.tolerance_], min=tolerance, max=tolerance
            )
        )
        out.add_dependant(
            F.Literals.Numbers.MakeChild_SetSuperset(
                [out, cls.practical_range_],
                min=practical_range[0],
                max=practical_range[1],
            )
        )
        return out

    def get_candidates_in_range(
        self, low: float, high: float
    ) -> Iterator[tuple[float, float, float]]:
        """
        Yield E-series candidates whose tolerance bands fit within [low, high].

        Clamps to practical_range.
        Each item is (nominal, min, max).
        """
        effective_low = max(low, self.practical_min)
        effective_high = min(high, self.practical_max)
        if effective_low > effective_high:
            return
        yield from eseries_values_in_range(
            self.series, self.tolerance, effective_low, effective_high
        )


# Populate lookup table
SERIES_TABLES[is_eseries_value.Series.E6] = E6_BASE
SERIES_TABLES[is_eseries_value.Series.E12] = E12_BASE
SERIES_TABLES[is_eseries_value.Series.E24] = E24_BASE
SERIES_TABLES[is_eseries_value.Series.E48] = E48_BASE
SERIES_TABLES[is_eseries_value.Series.E96] = E96_BASE
SERIES_TABLES[is_eseries_value.Series.E192] = E192_BASE
