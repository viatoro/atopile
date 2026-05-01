# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

from dataclasses import dataclass
from enum import Enum

import pytest

import faebryk.core.node as fabll
import faebryk.library._F as F
from faebryk.libs.util import join_if_non_empty, not_none


@dataclass(frozen=True, slots=True)
class Spec:
    param: fabll._ChildField
    format_mode: F.Literals.FormatMode = F.Literals.FormatMode.VALUE
    prefix: str = ""
    suffix: str = ""


class SpecNode(fabll.Node):
    param_ptr_ = F.Collections.Pointer.MakeChild()
    format_mode_ = F.Parameters.EnumParameter.MakeChild(enum_t=F.Literals.FormatMode)
    prefix_ = F.Parameters.StringParameter.MakeChild()
    suffix_ = F.Parameters.StringParameter.MakeChild()

    @property
    def param(self) -> fabll.Node:
        return not_none(self.param_ptr_.get().deref())

    @property
    def prefix(self) -> str:
        return self.prefix_.get().try_extract_singleton() or ""

    @property
    def suffix(self) -> str:
        return self.suffix_.get().try_extract_singleton() or ""

    @property
    def format_mode(self) -> F.Literals.FormatMode:
        return not_none(
            self.format_mode_.get().try_extract_singleton_typed(F.Literals.FormatMode)
        )

    def _try_get_literal(self) -> "F.Literals.is_literal | None":
        param = self.param
        try:
            _, part_picked = param.get_parent_with_trait(F.Pickable.has_part_picked)
        except KeyError:
            pass
        else:
            if (
                picked_literal := part_picked.get_attribute(param.get_name())
            ) is not None:
                return picked_literal

        return param.get_trait(
            F.Parameters.is_parameter_operatable
        ).try_extract_superset()

    def _format_literal(self, literal: "F.Literals.is_literal") -> str:
        literal = literal.switch_cast()
        if number_literal := literal.try_cast(F.Literals.Numbers):
            return number_literal.pretty_str(format_mode=self.format_mode)
        return literal.pretty_str()

    def get_value(self) -> str:
        literal = self._try_get_literal()
        if literal is None:
            return ""

        return join_if_non_empty(
            " ",
            self.prefix,
            self._format_literal(literal),
            self.suffix,
        )

    @classmethod
    def MakeChild(cls, spec: Spec):
        out = fabll._ChildField(cls)
        out.add_dependant(
            F.Collections.Pointer.MakeEdge([out, cls.param_ptr_], [spec.param])
        )
        out.add_dependant(
            F.Literals.AbstractEnums.MakeChild_SetSuperset(
                [out, cls.format_mode_], spec.format_mode
            )
        )
        out.add_dependant(
            F.Literals.Strings.MakeChild_SetSuperset([out, cls.prefix_], spec.prefix)
        )
        out.add_dependant(
            F.Literals.Strings.MakeChild_SetSuperset([out, cls.suffix_], spec.suffix)
        )
        return out


class has_simple_value_representation(fabll.Node):
    Spec = Spec
    FormatMode = F.Literals.FormatMode

    specs_set_ = F.Collections.PointerSet.MakeChild()

    is_trait = fabll.Traits.MakeEdge(fabll.ImplementsTrait.MakeChild().put_on_type())
    is_immutable = fabll.Traits.MakeEdge(fabll.is_immutable.MakeChild()).put_on_type()

    @property
    def specs(self) -> list[SpecNode]:
        return [
            SpecNode.bind_instance(node.instance)
            for node in self.specs_set_.get().as_list()
        ]

    @classmethod
    def MakeChild(cls, *specs: Spec):
        out = fabll._ChildField(cls)
        for spec in specs:
            spec_node = SpecNode.MakeChild(spec)
            out.add_dependant(spec_node)
            out.add_dependant(
                F.Collections.PointerSet.MakeEdge([out, cls.specs_set_], [spec_node])
            )
        return out

    def get_value(self) -> str:
        return join_if_non_empty(" ", *(spec.get_value() for spec in self.specs))


def make_graph_and_typegraph():
    import faebryk.core.faebrykpy as fbrk
    from faebryk.core import graph

    g = graph.GraphView.create()
    tg = fbrk.TypeGraph.create(g=g)
    return g, tg


def make_kiloohm_unit(g, tg) -> "F.Units.is_unit":
    from faebryk.library.Units import BasisVector, is_unit

    class _Kiloohm(fabll.Node):
        unit_vector_arg = BasisVector(kilogram=1, meter=2, second=-3, ampere=-2)
        is_unit_trait = fabll.Traits.MakeEdge(
            is_unit.MakeChild(("kΩ", "kohm"), unit_vector_arg, multiplier=1000.0)
        ).put_on_type()
        can_be_operand = fabll.Traits.MakeEdge(
            F.Parameters.can_be_operand.MakeChild()
        ).put_on_type()

    return _Kiloohm.bind_typegraph(tg=tg).as_type_node().is_unit_trait.get()


def make_milliohm_unit(g, tg) -> "F.Units.is_unit":
    from faebryk.library.Units import BasisVector, is_unit

    class _Milliohm(fabll.Node):
        unit_vector_arg = BasisVector(kilogram=1, meter=2, second=-3, ampere=-2)
        is_unit_trait = fabll.Traits.MakeEdge(
            is_unit.MakeChild(("mΩ", "mohm"), unit_vector_arg, multiplier=0.001)
        ).put_on_type()
        can_be_operand = fabll.Traits.MakeEdge(
            F.Parameters.can_be_operand.MakeChild()
        ).put_on_type()

    return _Milliohm.bind_typegraph(tg=tg).as_type_node().is_unit_trait.get()


class TestHasSimpleValueRepresentation:
    def test_repr_chain_basic(self):
        g, tg = make_graph_and_typegraph()

        class _TestModule(fabll.Node):
            param1 = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Volt)
            param2 = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Ampere)
            param3 = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Volt)

            S = has_simple_value_representation.Spec
            _simple_repr = fabll.Traits.MakeEdge(
                has_simple_value_representation.MakeChild(
                    S(
                        param=param1,
                        format_mode=has_simple_value_representation.FormatMode.RANGE,
                        prefix="TM",
                    ),
                    S(param=param2, suffix="P2"),
                    S(
                        param=param3,
                        format_mode=(
                            has_simple_value_representation.FormatMode.VALUE_WITH_TOLERANCE
                        ),
                        suffix="P3",
                    ),
                )
            )

        m = _TestModule.bind_typegraph(tg).create_instance(g=g)
        m.param1.get().set_superset(
            g=g,
            value=F.Literals.Numbers.bind_typegraph(tg)
            .create_instance(g=g)
            .setup_from_min_max(
                min=10.0,
                max=20,
                unit=F.Units.Volt.bind_typegraph(tg=tg).as_type_node().is_unit.get(),
            ),
        )
        m.param2.get().set_superset(
            g=g,
            value=F.Literals.Numbers.bind_typegraph(tg)
            .create_instance(g=g)
            .setup_from_singleton(
                value=5.0,
                unit=F.Units.Ampere.bind_typegraph(tg=tg).as_type_node().is_unit.get(),
            ),
        )
        m.param3.get().set_superset(
            g=g,
            value=F.Literals.Numbers.bind_typegraph(tg)
            .create_instance(g=g)
            .setup_from_singleton(
                value=10.0,
                unit=F.Units.Volt.bind_typegraph(tg=tg).as_type_node().is_unit.get(),
            ),
        )

        assert m._simple_repr.get().get_value() == "TM 10-20V 5A P2 10V P3"

    @pytest.mark.usefixtures("setup_project_config")
    def test_repr_with_picked_attributes(self, monkeypatch):
        from unittest.mock import Mock

        from faebryk.libs.picker.api import models
        from faebryk.libs.picker.api.models import Component

        monkeypatch.setattr(models, "lcsc_attach", Mock())

        g, tg = make_graph_and_typegraph()

        class _TestModule(fabll.Node):
            _is_module = fabll.Traits.MakeEdge(fabll.is_module.MakeChild())
            param1 = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Volt)
            param2 = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Ampere)

            S = has_simple_value_representation.Spec
            _simple_repr = fabll.Traits.MakeEdge(
                has_simple_value_representation.MakeChild(
                    S(
                        param=param1,
                        format_mode=(
                            has_simple_value_representation.FormatMode.VALUE_WITH_TOLERANCE
                        ),
                        prefix="V:",
                    ),
                    S(param=param2, suffix="A"),
                )
            )

            _pickable = fabll.Traits.MakeEdge(
                F.Pickable.is_pickable_by_type.MakeChild(
                    endpoint=F.Pickable.is_pickable_by_type.Endpoint.RESISTORS,
                    params={"param1": param1, "param2": param2},
                )
            )
            _can_attach = fabll.Traits.MakeEdge(
                F.Footprints.can_attach_to_footprint.MakeChild()
            )

        m = _TestModule.bind_typegraph(tg).create_instance(g=g)

        lit1 = (
            F.Literals.Numbers.bind_typegraph(tg)
            .create_instance(g=g)
            .setup_from_center_rel(
                center=12.0,
                rel=0.05,
                unit=F.Units.Volt.bind_typegraph(tg=tg).as_type_node().is_unit.get(),
            )
        )
        lit2 = (
            F.Literals.Numbers.bind_typegraph(tg)
            .create_instance(g=g)
            .setup_from_singleton(
                value=2.5,
                unit=F.Units.Ampere.bind_typegraph(tg=tg).as_type_node().is_unit.get(),
            )
        )

        attributes: dict[str, dict] = {}
        for name, lit in [("param1", lit1), ("param2", lit2)]:
            serialized = lit.is_literal.get().serialize()
            if serialized is not None:
                attributes[name] = serialized

        component = Component(
            lcsc=12345,
            manufacturer_name="TestMfr",
            part_number="TestPart",
            package="0402",
            datasheet_url="",
            description="Test component",
            is_basic=0,
            is_preferred=0,
            stock=100,
            price=[],
            attributes=attributes,
        )
        component.attach(
            m.get_trait(F.Pickable.is_pickable_by_type).get_trait(
                F.Pickable.is_pickable
            ),
            qty=1,
        )

        assert m._simple_repr.get().get_value() == "V: 12V ±5% 2.5A A"

    def test_repr_chain_non_number(self):
        g, tg = make_graph_and_typegraph()

        class TestEnum(Enum):
            A = "AS"
            B = "BS"

        class _TestModule(fabll.Node):
            param1 = F.Parameters.EnumParameter.MakeChild(TestEnum)
            param2 = F.Parameters.BooleanParameter.MakeChild()

            S = has_simple_value_representation.Spec
            _simple_repr = fabll.Traits.MakeEdge(
                has_simple_value_representation.MakeChild(
                    S(param=param1),
                    S(param=param2, prefix="P2:"),
                )
            )

        m = _TestModule.bind_typegraph(tg).create_instance(g=g)
        test_enum_lit = (
            F.Literals.AbstractEnums.bind_typegraph(tg=tg)
            .create_instance(g=g)
            .setup(TestEnum.A)
        )
        m.param1.get().is_parameter_operatable.get().set_superset(
            g=g,
            value=test_enum_lit,
        )
        m.param2.get().set_singleton(value=True)

        assert m._simple_repr.get().get_value() == "A P2: true"

    def test_repr_chain_no_literal(self):
        g, tg = make_graph_and_typegraph()

        class _TestModule(fabll.Node):
            param1 = F.Parameters.NumericParameter.MakeChild(
                unit=F.Units.Volt, domain=F.NumberDomain.Args(negative=True)
            )
            param2 = F.Parameters.NumericParameter.MakeChild(
                unit=F.Units.Ampere, domain=F.NumberDomain.Args(negative=True)
            )
            param3 = F.Parameters.NumericParameter.MakeChild(
                unit=F.Units.Volt, domain=F.NumberDomain.Args(negative=True)
            )

            S = has_simple_value_representation.Spec
            _simple_repr = fabll.Traits.MakeEdge(
                has_simple_value_representation.MakeChild(
                    S(param=param1),
                    S(param=param2),
                    S(param=param3),
                )
            )

        m = _TestModule.bind_typegraph(tg).create_instance(g=g)
        assert m._simple_repr.get().get_value() == ""

        m.param1.get().set_superset(
            g=g,
            value=F.Literals.Numbers.bind_typegraph(tg)
            .create_instance(g=g)
            .setup_from_singleton(
                value=10.0,
                unit=F.Units.Volt.bind_typegraph(tg=tg).as_type_node().is_unit.get(),
            ),
        )
        assert m._simple_repr.get().get_value() == "10V"

    def test_repr_value_with_tolerance(self):
        g, tg = make_graph_and_typegraph()

        class _TestModule(fabll.Node):
            capacitance = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Farad)

            S = has_simple_value_representation.Spec
            _simple_repr = fabll.Traits.MakeEdge(
                has_simple_value_representation.MakeChild(
                    S(
                        param=capacitance,
                        format_mode=(
                            has_simple_value_representation.FormatMode.VALUE_WITH_TOLERANCE
                        ),
                    ),
                )
            )

        m = _TestModule.bind_typegraph(tg).create_instance(g=g)
        m.capacitance.get().set_superset(
            g=g,
            value=F.Literals.Numbers.bind_typegraph(tg)
            .create_instance(g=g)
            .setup_from_center_rel(
                center=100e-9,
                rel=0.1,
                unit=F.Units.Farad.bind_typegraph(tg=tg).as_type_node().is_unit.get(),
            ),
        )

        assert m._simple_repr.get().get_value() == "100nF ±10%"

    def test_repr_value(self):
        g, tg = make_graph_and_typegraph()

        class _TestModule(fabll.Node):
            capacitance = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Farad)

            S = has_simple_value_representation.Spec
            _simple_repr = fabll.Traits.MakeEdge(
                has_simple_value_representation.MakeChild(S(param=capacitance))
            )

        m = _TestModule.bind_typegraph(tg).create_instance(g=g)
        m.capacitance.get().set_superset(
            g=g,
            value=F.Literals.Numbers.bind_typegraph(tg)
            .create_instance(g=g)
            .setup_from_center_rel(
                center=100e-9,
                rel=0.1,
                unit=F.Units.Farad.bind_typegraph(tg=tg).as_type_node().is_unit.get(),
            ),
        )

        assert m._simple_repr.get().get_value() == "100nF"

    def test_repr_display_unit_conversion(self):
        from faebryk.library.Units import has_unit

        g, tg = make_graph_and_typegraph()

        kohm_unit = make_kiloohm_unit(g=g, tg=tg)
        param = F.Parameters.NumericParameter.bind_typegraph(tg).create_instance(g=g)
        param.setup(is_unit=kohm_unit)

        api_lit = F.Literals.Numbers.create_instance(g=g, tg=tg)
        numeric_set = F.Literals.NumericSet.create_instance(
            g=g, tg=tg
        ).setup_from_values(values=[(47000.0, 47000.0)])
        api_lit.numeric_set_ptr.get().point(numeric_set)
        base_ohm = F.Units.Ohm.bind_typegraph(tg=tg).as_type_node().is_unit.get()
        base_ohm = base_ohm.copy_into(g=g)
        fabll.Traits.create_and_add_instance_to(api_lit, has_unit).setup(
            is_unit=base_ohm
        )

        assert api_lit.pretty_str() == "47kΩ"

        user_lit = (
            F.Literals.Numbers.bind_typegraph(tg)
            .create_instance(g=g)
            .setup_from_singleton(value=47.0, unit=kohm_unit)
        )
        assert user_lit.pretty_str() == "47kΩ"

    def test_repr_display_unit_mismatch(self):
        from faebryk.library.Units import has_unit

        g, tg = make_graph_and_typegraph()

        mohm_unit = make_milliohm_unit(g=g, tg=tg)
        param = F.Parameters.NumericParameter.bind_typegraph(tg).create_instance(g=g)
        param.setup(is_unit=mohm_unit)

        base_ohm = F.Units.Ohm.bind_typegraph(tg=tg).as_type_node().is_unit.get()

        api_lit = F.Literals.Numbers.create_instance(g=g, tg=tg)
        numeric_set = F.Literals.NumericSet.create_instance(
            g=g, tg=tg
        ).setup_from_values(values=[(0.5, 0.5)])
        api_lit.numeric_set_ptr.get().point(numeric_set)
        base_ohm_copy = base_ohm.copy_into(g=g)
        fabll.Traits.create_and_add_instance_to(api_lit, has_unit).setup(
            is_unit=base_ohm_copy
        )

        assert api_lit.pretty_str() == "500mΩ"

        user_lit = (
            F.Literals.Numbers.bind_typegraph(tg)
            .create_instance(g=g)
            .setup_from_singleton(value=0.5, unit=base_ohm)
        )
        assert user_lit.pretty_str() == "500mΩ"

    def test_resistor_value_representation(self):
        from faebryk.library.Resistor import Resistor

        g, tg = make_graph_and_typegraph()

        resistor = Resistor.bind_typegraph(tg=tg).create_instance(g=g)
        kohm_unit = make_kiloohm_unit(g=g, tg=tg)

        resistor.resistance.get().set_superset(
            g=g,
            value=F.Literals.Numbers.bind_typegraph(tg)
            .create_instance(g=g)
            .setup_from_center_rel(
                center=10.0,
                rel=0.01,
                unit=kohm_unit,
            ),
        )
        resistor.max_power.get().set_superset(
            g=g,
            value=F.Literals.Numbers.bind_typegraph(tg)
            .create_instance(g=g)
            .setup_from_singleton(
                value=0.125,
                unit=F.Units.Watt.bind_typegraph(tg=tg).as_type_node().is_unit.get(),
            ),
        )
        resistor.max_voltage.get().set_superset(
            g=g,
            value=F.Literals.Numbers.bind_typegraph(tg)
            .create_instance(g=g)
            .setup_from_singleton(
                value=10.0,
                unit=F.Units.Volt.bind_typegraph(tg=tg).as_type_node().is_unit.get(),
            ),
        )

        assert resistor._simple_repr.get().specs[0].get_value() == "10kΩ ±1%"
        assert resistor._simple_repr.get().get_value() == "10kΩ ±1% 125mW 10V"
