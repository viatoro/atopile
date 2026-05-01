# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

"""Tests for marked discrete E-series solve problems."""

import math
import time

import faebryk.core.faebrykpy as fbrk
import faebryk.core.graph as graph
import faebryk.core.node as fabll
import faebryk.library._F as F
from faebryk.core.solver import Solver
from faebryk.libs.test.boundexpressions import BoundExpressions


def _assert_matches_eseries_candidate(
    solver: Solver, param: F.Parameters.NumericParameter
) -> None:
    lit = (
        solver.extract_superset(param.is_parameter.get())
        .switch_cast()
        .cast(F.Literals.Numbers)
    )
    is_eseries = param.get_trait(F.is_eseries_value)
    endpoints = (lit.get_min_value(), lit.get_max_value())
    candidates = is_eseries.get_candidates_in_range(endpoints[0], endpoints[1])
    assert any(
        math.isclose(lo, endpoints[0], rel_tol=1e-9, abs_tol=1e-18)
        and math.isclose(hi, endpoints[1], rel_tol=1e-9, abs_tol=1e-18)
        for _, lo, hi in candidates
    ), f"{endpoints} is not an E-series candidate band"


def test_vdiv_problem():
    g = graph.GraphView.create()
    tg = fbrk.TypeGraph.create(g=g)

    class App(fabll.Node):
        vdiv = F.ResistorVoltageDivider.MakeChild()

    app = App.bind_typegraph(tg=tg).create_instance(g=g)
    vdiv = app.vdiv.get()
    E = BoundExpressions(g=g, tg=tg)

    E.is_subset(
        vdiv.v_in.get().can_be_operand.get(),
        E.lit_op_range(((9.9, E.U.V), (10.1, E.U.V))),
        assert_=True,
    )
    E.is_subset(
        vdiv.v_out.get().can_be_operand.get(),
        E.lit_op_range(((3.0, E.U.V), (3.3, E.U.V))),
        assert_=True,
    )
    E.is_subset(
        vdiv.current.get().can_be_operand.get(),
        E.lit_op_range(((50e-6, E.U.A), (100e-6, E.U.A))),
        assert_=True,
    )

    r_top_param = (
        vdiv.chain.get().resistors[0].get().resistance.get().is_parameter.get()
    )
    r_bottom_param = (
        vdiv.chain.get().resistors[1].get().resistance.get().is_parameter.get()
    )

    solver = Solver()
    solver.simplify_for(r_top_param, r_bottom_param)

    _assert_matches_eseries_candidate(
        solver, vdiv.chain.get().resistors[0].get().resistance.get()
    )
    _assert_matches_eseries_candidate(
        solver, vdiv.chain.get().resistors[1].get().resistance.get()
    )

    # Verify achieved ratio (r_bottom / (r_top + r_bottom)) falls within v_out/v_in
    r_top = (
        fabll.Traits(
            solver.extract_superset(
                vdiv.chain.get().resistors[0].get().resistance.get().is_parameter.get()
            )
        )
        .get_obj_raw()
        .cast(F.Literals.Numbers)
    )
    r_bottom = (
        fabll.Traits(
            solver.extract_superset(
                vdiv.chain.get().resistors[1].get().resistance.get().is_parameter.get()
            )
        )
        .get_obj_raw()
        .cast(F.Literals.Numbers)
    )
    ratio_lo = r_bottom.get_min_value() / (
        r_top.get_max_value() + r_bottom.get_max_value()
    )
    ratio_hi = r_bottom.get_max_value() / (
        r_top.get_min_value() + r_bottom.get_min_value()
    )
    assert ratio_lo >= (3.0 / 10.1), f"ratio_lo {ratio_lo} < {3.0 / 10.1}"
    assert ratio_hi <= (3.3 / 9.9), f"ratio_hi {ratio_hi} > {3.3 / 9.9}"


def test_vdiv_feedback_divider():
    """
    Feedback divider for adjustable regulator (mirrors TPS63020 usage).

    Constraints:
      - ref_in (power_out): 3.25V to 3.35V
      - ref_out (reference): 0.495V to 0.505V (0.500V +/- 1%)
      - total_resistance: 200kΩ +/- 30% (140kΩ to 260kΩ)
      - current: 1µA to 100µA
    """
    g = graph.GraphView.create()
    tg = fbrk.TypeGraph.create(g=g)

    class App(fabll.Node):
        vdiv = F.ResistorVoltageDivider.MakeChild()

    app = App.bind_typegraph(tg=tg).create_instance(g=g)
    vdiv = app.vdiv.get()
    E = BoundExpressions(g=g, tg=tg)

    E.is_subset(
        vdiv.v_in.get().can_be_operand.get(),
        E.lit_op_range(((3.25, E.U.V), (3.35, E.U.V))),
        assert_=True,
    )
    E.is_subset(
        vdiv.v_out.get().can_be_operand.get(),
        E.lit_op_range(((0.495, E.U.V), (0.505, E.U.V))),
        assert_=True,
    )
    E.is_subset(
        vdiv.total_resistance.get().can_be_operand.get(),
        E.lit_op_range(((140e3, E.U.Ohm), (260e3, E.U.Ohm))),
        assert_=True,
    )
    E.is_subset(
        vdiv.current.get().can_be_operand.get(),
        E.lit_op_range(((1e-6, E.U.A), (100e-6, E.U.A))),
        assert_=True,
    )

    r_top_param = (
        vdiv.chain.get().resistors[0].get().resistance.get().is_parameter.get()
    )
    r_bottom_param = (
        vdiv.chain.get().resistors[1].get().resistance.get().is_parameter.get()
    )

    solver = Solver()
    solver.simplify_for(r_top_param, r_bottom_param)

    _assert_matches_eseries_candidate(
        solver, vdiv.chain.get().resistors[0].get().resistance.get()
    )
    _assert_matches_eseries_candidate(
        solver, vdiv.chain.get().resistors[1].get().resistance.get()
    )


def test_rc_filter_problem():
    g = graph.GraphView.create()
    tg = fbrk.TypeGraph.create(g=g)
    E = BoundExpressions(g=g, tg=tg)

    rc = F.FilterElectricalRC.bind_typegraph(tg=tg).create_instance(g=g)

    E.is_subset(
        rc.filter.get().cutoff_frequency.get().can_be_operand.get(),
        E.lit_op_range(((800, E.U.Hz), (1200, E.U.Hz))),
        assert_=True,
    )
    E.is_subset(
        rc.capacitor.get().capacitance.get().can_be_operand.get(),
        E.lit_op_range(((90e-9, E.U.Fa), (110e-9, E.U.Fa))),
        assert_=True,
    )

    solver = Solver()
    solver.simplify_for(
        rc.resistor.get().resistance.get().is_parameter.get(),
        rc.capacitor.get().capacitance.get().is_parameter.get(),
        rc.filter.get().cutoff_frequency.get().is_parameter.get(),
    )

    _assert_matches_eseries_candidate(solver, rc.resistor.get().resistance.get())
    _assert_matches_eseries_candidate(solver, rc.capacitor.get().capacitance.get())

    cutoff = (
        fabll.Traits(
            solver.extract_superset(
                rc.filter.get().cutoff_frequency.get().is_parameter.get()
            )
        )
        .get_obj_raw()
        .cast(F.Literals.Numbers)
    )
    assert cutoff.get_min_value() >= 800
    assert cutoff.get_max_value() <= 1200


# ---------------------------------------------------------------------------
# Unit tests: E-series candidate generation
# ---------------------------------------------------------------------------


class TestCandidateGeneration:
    def test_eseries_candidates_in_narrow_range(self):
        """E96 candidates in a narrow range around 10kΩ."""
        g = graph.GraphView.create()
        tg = fbrk.TypeGraph.create(g=g)

        class App(fabll.Node):
            r = F.Resistor.MakeChild()

        app = App.bind_typegraph(tg=tg).create_instance(g=g)
        trait = app.r.get().resistance.get().try_get_trait(F.is_eseries_value)
        assert trait is not None

        candidates = list(trait.get_candidates_in_range(9500.0, 10500.0))
        nominals = [c[0] for c in candidates]

        assert 10000.0 in nominals
        for nom, lo, hi in candidates:
            assert lo >= 9500.0
            assert hi <= 10500.0

    def test_eseries_candidates_respect_practical_bounds(self):
        """Candidates are clamped to practical min/max."""
        g = graph.GraphView.create()
        tg = fbrk.TypeGraph.create(g=g)

        class App(fabll.Node):
            r = F.Resistor.MakeChild()

        app = App.bind_typegraph(tg=tg).create_instance(g=g)
        trait = app.r.get().resistance.get().try_get_trait(F.is_eseries_value)

        candidates = list(trait.get_candidates_in_range(9e6, 20e6))
        for nom, _, _ in candidates:
            assert nom <= trait.practical_max

    def test_capacitor_e24_candidates(self):
        """E24 candidates for capacitors in the 100nF range."""
        g = graph.GraphView.create()
        tg = fbrk.TypeGraph.create(g=g)

        class App(fabll.Node):
            c = F.Capacitor.MakeChild()

        app = App.bind_typegraph(tg=tg).create_instance(g=g)
        trait = app.c.get().capacitance.get().try_get_trait(F.is_eseries_value)
        assert trait is not None

        candidates = list(trait.get_candidates_in_range(80e-9, 130e-9))
        nominals = [c[0] for c in candidates]
        assert any(abs(n - 100e-9) < 1e-12 for n in nominals)

    def test_no_candidates_in_empty_range(self):
        """No candidates when range is impossibly narrow."""
        g = graph.GraphView.create()
        tg = fbrk.TypeGraph.create(g=g)

        class App(fabll.Node):
            r = F.Resistor.MakeChild()

        app = App.bind_typegraph(tg=tg).create_instance(g=g)
        trait = app.r.get().resistance.get().try_get_trait(F.is_eseries_value)

        # E96 with 1% tolerance can't fit in a 0.1% window
        candidates = list(trait.get_candidates_in_range(10000.0, 10010.0))
        assert len(candidates) == 0


# ---------------------------------------------------------------------------
# Integration tests: adjustable regulator patterns
# ---------------------------------------------------------------------------


class TestAdjustableRegulatorPattern:
    """
    Test the discrete solver on designs that mirror a real AdjustableRegulator:
    feedback divider voltages are constrained through Is aliases and interface
    bus connections, not directly.
    """

    def test_regulator_with_output_voltage_constraint(self):
        """
        AdjustableRegulator pattern:
        - output_voltage Is feedback_divider.v_in
        - reference_voltage Is feedback_divider.v_out
        - power_out.voltage within some range (from regulator spec)
        - output_voltage Is power_out.voltage
        - reference_voltage within 1.185V +/- 2%
        """
        g = graph.GraphView.create()
        tg = fbrk.TypeGraph.create(g=g)

        class App(fabll.Node):
            _is_module = fabll.Traits.MakeEdge(fabll.is_module.MakeChild())
            vdiv = F.ResistorVoltageDivider.MakeChild()
            output_voltage = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Volt)
            reference_voltage = F.Parameters.NumericParameter.MakeChild(
                unit=F.Units.Volt
            )
            _link_v_in = F.Expressions.Is.MakeChild(
                [vdiv, F.ResistorVoltageDivider.v_in],
                [output_voltage],
                assert_=True,
            )
            _link_v_out = F.Expressions.Is.MakeChild(
                [vdiv, F.ResistorVoltageDivider.v_out],
                [reference_voltage],
                assert_=True,
            )

        app = App.bind_typegraph(tg=tg).create_instance(g=g)
        E = BoundExpressions(g=g, tg=tg)

        # Regulator spec: output 1.2V to 12V (wide)
        E.is_subset(
            app.output_voltage.get().can_be_operand.get(),
            E.lit_op_range(((1.2, E.U.V), (12.0, E.U.V))),
            assert_=True,
        )
        # Reference: 1.185V +/- 2%
        E.is_subset(
            app.reference_voltage.get().can_be_operand.get(),
            E.lit_op_range(((1.1613, E.U.V), (1.2087, E.U.V))),
            assert_=True,
        )
        # Current
        E.is_subset(
            app.vdiv.get().current.get().can_be_operand.get(),
            E.lit_op_range(((1e-6, E.U.A), (1e-3, E.U.A))),
            assert_=True,
        )

        F.is_alias_bus_parameter.resolve_bus_parameters(g=g, tg=tg)

        vdiv = app.vdiv.get()
        r_top_p = (
            vdiv.chain.get().resistors[0].get().resistance.get().is_parameter.get()
        )
        r_bottom_p = (
            vdiv.chain.get().resistors[1].get().resistance.get().is_parameter.get()
        )

        solver = Solver()
        solver.simplify_for(r_top_p, r_bottom_p)

        _assert_matches_eseries_candidate(
            solver, vdiv.chain.get().resistors[0].get().resistance.get()
        )
        _assert_matches_eseries_candidate(
            solver, vdiv.chain.get().resistors[1].get().resistance.get()
        )

    def test_regulator_with_usage_tightening(self):
        """
        Regulator + usage pattern: the output voltage is constrained both
        by the regulator spec (wide) and by a connected module (tight).

        Regulator: power_out.voltage within 1.2V to 12V
        Usage: connected_bus.voltage within 3.3V +/- 5%

        Both constrain the same parameter through Is aliases.
        The solver must find the intersection (tight bound).
        """
        g = graph.GraphView.create()
        tg = fbrk.TypeGraph.create(g=g)

        class App(fabll.Node):
            _is_module = fabll.Traits.MakeEdge(fabll.is_module.MakeChild())
            vdiv = F.ResistorVoltageDivider.MakeChild()
            output_voltage = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Volt)
            reference_voltage = F.Parameters.NumericParameter.MakeChild(
                unit=F.Units.Volt
            )
            bus_voltage = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Volt)
            _link_v_in = F.Expressions.Is.MakeChild(
                [vdiv, F.ResistorVoltageDivider.v_in],
                [output_voltage],
                assert_=True,
            )
            _link_v_out = F.Expressions.Is.MakeChild(
                [vdiv, F.ResistorVoltageDivider.v_out],
                [reference_voltage],
                assert_=True,
            )
            _link_bus = F.Expressions.Is.MakeChild(
                [output_voltage], [bus_voltage], assert_=True
            )

        app = App.bind_typegraph(tg=tg).create_instance(g=g)
        E = BoundExpressions(g=g, tg=tg)

        # Wide bound from regulator spec
        E.is_subset(
            app.output_voltage.get().can_be_operand.get(),
            E.lit_op_range(((1.2, E.U.V), (12.0, E.U.V))),
            assert_=True,
        )
        # Tight bound from connected MCU (3.3V +/- 5%)
        E.is_subset(
            app.bus_voltage.get().can_be_operand.get(),
            E.lit_op_range(((3.135, E.U.V), (3.465, E.U.V))),
            assert_=True,
        )
        # Reference: 1.185V +/- 2%
        E.is_subset(
            app.reference_voltage.get().can_be_operand.get(),
            E.lit_op_range(((1.1613, E.U.V), (1.2087, E.U.V))),
            assert_=True,
        )
        # Current
        E.is_subset(
            app.vdiv.get().current.get().can_be_operand.get(),
            E.lit_op_range(((1e-6, E.U.A), (1e-3, E.U.A))),
            assert_=True,
        )

        F.is_alias_bus_parameter.resolve_bus_parameters(g=g, tg=tg)

        vdiv = app.vdiv.get()
        r_top_p = (
            vdiv.chain.get().resistors[0].get().resistance.get().is_parameter.get()
        )
        r_bottom_p = (
            vdiv.chain.get().resistors[1].get().resistance.get().is_parameter.get()
        )

        solver = Solver()
        solver.simplify_for(r_top_p, r_bottom_p)

        _assert_matches_eseries_candidate(
            solver, vdiv.chain.get().resistors[0].get().resistance.get()
        )
        _assert_matches_eseries_candidate(
            solver, vdiv.chain.get().resistors[1].get().resistance.get()
        )

        # Verify the ratio matches the tight 3.3V output, not the wide spec
        r_top = solver.extract_superset(r_top_p).switch_cast().cast(F.Literals.Numbers)
        r_bottom = (
            solver.extract_superset(r_bottom_p).switch_cast().cast(F.Literals.Numbers)
        )
        rt_nom = (r_top.get_min_value() + r_top.get_max_value()) / 2
        rb_nom = (r_bottom.get_min_value() + r_bottom.get_max_value()) / 2
        ratio = rb_nom / (rt_nom + rb_nom)

        # ratio should be ~reference/output = 1.185/3.3 ≈ 0.359
        target_ratio = 1.185 / 3.3
        assert abs(ratio - target_ratio) < 0.05, (
            f"Ratio {ratio:.4f} too far from target {target_ratio:.4f}"
        )


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------


class _BenchApp(fabll.Node):
    vdiv = F.ResistorVoltageDivider.MakeChild()


def _make_vdiv_problem():
    """Set up a voltage divider problem and return (vdiv, target_params)."""
    g = graph.GraphView.create()
    tg = fbrk.TypeGraph.create(g=g)

    app = _BenchApp.bind_typegraph(tg=tg).create_instance(g=g)
    vdiv = app.vdiv.get()
    E = BoundExpressions(g=g, tg=tg)

    E.is_subset(
        vdiv.v_in.get().can_be_operand.get(),
        E.lit_op_range(((9.9, E.U.V), (10.1, E.U.V))),
        assert_=True,
    )
    E.is_subset(
        vdiv.v_out.get().can_be_operand.get(),
        E.lit_op_range(((3.0, E.U.V), (3.3, E.U.V))),
        assert_=True,
    )
    E.is_subset(
        vdiv.current.get().can_be_operand.get(),
        E.lit_op_range(((50e-6, E.U.A), (100e-6, E.U.A))),
        assert_=True,
    )

    target_params = [
        vdiv.chain.get().resistors[0].get().resistance.get().is_parameter.get(),
        vdiv.chain.get().resistors[1].get().resistance.get().is_parameter.get(),
    ]

    return vdiv, target_params


def test_vdiv_solve_benchmark():
    """Benchmark the voltage divider discrete solve."""
    # Warmup
    vdiv, target_params = _make_vdiv_problem()
    solver = Solver()
    solver.simplify_for(*target_params)

    N = 5
    times = []
    for _ in range(N):
        vdiv, target_params = _make_vdiv_problem()
        solver = Solver()
        t0 = time.perf_counter()
        solver.simplify_for(*target_params)
        elapsed = time.perf_counter() - t0
        times.append(elapsed)

    median = sorted(times)[N // 2]
    best = min(times)
    print(
        f"\n  vdiv solve: median={median * 1000:.1f}ms best={best * 1000:.1f}ms (n={N})"
    )
