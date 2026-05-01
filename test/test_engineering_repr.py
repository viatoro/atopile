import faebryk.core.faebrykpy as fbrk
import faebryk.library._F as F
from faebryk.libs.util import not_none
from test.compiler.conftest import build_instance


def test_capacitance_interval_repr():
    _, _, _, _, app_instance = build_instance(
        """
        import Capacitor
        import ElectricPower

        module App:
            c1 = new Capacitor
            c1.capacitance = 10uF +/- 1%

            power = new ElectricPower
            power.voltage = 3V to 17V

        """,
        "App",
    )

    cap = F.Capacitor.bind_instance(
        not_none(
            fbrk.EdgeComposition.get_child_by_identifier(
                bound_node=app_instance, child_identifier="c1"
            )
        )
    )

    power = F.ElectricPower.bind_instance(
        not_none(
            fbrk.EdgeComposition.get_child_by_identifier(
                bound_node=app_instance, child_identifier="power"
            )
        )
    )

    cap_literal = cap.capacitance.get().force_extract_superset()
    power_literal = power.voltage.get().force_extract_superset()

    assert cap_literal.pretty_str() == "10µF ±1%"
    assert power_literal.pretty_str() == "3-17V"
    assert cap._simple_repr.get().specs[0].get_value() == "10µF ±1%"
