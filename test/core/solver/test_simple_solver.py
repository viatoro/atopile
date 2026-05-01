# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

import math

import pytest

import faebryk.core.faebrykpy as fbrk
import faebryk.core.graph as graph
import faebryk.core.node as fabll
import faebryk.library._F as F
from faebryk.core.solver import Solver
from faebryk.core.solver.mutator import MutationMap
from faebryk.core.solver.simple_solver import (
    AliasMap,
    SafetyError,
    SimpleSolver,
    _Overlay,
)
from faebryk.core.solver.utils import FULL_SOLVER, Contradiction, ContradictionByLiteral
from faebryk.libs.test.boundexpressions import BoundExpressions
from faebryk.libs.util import not_none


def test_simple_solver_is_default():
    assert not FULL_SOLVER


def test_simple_solver_rejects_requested_target_defined_by_expression_alias():
    E = BoundExpressions()
    a = E.parameter_op(
        within=E.numbers().setup_from_min_max(min=1.0, max=3.0, unit=E.u.make_dl())
    )
    b = E.parameter_op()

    add_expr = E.add(a, E.lit_op_single(2.0))
    E.is_(b, add_expr, assert_=True)

    with pytest.raises(SafetyError, match="could not be verified"):
        Solver().simplify_for(b.get_sibling_trait(F.Parameters.is_parameter))


def test_simple_solver_rejects_wrong_side_of_expression_alias():
    E = BoundExpressions()
    a = E.parameter_op(
        within=E.numbers().setup_from_min_max(min=1.0, max=3.0, unit=E.u.make_dl())
    )
    b = E.parameter_op()
    c = E.parameter_op(
        within=E.numbers().setup_from_singleton(value=2.0, unit=E.u.make_dl())
    )

    E.is_(b, E.divide(a, c), assert_=True)

    with pytest.raises(SafetyError, match="could not be verified"):
        Solver().simplify_for(a.get_sibling_trait(F.Parameters.is_parameter))


def test_simple_solver_rejects_implies():
    E = BoundExpressions()
    a = E.bool_parameter_op()
    b = E.bool_parameter_op()

    E.implies(a, b, assert_=True)

    with pytest.raises(SafetyError, match="pattern the solver doesn't support"):
        Solver().simplify_for(a.get_sibling_trait(F.Parameters.is_parameter))


def test_simple_solver_rejects_nested_implies():
    E = BoundExpressions()
    a = E.bool_parameter_op()
    b = E.bool_parameter_op()
    c = E.bool_parameter_op()

    E.and_(E.implies(a, b), c, assert_=True)

    with pytest.raises(SafetyError, match="pattern the solver doesn't support"):
        Solver().simplify_for(c.get_sibling_trait(F.Parameters.is_parameter))


def test_simple_solver_allows_requested_target_defined_by_singleton_expression_alias():
    E = BoundExpressions()
    a = E.parameter_op(
        within=E.numbers().setup_from_singleton(value=2.0, unit=E.u.make_dl())
    )
    b = E.parameter_op(
        within=E.numbers().setup_from_singleton(value=3.0, unit=E.u.make_dl())
    )
    c = E.parameter_op()

    E.is_(c, E.add(a, b), assert_=True)

    result = Solver().simplify_and_extract_superset(
        c.get_sibling_trait(F.Parameters.is_parameter)
    )
    numbers = fabll.Traits(result).get_obj_raw().cast(F.Literals.Numbers)
    assert numbers.is_singleton()
    assert numbers.get_single() == 5.0


def test_simple_solver_intersects_numeric_inequalities():
    E = BoundExpressions()
    a = E.parameter_op()

    E.greater_or_equal(a, E.lit_op_single(2.0), assert_=True)
    E.less_or_equal(a, E.lit_op_single(10.0), assert_=True)

    solver = Solver()
    result = solver.simplify_and_extract_superset(
        a.get_sibling_trait(F.Parameters.is_parameter)
    )

    numbers = fabll.Traits(result).get_obj_raw().cast(F.Literals.Numbers)
    assert numbers.get_min_value() == 2.0
    assert numbers.get_max_value() == 10.0


def test_simple_solver_simplify_for_follows_transitive_parameter_chain():
    E = BoundExpressions()
    a = E.parameter_op()
    b = E.parameter_op()

    E.less_or_equal(a, b, assert_=True)
    E.less_or_equal(b, E.lit_op_single(10.0), assert_=True)

    solver = Solver()
    a_param = a.get_sibling_trait(F.Parameters.is_parameter)
    solver.simplify_for(a_param)
    result = solver.extract_superset(a_param)

    numbers = fabll.Traits(result).get_obj_raw().cast(F.Literals.Numbers)
    assert numbers.get_max_value() == 0.0


def test_simple_solver_simplify_for_uses_parameter_bound_over_alias_expression():
    E = BoundExpressions()
    a = E.parameter_op()
    b = E.parameter_op()
    c = E.parameter_op()

    E.is_(b, E.add(a, E.lit_op_single(1.0)), assert_=True)
    E.less_or_equal(b, E.lit_op_single(10.0), assert_=True)
    E.less_or_equal(c, b, assert_=True)

    solver = Solver()
    c_param = c.get_sibling_trait(F.Parameters.is_parameter)
    solver.simplify_for(c_param)
    result = solver.extract_superset(c_param)

    numbers = fabll.Traits(result).get_obj_raw().cast(F.Literals.Numbers)
    assert numbers.get_max_value() == 1.0


def test_simple_solver_supports_is_superset_without_canonicalization():
    E = BoundExpressions()
    a = E.parameter_op()
    b = E.parameter_op(
        within=E.numbers().setup_from_singleton(value=3.0, unit=E.u.make_dl())
    )

    E.is_superset(b, a, assert_=True)

    solver = Solver()
    result = solver.simplify_and_extract_superset(
        a.get_sibling_trait(F.Parameters.is_parameter)
    )

    numbers = fabll.Traits(result).get_obj_raw().cast(F.Literals.Numbers)
    assert numbers.get_min_value() == 3.0
    assert numbers.get_max_value() == 3.0


def test_simple_solver_is_superset_literal_contradiction():
    """IsSuperset(P, literal) where P's bound doesn't contain the literal."""
    E = BoundExpressions()
    a = E.parameter_op(
        within=E.numbers().setup_from_min_max(
            min=8000.0, max=12000.0, unit=E.u.make_dl()
        )
    )

    E.is_superset(a, E.lit_op_range((990.0, 1010.0)), assert_=True)

    with pytest.raises(Contradiction):
        SimpleSolver().simplify_for(a.get_sibling_trait(F.Parameters.is_parameter))


def test_simple_solver_is_superset_literal_compatible():
    """IsSuperset(P, literal) where P's bound does contain the literal."""
    E = BoundExpressions()
    a = E.parameter_op(
        within=E.numbers().setup_from_min_max(
            min=8000.0, max=12000.0, unit=E.u.make_dl()
        )
    )

    E.is_superset(a, E.lit_op_range((9900.0, 10100.0)), assert_=True)

    solver = SimpleSolver()
    a_param = a.get_sibling_trait(F.Parameters.is_parameter)
    solver.simplify_for(a_param)

    result = solver.extract_superset(a_param)
    numbers = fabll.Traits(result).get_obj_raw().cast(F.Literals.Numbers)
    assert numbers.get_min_value() == 8000.0
    assert numbers.get_max_value() == 12000.0


def test_simple_solver_rejects_parameter_cycles():
    E = BoundExpressions()
    a = E.parameter_op()
    b = E.parameter_op()
    c = E.parameter_op()

    E.less_or_equal(c, a, assert_=True)
    E.is_(a, E.add(b, E.lit_op_single(1.0)), assert_=True)
    E.is_(b, E.add(a, E.lit_op_single(1.0)), assert_=True)

    with pytest.raises(
        SafetyError,
        match="Circular dependency",
    ):
        Solver().simplify_for(c.get_sibling_trait(F.Parameters.is_parameter))


def test_simple_solver_intersects_multiple_resolvable_root_constraints():
    E = BoundExpressions()
    a = E.parameter_op(
        within=E.numbers().setup_from_min_max(min=1.0, max=3.0, unit=E.u.make_dl())
    )
    b = E.parameter_op(
        within=E.numbers().setup_from_singleton(value=3.0, unit=E.u.make_dl())
    )
    c = E.parameter_op()

    E.less_or_equal(c, a, assert_=True)
    E.less_or_equal(c, b, assert_=True)

    solver = Solver()
    result = solver.simplify_and_extract_superset(
        c.get_sibling_trait(F.Parameters.is_parameter)
    )

    numbers = fabll.Traits(result).get_obj_raw().cast(F.Literals.Numbers)
    assert numbers.get_min_value() == 0.0
    assert numbers.get_max_value() == 1.0


def test_simple_solver_overlay_compile_tracks_direct_target_predicates():
    """Verify that supported predicate shapes index the expected direct targets."""
    g = graph.GraphView.create()
    tg = fbrk.TypeGraph.create(g=g)

    class _App(fabll.Node):
        a = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Dimensionless)
        b = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Dimensionless)
        c = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Dimensionless)
        d = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Dimensionless)
        e = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Dimensionless)
        f = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Dimensionless)
        h = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Dimensionless)
        i = F.Parameters.NumericParameter.MakeChild(unit=F.Units.Dimensionless)

    app = _App.bind_typegraph(tg=tg).create_instance(g=g)

    # IsSubset: a ⊆ b → a depends on b
    F.Expressions.IsSubset.c(
        app.a.get().can_be_operand.get(),
        app.b.get().can_be_operand.get(),
        g=g,
        tg=tg,
        assert_=True,
    )
    # IsSuperset: c ⊇ d → c depends on d
    F.Expressions.IsSuperset.c(
        app.c.get().can_be_operand.get(),
        app.d.get().can_be_operand.get(),
        g=g,
        tg=tg,
        assert_=True,
    )
    # GreaterOrEqual: e >= f → e depends on f
    F.Expressions.GreaterOrEqual.c(
        app.e.get().can_be_operand.get(),
        app.f.get().can_be_operand.get(),
        g=g,
        tg=tg,
        assert_=True,
    )
    # LessOrEqual: h <= i → h depends on i
    F.Expressions.LessOrEqual.c(
        app.h.get().can_be_operand.get(),
        app.i.get().can_be_operand.get(),
        g=g,
        tg=tg,
        assert_=True,
    )

    expected_targets = (
        app.a.get().is_parameter.get(),
        app.d.get().is_parameter.get(),
        app.e.get().is_parameter.get(),
        app.f.get().is_parameter.get(),
        app.h.get().is_parameter.get(),
        app.i.get().is_parameter.get(),
    )
    all_ops = [p.as_operand.get() for p in expected_targets] + [
        app.b.get().can_be_operand.get(),
        app.c.get().can_be_operand.get(),
    ]

    mm = MutationMap._with_relevance_set(g=g, tg=tg, relevant=all_ops)

    AliasMap.build(mm.G_out, mm.tg_out)
    _Overlay(mm.G_out, mm.tg_out)

    for target in expected_targets:
        mapped = mm.map_forward(target.as_parameter_operatable.get())
        rep = AliasMap.rep(
            not_none(
                (mapped.maps_to or target.as_parameter_operatable.get())
                .as_operand.get()
                .try_get_sibling_trait(F.Parameters.is_parameter)
            )
        )
        assert _Overlay.get_targeting_preds(rep), (
            f"Expected targeting predicates for {target}"
        )

    mm.destroy()


def test_simple_solver_rejects_contradicting_resolvable_root_constraints():
    E = BoundExpressions()
    a = E.parameter_op(
        within=E.numbers().setup_from_min_max(min=1.0, max=3.0, unit=E.u.make_dl())
    )
    b = E.parameter_op(
        within=E.numbers().setup_from_singleton(value=5.0, unit=E.u.make_dl())
    )
    c = E.parameter_op()

    E.less_or_equal(c, a, assert_=True)
    E.greater_or_equal(c, b, assert_=True)

    solver = Solver()
    with pytest.raises(
        ContradictionByLiteral,
        match="empty superset",
    ):
        solver.simplify_for(c.get_sibling_trait(F.Parameters.is_parameter))


def test_simple_solver_safe_with_shared_exogenous_mediator():
    E = BoundExpressions()
    a = E.parameter_op()
    b = E.parameter_op()
    c = E.parameter_op()

    a_param = a.get_sibling_trait(F.Parameters.is_parameter)
    b_param = b.get_sibling_trait(F.Parameters.is_parameter)

    E.is_subset(a, c, assert_=True)
    E.is_subset(b, c, assert_=True)

    SimpleSolver().simplify_for(a_param, b_param)


def test_simple_solver_ge_rating_constraint_is_safe():
    E = BoundExpressions()
    a = E.parameter_op()
    b = E.parameter_op()

    a_param = a.get_sibling_trait(F.Parameters.is_parameter)

    E.greater_or_equal(a, b, assert_=True)

    SimpleSolver().simplify_for(a_param)


def test_simple_solver_handles_less_than():
    """LessThan is now supported via UPPER bound transform."""
    E = BoundExpressions()
    a = E.parameter_op()
    b = E.parameter_op()

    a_param = a.get_sibling_trait(F.Parameters.is_parameter)

    E.less_than(a, b, assert_=True)

    SimpleSolver().simplify_for(a_param)


def test_simple_solver_rejects_cycle():
    E = BoundExpressions()
    a = E.parameter_op()
    b = E.parameter_op()
    c = E.parameter_op()

    c_param = c.get_sibling_trait(F.Parameters.is_parameter)

    E.less_or_equal(c, a, assert_=True)
    E.is_(a, E.add(b, E.lit_op_single(1.0)), assert_=True)
    E.is_(b, E.add(a, E.lit_op_single(1.0)), assert_=True)

    with pytest.raises(SafetyError, match="Circular dependency"):
        SimpleSolver().simplify_for(c_param)


def test_simple_solver_rejects_shared_endogenous_node():
    """Two requested params both depend on a common non-literal mediator.

    a ⊆ c, b ⊆ c, c = d + 1. Requesting [a, b] means c is an interior
    formula-backed mediator shared by both trees.
    """
    E = BoundExpressions()
    a = E.parameter_op()
    b = E.parameter_op()
    c = E.parameter_op()
    d = E.parameter_op(
        within=E.numbers().setup_from_min_max(min=0.0, max=10.0, unit=E.u.make_dl())
    )

    a_param = a.get_sibling_trait(F.Parameters.is_parameter)
    b_param = b.get_sibling_trait(F.Parameters.is_parameter)

    E.is_subset(a, c, assert_=True)
    E.is_subset(b, c, assert_=True)
    E.is_(c, E.add(d, E.lit_op_single(1.0)), assert_=True)

    with pytest.raises(
        SafetyError, match="same intermediate expression|both as a target"
    ):
        SimpleSolver().simplify_for(a_param, b_param)


def test_simple_solver_rejects_requested_as_interior():
    """Param a depends on param b, both requested → direct coupling.

    a <= b. Requesting [a, b] means b appears as an interior dep of a's tree
    while also being a root.
    """
    E = BoundExpressions()
    a = E.parameter_op()
    b = E.parameter_op()

    a_param = a.get_sibling_trait(F.Parameters.is_parameter)
    b_param = b.get_sibling_trait(F.Parameters.is_parameter)

    E.less_or_equal(a, b, assert_=True)

    with pytest.raises(SafetyError, match="both as a target"):
        SimpleSolver().simplify_for(a_param, b_param)


def test_simple_solver_rejects_multiple_defining_predicates():
    """Two Is predicates from different expressions target the same rep.

    a = b + 1 and a = c + 2 → two Is-based defining predicates on a.
    """
    E = BoundExpressions()
    a = E.parameter_op()
    b = E.parameter_op()
    c = E.parameter_op()

    a_param = a.get_sibling_trait(F.Parameters.is_parameter)

    E.is_(a, E.add(b, E.lit_op_single(1.0)), assert_=True)
    E.is_(a, E.add(c, E.lit_op_single(2.0)), assert_=True)

    with pytest.raises(SafetyError, match="multiple aliases"):
        SimpleSolver().simplify_for(a_param)


def test_simple_solver_rejects_extra_incoming_edge():
    """One requested tree reaches the same endogenous dep through two parents."""
    E = BoundExpressions()
    a = E.parameter_op()
    b = E.parameter_op()
    c = E.parameter_op()
    m = E.parameter_op()
    n = E.parameter_op(
        within=E.numbers().setup_from_min_max(min=0.0, max=10.0, unit=E.u.make_dl())
    )

    a_param = a.get_sibling_trait(F.Parameters.is_parameter)

    E.is_subset(a, b, assert_=True)
    E.is_subset(a, c, assert_=True)
    E.is_subset(b, m, assert_=True)
    E.is_subset(c, m, assert_=True)
    E.is_(m, E.add(n, E.lit_op_single(1.0)), assert_=True)

    with pytest.raises(
        SafetyError,
        match="same intermediate expression|multiple conflicting expressions",
    ):
        SimpleSolver().simplify_for(a_param)


# ---------------------------------------------------------------------------
# Soundness: compiled predicates must hold post-solve
# ---------------------------------------------------------------------------


def test_simple_solver_inequality_backward_operand_solves_requested_target():
    """A >= B should narrow B from A when B is the requested target."""
    E = BoundExpressions()
    a = E.parameter_op(
        within=E.numbers().setup_from_min_max(min=3.0, max=5.0, unit=E.u.make_dl())
    )
    b = E.parameter_op()

    E.greater_or_equal(a, b, assert_=True)

    solver = Solver()
    b_param = b.get_sibling_trait(F.Parameters.is_parameter)
    solver.simplify_for(b_param)

    result = solver.extract_superset(b_param)
    numbers = fabll.Traits(result).get_obj_raw().cast(F.Literals.Numbers)
    assert numbers.get_min_value() == 0.0
    assert numbers.get_max_value() == 3.0


def test_simple_solver_le_backward_operand_solves_requested_target():
    """A <= B should narrow B from A when B is the requested target."""
    E = BoundExpressions()
    a = E.parameter_op(
        within=E.numbers().setup_from_min_max(min=3.0, max=5.0, unit=E.u.make_dl())
    )
    b = E.parameter_op()

    E.less_or_equal(a, b, assert_=True)

    solver = Solver()
    b_param = b.get_sibling_trait(F.Parameters.is_parameter)
    solver.simplify_for(b_param)

    result = solver.extract_superset(b_param)
    numbers = fabll.Traits(result).get_obj_raw().cast(F.Literals.Numbers)
    assert numbers.get_min_value() == 5.0
    assert numbers.get_max_value() == math.inf


def test_simple_solver_gt_backward_operand_solves_requested_target():
    """A > B should narrow B from A when B is the requested target."""
    E = BoundExpressions()
    a = E.parameter_op(
        within=E.numbers().setup_from_min_max(min=3.0, max=5.0, unit=E.u.make_dl())
    )
    b = E.parameter_op()

    E.greater_than(a, b, assert_=True)

    solver = Solver()
    b_param = b.get_sibling_trait(F.Parameters.is_parameter)
    solver.simplify_for(b_param)

    result = solver.extract_superset(b_param)
    numbers = fabll.Traits(result).get_obj_raw().cast(F.Literals.Numbers)
    assert numbers.get_min_value() == 0.0
    assert numbers.get_max_value() == 3.0


def test_simple_solver_lt_backward_operand_solves_requested_target():
    """A < B should narrow B from A when B is the requested target."""
    E = BoundExpressions()
    a = E.parameter_op(
        within=E.numbers().setup_from_min_max(min=3.0, max=5.0, unit=E.u.make_dl())
    )
    b = E.parameter_op()

    E.less_than(a, b, assert_=True)

    solver = Solver()
    b_param = b.get_sibling_trait(F.Parameters.is_parameter)
    solver.simplify_for(b_param)

    result = solver.extract_superset(b_param)
    numbers = fabll.Traits(result).get_obj_raw().cast(F.Literals.Numbers)
    assert numbers.get_min_value() == 5.0
    assert numbers.get_max_value() == math.inf


def test_simple_solver_rejects_shared_endogenous_comparison_mediator():
    """Two requested params sharing a comparison-tightened mediator are unsafe."""
    E = BoundExpressions()
    a = E.parameter_op()
    b = E.parameter_op()
    c = E.parameter_op()

    a_param = a.get_sibling_trait(F.Parameters.is_parameter)
    b_param = b.get_sibling_trait(F.Parameters.is_parameter)

    E.less_or_equal(a, c, assert_=True)
    E.less_or_equal(b, c, assert_=True)

    with pytest.raises(
        SafetyError, match="same intermediate expression|both as a target"
    ):
        SimpleSolver().simplify_for(a_param, b_param)


def test_simple_solver_rejects_shared_endogenous_expression_mediator_with_subtree():
    """An interior expression dep with its own subtree must be treated as endogenous."""
    E = BoundExpressions()
    a = E.parameter_op()
    b = E.parameter_op()
    c = E.parameter_op()
    d = E.parameter_op(
        within=E.numbers().setup_from_singleton(value=10.0, unit=E.u.make_dl())
    )

    a_param = a.get_sibling_trait(F.Parameters.is_parameter)
    b_param = b.get_sibling_trait(F.Parameters.is_parameter)

    E.less_or_equal(a, E.add(c, E.lit_op_single(1.0)), assert_=True)
    E.less_or_equal(b, E.add(c, E.lit_op_single(2.0)), assert_=True)
    E.less_or_equal(c, d, assert_=True)

    with pytest.raises(SafetyError, match="same intermediate expression"):
        SimpleSolver().simplify_for(a_param, b_param)


def test_simple_solver_inequality_detects_contradiction():
    """A >= B but A's range is entirely below B's range → contradiction.

    A ⊆ [1, 2], B ⊆ [10, 20].
    A >= B is impossible.
    """
    E = BoundExpressions()
    a = E.parameter_op(
        within=E.numbers().setup_from_min_max(min=1.0, max=2.0, unit=E.u.make_dl())
    )
    b = E.parameter_op(
        within=E.numbers().setup_from_min_max(min=10.0, max=20.0, unit=E.u.make_dl())
    )

    E.greater_or_equal(a, b, assert_=True)

    with pytest.raises((Contradiction, ContradictionByLiteral)):
        Solver().simplify_for(a.get_sibling_trait(F.Parameters.is_parameter))


def test_simple_solver_allows_target_expression_alias_if_it_becomes_singleton():
    """
    A root expression alias is one-shot-safe once the requested target is singleton.
    """
    E = BoundExpressions()
    a = E.parameter_op(
        within=E.numbers().setup_from_min_max(min=0.0, max=10.0, unit=E.u.make_dl())
    )
    b = E.parameter_op(
        within=E.numbers().setup_from_min_max(min=0.0, max=10.0, unit=E.u.make_dl())
    )
    c = E.parameter_op(
        within=E.numbers().setup_from_singleton(value=5.0, unit=E.u.make_dl())
    )

    E.is_(c, E.add(a, b), assert_=True)

    result = Solver().simplify_and_extract_superset(
        c.get_sibling_trait(F.Parameters.is_parameter)
    )
    numbers = fabll.Traits(result).get_obj_raw().cast(F.Literals.Numbers)
    assert numbers.is_singleton()
    assert numbers.get_single() == 5.0
