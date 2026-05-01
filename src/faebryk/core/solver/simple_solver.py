"""
Simple one-shot solver.

Execution model:
- `simplify_for(...)` inputs are the requested parameters; solving is performed on their
  alias representatives after alias closure.
- The expression dependency graph must form a forest rooted at the requested
  representatives, with no cycles and no shared endogenous interior nodes. Leaf nodes
  must be literals.
- Each dependency tree is evaluated bottom-up. Nodes are narrowed by evaluating their
  child-dependent rules and intersecting the results.
- A `DiscreteSystem` expression marker turns a small SCC into an atomic DFS supernode.
  When the DFS first reaches one of its unknown parameters, all non-SCC dependencies are
  solved through the same DFS, the SCC is sampled over its discrete candidate values,
  the unknowns are narrowed to the winning assignment, and ordinary bottom-up solving
  resumes.

Safety requirements:
- Every admitted graph must be fully evaluable by the preceding bottom-up model.
- A target may accumulate many monotone narrowing predicates whose literal results
  can be safely intersected, but may have at most one non-literal expression alias.
- Requested targets must be constrained only by downward-closed predicates. A
  non-literal Is(P, E) is admissible for a requested target only if the solved target
  resolves to a singleton literal.
- If a required predicate shape is unsupported, a touched predicate cannot be resolved
  or verified, or any narrowing step produces an empty set, the solver fails closed.

Post-solve guarantees:
- Every target parameter is resolved to a literal.
- Every predicate touching the solved closure is either:
  - handled by the ordinary bottom-up evaluation model
  - owned and verified by an discrete SCC solve
  - or causes the solve to fail closed
- Subsequent narrowing of a target parameter cannot influence other target parameters.
"""

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum, auto
from functools import reduce
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from faebryk.core.solver.mutator import Mutator

import faebryk.core.faebrykpy as fbrk
import faebryk.core.graph as graph
import faebryk.core.node as fabll
import faebryk.library._F as F
from atopile.logging import get_logger
from faebryk.core.solver.DiscreteSystem import DiscreteSystem
from faebryk.core.solver.mutator import MutationMap
from faebryk.core.solver.symbolic.pure_literal import exec_pure_literal_operands
from faebryk.core.solver.utils import (
    Contradiction,
    ContradictionByLiteral,
    MutatorUtils,
)
from faebryk.libs import interval_math
from faebryk.libs.util import EquivalenceClasses, md_list, not_none

IsSuperset = F.Expressions.IsSuperset
IsSubset = F.Expressions.IsSubset
Is = F.Expressions.Is
Implies = F.Expressions.Implies
GreaterThan = F.Expressions.GreaterThan
LessThan = F.Expressions.LessThan
GreaterOrEqual = F.Expressions.GreaterOrEqual
LessOrEqual = F.Expressions.LessOrEqual
is_parameter = F.Parameters.is_parameter
is_predicate = F.Expressions.is_predicate
can_be_operand = F.Parameters.can_be_operand
is_literal = F.Literals.is_literal

logger = get_logger(__name__)

# Maps expression types to interval_math functions for the SCC float evaluator.
# All return IntervalSet for uniform handling.
_SCC_INTERVAL_OPS: dict[
    type[fabll.Node],
    Callable[
        [interval_math.Interval, interval_math.Interval], interval_math.IntervalSet
    ],
] = {
    # TODO: ensure complete expression type coverage
    F.Expressions.Add: lambda a, b: (interval_math.interval_add(a, b),),
    F.Expressions.Subtract: lambda a, b: (interval_math.interval_subtract(a, b),),
    F.Expressions.Multiply: lambda a, b: (interval_math.interval_multiply(a, b),),
    F.Expressions.Divide: interval_math.interval_divide,
    F.Expressions.Power: interval_math.interval_pow,
}


class SolverValidationError(ValueError):
    pass


class SafetyError(SolverValidationError):
    class Reason(Enum):
        SHARED_ENDOGENOUS = auto()
        DIRECT_COUPLING = auto()
        EXTRA_INCOMING_EDGE = auto()
        MULTIPLE_DEFINING_PREDICATES = auto()
        CYCLE = auto()
        UNSUPPORTED_PREDICATE_SHAPE = auto()
        UNRESOLVABLE_PREDICATE = auto()

    _USER_REASONS: dict[Reason, str] = {
        Reason.SHARED_ENDOGENOUS: (
            "Multiple parameters depend on the same intermediate expression"
            " — the solver can't determine values independently"
        ),
        Reason.DIRECT_COUPLING: (
            "Parameter is used both as a target and as a dependency of another"
            " target — circular dependency"
        ),
        Reason.EXTRA_INCOMING_EDGE: (
            "A parameter is constrained by multiple conflicting expressions"
        ),
        Reason.MULTIPLE_DEFINING_PREDICATES: (
            "Parameter has multiple aliases — only one defining expression is allowed"
        ),
        Reason.CYCLE: ("Circular dependency between parameters"),
        Reason.UNSUPPORTED_PREDICATE_SHAPE: (
            "Constraint expression uses a pattern the solver doesn't support"
        ),
        Reason.UNRESOLVABLE_PREDICATE: (
            "Constraint could not be verified — the solver can't determine if it holds"
        ),
    }

    _HINTS: dict[Reason, str] = {
        Reason.SHARED_ENDOGENOUS: (
            "Try breaking the dependency by constraining intermediate"
            " parameters with explicit values"
        ),
        Reason.DIRECT_COUPLING: (
            "Try breaking the dependency by constraining intermediate"
            " parameters with explicit values"
        ),
        Reason.CYCLE: (
            "Try breaking the dependency by constraining intermediate"
            " parameters with explicit values"
        ),
        Reason.EXTRA_INCOMING_EDGE: ("Remove one of the conflicting constraints"),
        Reason.MULTIPLE_DEFINING_PREDICATES: ("Remove one of the conflicting aliases"),
        Reason.UNSUPPORTED_PREDICATE_SHAPE: (
            "Simplify the constraint expression or add explicit bounds"
            " to the parameters involved"
        ),
        Reason.UNRESOLVABLE_PREDICATE: (
            "Simplify the constraint expression or add explicit bounds"
            " to the parameters involved"
        ),
    }

    def __init__(
        self,
        reason: Reason,
        predicate: is_predicate | None = None,
        predicates: Iterable[is_predicate] = (),
        params: Iterable[is_parameter] = (),
        mut_map: "MutationMap | None" = None,
    ):
        self.reason = reason
        self.predicate = predicate
        self.predicates = list(predicates)
        if predicate and predicate not in self.predicates:
            self.predicates.insert(0, predicate)
        self.params = list(params)
        self.mut_map = mut_map
        super().__init__(self._format_message())

    def _map_to_original(self, param: is_parameter) -> list[is_parameter]:
        """Map a working-graph parameter back to its original-graph counterpart(s)."""
        if self.mut_map is None:
            return [param]
        originals = self.mut_map.map_backward(param.as_parameter_operatable.get())
        return [
            p
            for op in originals
            if (p := op.try_get_sibling_trait(is_parameter)) is not None
        ]

    def _format_message(self) -> str:
        user_reason = self._USER_REASONS.get(self.reason, self.reason.name)
        parts = [f"Unable to solve constraints: {user_reason}"]

        if self.params:
            original_params = [
                orig for p in self.params for orig in self._map_to_original(p)
            ]
            names = md_list(
                f"`{fabll.Traits(p).get_obj_raw().get_full_name()}`"
                for p in (original_params or self.params)
            )
            parts.append(f"Involves:\n{names}")

        if (hint := self._HINTS.get(self.reason)) is not None:
            parts.append(f"Hint: {hint}")

        return "\n\n".join(parts)


class _Transform(Enum):
    IDENTITY = auto()
    LOWER = auto()
    UPPER = auto()


@dataclass
class _MutatorShim:
    """Shim satisfying the mutator.mutation_map contract on Contradiction."""

    mutation_map: MutationMap


class _Contradictions:
    EMPTY_SUPERSET = "empty superset"
    FALSE_PREDICATE = "deduced predicate to false"


class is_alias_representative(fabll.Node):
    """Marks a parameter as the canonical representative of its alias class."""

    is_trait = fabll.Traits.MakeEdge(fabll.ImplementsTrait.MakeChild().put_on_type())


class AliasMap:
    """Alias equivalence classes, stored in the graph"""

    REP_ID = "_alias_rep"
    MEMBERS_ID = "_alias_members"

    @staticmethod
    def build(g: graph.GraphView, tg: fbrk.TypeGraph) -> None:
        # Build classes
        preds = is_predicate.bind_typegraph(tg).get_instances(g)
        exprs = [pred.as_expression.get() for pred in preds]
        classes = EquivalenceClasses(
            p for expr in exprs for p in MutatorUtils.get_params_for_expr(expr)
        )

        for expr in exprs:
            # Is(P, P) only - Is(P, E) handled elsewhere
            if expr.expr_isinstance(Is) and all(
                op.try_get_sibling_trait(is_parameter) is not None
                for op in expr.get_operands()
            ):
                classes.add_eq(*expr.get_operands_with_trait(is_parameter))

        # Assign representatives
        for members in classes.get():
            first = next(iter(members))
            rep_op = first.as_operand.get()

            rep_obj = fabll.Traits(rep_op).get_obj_raw()
            if not rep_obj.has_trait(is_alias_representative):
                fabll.Traits.create_and_add_instance_to(
                    node=rep_obj, trait=is_alias_representative
                )

            # rep -> class members
            member_set = F.Collections.PointerSet.bind_typegraph(  # type: ignore[arg-type]
                tg=rep_op.tg
            ).create_instance(g=rep_op.g)
            fbrk.EdgePointer.point_to(
                bound_node=rep_op.instance,
                target_node=member_set.instance.node(),
                identifier=AliasMap.MEMBERS_ID,
                index=None,
            )
            member_set.append(*members)

            for p in members:
                # param -> rep
                fbrk.EdgePointer.point_to(
                    bound_node=p.instance,
                    target_node=rep_op.instance.node(),
                    identifier=AliasMap.REP_ID,
                    index=None,
                )

    @staticmethod
    def rep(param: is_parameter) -> can_be_operand:
        return (
            fabll.Node.bind_instance(target).cast(can_be_operand)
            if (
                target := fbrk.EdgePointer.get_pointed_node_by_identifier(
                    bound_node=param.instance, identifier=AliasMap.REP_ID
                )
            )
            is not None
            else param.as_operand.get()
        )

    @staticmethod
    def members(rep: can_be_operand) -> list[is_parameter]:
        members = fbrk.EdgePointer.get_pointed_node_by_identifier(
            bound_node=rep.instance, identifier=AliasMap.MEMBERS_ID
        )

        return [
            n.cast(is_parameter)
            for n in F.Collections.PointerSet.bind_instance(members).as_list()  # type: ignore[arg-type]
        ]

    @staticmethod
    def all_reps(
        g: graph.GraphView, tg: fbrk.TypeGraph
    ) -> list[is_alias_representative]:
        return is_alias_representative.bind_typegraph(tg=tg).get_instances(g)


class _SupersetUtils:
    """
    Read/write API for parameter supersets.
    """

    def __init__(
        self, g: graph.GraphView, tg: fbrk.TypeGraph, mut_map: MutationMap
    ) -> None:
        self._g = g
        self._tg = tg
        self._mutator: "Mutator" = _MutatorShim(mut_map)  # type: ignore[assignment]

    @staticmethod
    def _intersect(a: is_literal | None, b: is_literal) -> is_literal | None:
        if b.op_setic_is_empty():
            return None
        if a is None:
            return b
        if a.op_setic_is_empty():
            return None
        if a.op_setic_is_subset_of(b):
            return a
        if b.op_setic_is_subset_of(a):
            return b
        result = a.op_setic_intersect(b)
        return None if result.op_setic_is_empty() else result

    @staticmethod
    def _apply_transform(
        g: graph.GraphView,
        tg: fbrk.TypeGraph,
        lit: is_literal,
        transform: _Transform,
    ) -> is_literal:
        """
        Convert a literal to a half-bounded range for picker-safe inequalities.

        LOWER: [lit_max, +inf)  (for >= constraints)
        UPPER: (-inf, lit_min]  (for <= constraints)
        """
        if (
            lit_node := fabll.Traits(lit).get_obj_raw().try_cast(F.Literals.Numbers)
        ) is None:
            raise SolverValidationError(
                f"Numeric inequality requires literal operand, got `{lit.pretty_str()}`"
            )

        match transform:
            case _Transform.LOWER:
                min = lit_node.get_max_value()
                max = math.inf
            case _Transform.UPPER:
                min = -math.inf
                max = lit_node.get_min_value()
            case _Transform.IDENTITY:
                assert False

        return (
            F.Literals.Numbers.bind_typegraph(tg=tg)
            .create_instance(g=g)
            .setup_from_min_max(min=min, max=max, unit=lit_node.get_is_unit())
            .is_literal.get()
        )

    def read(self, rep: can_be_operand) -> is_literal:
        """Tightest bound for rep across alias members."""
        current: is_literal | None = None
        for m in AliasMap.members(rep):
            bound = (
                m.as_parameter_operatable.get().try_extract_superset()
                or m.domain_set(g=self._g, tg=self._tg)
            )
            current = self._intersect(current, bound)
            if current is None:
                raise ContradictionByLiteral(
                    _Contradictions.EMPTY_SUPERSET,
                    involved=[rep.as_parameter_operatable.force_get()],
                    literals=[bound],
                    mutator=self._mutator,
                )
        return not_none(current)

    def narrow(
        self,
        rep: can_be_operand,
        bound: is_literal,
        transform: _Transform = _Transform.IDENTITY,
    ) -> None:
        """Narrow rep's superset by bound. No-op if subsumed. Raises on empty."""
        if transform != _Transform.IDENTITY:
            bound = self._apply_transform(self._g, self._tg, bound, transform)
        existing = self.read(rep)

        if (narrowed := self._intersect(existing, bound)) is None:
            raise ContradictionByLiteral(
                _Contradictions.EMPTY_SUPERSET,
                involved=[rep.as_parameter_operatable.force_get()],
                literals=[existing, bound],
                mutator=self._mutator,
            )

        if not existing.op_setic_equals(narrowed):
            rep.as_parameter_operatable.force_get().set_superset(
                g=self._g, value=narrowed.switch_cast()
            )

    def validate_not_empty(self, rep: can_be_operand) -> None:
        if (existing := self.read(rep)).op_setic_is_empty():
            raise ContradictionByLiteral(
                _Contradictions.EMPTY_SUPERSET,
                involved=[rep.as_parameter_operatable.force_get()],
                literals=[existing],
                mutator=self._mutator,
            )


class _Overlay:
    """
    Predicate index overlaid on the working graph.

    The overlay stores:
    - reachable predicates per alias rep
    - targeting predicates per alias rep
    - the deferred-predicate set

    Examples:
    - A <= B: reachable to A and B; targeting for A and B
    - A is B + C: reachable to A, B, and C; targeting for A only
    - A ⊆ (B + C): reachable to A, B, and C; targeting for A only
    """

    # TODO: persist rules in graph to avoid re-derivation
    class _Rule(NamedTuple):
        target_rep: can_be_operand
        operand: can_be_operand
        transform: _Transform
        deps: set[can_be_operand]

    # Rep -> predicate. Used for post-solve checks over predicates that mention at
    # least one parameter in the rep's alias class.
    REACHABLE_PRED_ID = "_reachable_pred"

    # Target rep -> predicate. Encodes whether a predicate contributes a compiled rule
    # for that rep (subset of reachable-predicate edges).
    TARGETING_PRED_ID = "_targeting_pred"

    def __init__(self, g: graph.GraphView, tg: fbrk.TypeGraph) -> None:
        # TODO: store in graph
        self.deferred_preds: set[is_predicate] = set()
        self._compile(is_predicate.bind_typegraph(tg).get_instances(g))

    @staticmethod
    def _collect_pointed_nodes[T: fabll.NodeT](
        rep: can_be_operand, identifier: str, node_type: type[T]
    ) -> list[T]:
        def collect(ctx: list[T], edge: graph.BoundEdge) -> None:
            ctx.append(
                fabll.Node.bind_instance(
                    edge.g().bind(
                        node=fbrk.EdgePointer.get_referenced_node(edge=edge.edge())
                    )
                ).cast(node_type)
            )

        nodes: list[T] = []

        fbrk.EdgePointer.visit_pointed_edges_with_identifier(
            bound_node=rep.instance, identifier=identifier, ctx=nodes, f=collect
        )

        return nodes

    @staticmethod
    def _add_reachable_predicate(pred: fabll.Node, rep: fabll.Node) -> None:
        fbrk.EdgePointer.point_to(
            bound_node=rep.instance,
            target_node=pred.instance.node(),
            identifier=_Overlay.REACHABLE_PRED_ID,
            index=None,
        )

    @staticmethod
    def get_reachable_predicates(rep: can_be_operand) -> list[is_predicate]:
        return _Overlay._collect_pointed_nodes(
            rep, identifier=_Overlay.REACHABLE_PRED_ID, node_type=is_predicate
        )

    @staticmethod
    def _add_targeting_predicate(pred: fabll.Node, target: fabll.Node) -> None:
        fbrk.EdgePointer.point_to(
            bound_node=target.instance,
            target_node=pred.instance.node(),
            identifier=_Overlay.TARGETING_PRED_ID,
            index=None,
        )

    @staticmethod
    def get_targeting_preds(rep: can_be_operand) -> list[is_predicate]:
        return _Overlay._collect_pointed_nodes(
            rep, identifier=_Overlay.TARGETING_PRED_ID, node_type=is_predicate
        )

    def get_pred_rules(self, pred: is_predicate) -> list[_Overlay._Rule]:
        """For each predicate, derive all potentially-constraining rules."""

        def gen_rule(
            target_rep: can_be_operand, dep_op: can_be_operand, transform: _Transform
        ) -> _Overlay._Rule:
            if (param := dep_op.try_get_sibling_trait(is_parameter)) is not None:
                dep_op = AliasMap.rep(param)

            return _Overlay._Rule(
                target_rep,
                dep_op,
                transform,
                {
                    AliasMap.rep(param)
                    for po in MutatorUtils.find_unique_params(dep_op)
                    if (param := po.as_parameter.try_get()) is not None
                },
            )

        expr = pred.as_expression.get()
        ops = expr.get_operands()

        if expr.expr_isinstance(Is):
            # Symmetric: every operand constrains every other
            # Is(P, E) only - Is(P, P) handled by alias closure
            target_reps = {
                AliasMap.rep(param)
                for op in ops
                if (param := op.try_get_sibling_trait(is_parameter))
            }
            return [
                gen_rule(target_rep, dep_op, _Transform.IDENTITY)
                for target_rep in target_reps
                for dep_op in ops
                if not (
                    (param := dep_op.try_get_sibling_trait(is_parameter)) is not None
                    and AliasMap.rep(param).is_same(target_rep)
                )
            ]
        elif expr.expr_isinstance(IsSubset):
            # Forward: operand 0 is constrained by operand 1
            target_op, dep_op = ops
            if target := target_op.try_get_sibling_trait(is_parameter):
                return [gen_rule(AliasMap.rep(target), dep_op, _Transform.IDENTITY)]
        elif expr.expr_isinstance(IsSuperset):
            # Backward: operand 1 is constrained by operand 0
            dep_op, target_op = ops
            if target := target_op.try_get_sibling_trait(is_parameter):
                return [gen_rule(AliasMap.rep(target), dep_op, _Transform.IDENTITY)]
        elif expr.expr_isinstance(GreaterOrEqual, GreaterThan, LessOrEqual, LessThan):
            # Bidirectional: each operand constrains the other
            lhs_transform, rhs_transform = (
                (_Transform.LOWER, _Transform.UPPER)
                if expr.expr_isinstance(GreaterOrEqual, GreaterThan)
                else (_Transform.UPPER, _Transform.LOWER)
            )
            lhs_op, rhs_op = ops
            return [
                gen_rule(AliasMap.rep(target), dep_op, transform)
                for target_op, dep_op, transform in (
                    (lhs_op, rhs_op, lhs_transform),
                    (rhs_op, lhs_op, rhs_transform),
                )
                if (target := target_op.try_get_sibling_trait(is_parameter))
            ]

        return []

    def _compile(self, preds: list[is_predicate]) -> None:
        for pred in preds:
            expr = pred.as_expression.get()

            if expr.is_non_constraining():
                continue

            # Discrete SCC markers are handled by _solve_marked_problem
            if expr.expr_isinstance(DiscreteSystem):
                continue

            # Is(param, param) handled elsewhere
            if expr.expr_isinstance(Is) and all(
                op.try_get_sibling_trait(is_parameter) is not None
                for op in expr.get_operands()
            ):
                continue

            for rep in {
                AliasMap.rep(p) for p in MutatorUtils.get_params_for_expr(expr)
            }:
                self._add_reachable_predicate(pred, rep)

            if not (rules := self.get_pred_rules(pred)):
                self.deferred_preds.add(pred)
                continue

            for target_rep in {rule.target_rep for rule in rules}:
                self._add_targeting_predicate(pred, target_rep)


class _Resolver:
    """
    Evaluate requested reps from the predicate-indexed overlay.

    Post-order DFS of the dependency graph from each target parameter.
    - detects inadmissible expression structure
    - recursively resolves parameters by narrowing according to intersection of
      per-predicate rule results applied to evaluated subtree

    After resolving the target closure, checks reachable predicates:
    - deferred predicates must fold to true
    - predicates reachable from an ordinary target parameter must have contributed to
      narrowing that parameter
    - predicates owned by a DiscreteSystem SCC are verified inside that SCC solve
    """

    def __init__(self, mut_map: MutationMap, target_reps: set[can_be_operand]) -> None:
        self._g = mut_map.G_out
        self._tg = mut_map.tg_out
        self._mut_map = mut_map
        self._target_reps = target_reps

        self._superset_utils = _SupersetUtils(g=self._g, tg=self._tg, mut_map=mut_map)
        self._overlay = _Overlay(self._g, self._tg)

        self._visiting: set[can_be_operand] = set()
        self._solved: set[can_be_operand] = set()
        self._owner: dict[can_be_operand, can_be_operand] = {}
        self._parent: dict[can_be_operand, can_be_operand] = {}

        self._validate_input_types()

    def _find_marked_problem_for_unknown(
        self, rep: can_be_operand
    ) -> DiscreteSystem | None:
        """Return the DiscreteSystem marker that owns `rep`, if any."""
        marker: DiscreteSystem | None = None
        for member in AliasMap.members(rep):
            for candidate in member.as_operand.get().get_operations(
                DiscreteSystem, predicates_only=True
            ):
                if marker is None:
                    marker = candidate
                    continue

                if not marker.is_same(candidate):
                    raise SafetyError(
                        reason=SafetyError.Reason.UNSUPPORTED_PREDICATE_SHAPE,
                        predicates=[
                            marker.get_trait(is_predicate),
                            candidate.get_trait(is_predicate),
                        ],
                        params=[
                            *marker.get_unknown_params(),
                            *candidate.get_unknown_params(),
                        ],
                        mut_map=self._mut_map,
                    )

        return marker

    def _validate_input_types(self) -> None:
        if implies_exprs := Implies.bind_typegraph(self._tg).get_instances(self._g):
            raise SafetyError(
                reason=SafetyError.Reason.UNSUPPORTED_PREDICATE_SHAPE,
                params={
                    param
                    for expr in implies_exprs
                    for param in MutatorUtils.get_params_for_expr(
                        expr.is_expression.get()
                    )
                },
                mut_map=self._mut_map,
            )

    def _get_target_rules(
        self, rep: can_be_operand, exclude_pred: is_predicate | None = None
    ) -> list[tuple[is_predicate, _Overlay._Rule]]:
        """Return directional rules for `rep`, derived on demand from predicates."""
        return [
            (pred, rule)
            for pred in _Overlay.get_targeting_preds(rep)
            if exclude_pred is None or not pred.is_same(exclude_pred)
            for rule in self._overlay.get_pred_rules(pred)
            if rule.target_rep.is_same(rep)
        ]

    def _rep_has_derived_target_rule(
        self, rep: can_be_operand, exclude_pred: is_predicate | None = None
    ) -> bool:
        # TODO: should be a has_trait check
        return any(
            rule.deps
            for pred in _Overlay.get_targeting_preds(rep)
            if exclude_pred is None or not pred.is_same(exclude_pred)
            for rule in self._overlay.get_pred_rules(pred)
            if rule.target_rep.is_same(rep)
        )

    def _dep_is_endogenous(self, pred: is_predicate, dep: can_be_operand) -> bool:
        """
        Classify whether a traversed dependency should count as an interior node.

        Safety heuristic for ownership and parent checks:
        - dependencies reached through aliases (Is(P, E)) count as endogenous
        - otherwise, a dependency counts as endogenous if it is itself directly
          targeted by some derived rule

        TODO: avoid over-rejection with more sophisticated check
        """
        return pred.as_expression.get().expr_isinstance(Is) or (
            self._rep_has_derived_target_rule(dep)
        )

    @staticmethod
    def _get_rep_targeting_preds(
        reps: Iterable[can_be_operand],
    ) -> list[is_predicate]:
        return [pred for rep in reps for pred in _Overlay.get_targeting_preds(rep)]

    def simplify(self) -> None:
        """Resolve target parameters, then post-check reachable predicates."""
        # TODO: replace with zig-side post-order traversal
        self._visiting = set()
        self._solved = set()
        self._owner = {}
        self._parent = {}

        for rep in self._target_reps:
            self._solve(rep, root=rep)

        self._check_reachable_predicates(self._solved)

    def _solve(
        self,
        rep: can_be_operand,
        root: can_be_operand,
        parent_rep: can_be_operand | None = None,
        exclude_pred: is_predicate | None = None,
    ) -> None:
        """
        Post-order solve for a single parameter rep, with graph-structure safety checks.
        """
        endogenous = (
            parent_rep is not None
            and exclude_pred is not None
            and self._dep_is_endogenous(exclude_pred, rep)
        )

        # Check for cycles
        if rep in self._visiting:
            raise SafetyError(
                reason=SafetyError.Reason.CYCLE,
                predicates=self._get_rep_targeting_preds(self._visiting),
                params=(p for r in self._visiting for p in AliasMap.members(r)),
                mut_map=self._mut_map,
            )

        # Requested param appearing as interior node (direct coupling)
        if rep in self._target_reps and not rep.is_same(root):
            raise SafetyError(
                reason=SafetyError.Reason.DIRECT_COUPLING,
                predicates=self._get_rep_targeting_preds((rep, root)),
                params=(p for r in (rep, root) for p in AliasMap.members(r)),
                mut_map=self._mut_map,
            )

        # Shared endogenous: already owned by a different root
        if endogenous and rep in self._owner and not self._owner[rep].is_same(root):
            raise SafetyError(
                reason=SafetyError.Reason.SHARED_ENDOGENOUS,
                predicates=self._get_rep_targeting_preds((rep, root, self._owner[rep])),
                params=(
                    p
                    for r in (rep, root, self._owner[rep])
                    for p in AliasMap.members(r)
                ),
                mut_map=self._mut_map,
            )

        # Extra incoming edge: different parent
        if (
            endogenous
            and parent_rep is not None
            and rep in self._parent
            and not self._parent[rep].is_same(parent_rep)
        ):
            raise SafetyError(
                reason=SafetyError.Reason.EXTRA_INCOMING_EDGE,
                predicates=self._get_rep_targeting_preds((rep,)),
                params=AliasMap.members(rep),
                mut_map=self._mut_map,
            )

        # Already solved — nothing further to do
        if rep in self._solved:
            return

        # SCC unknowns are handled atomically by the discrete system solver.
        if (marker := self._find_marked_problem_for_unknown(rep)) is not None:
            _DiscreteSystemSolver(self).solve(marker, root)
            return

        if endogenous or rep in self._target_reps:
            # Track ownership to ensure no parameter sharing between trees
            self._owner[rep] = root

            # Track parentage so endogenous nodes form a forest, not a DAG.
            if parent_rep is not None:
                self._parent[rep] = parent_rep

        self._visiting.add(rep)

        try:
            # Multiple defining predicates: two different non-literal Is predicates
            # target the same parameter (i.e. Is(P, E1) and Is(P, E2)).
            # Treat as ambiguous full definitions and reject instead of trying to
            # reconcile over multiple passes.
            _rep_preds = [
                pred
                for pred in _Overlay.get_targeting_preds(rep)
                if pred.as_expression.get().expr_isinstance(Is)
            ]

            if len(set(_rep_preds)) > 1:
                raise SafetyError(
                    reason=SafetyError.Reason.MULTIPLE_DEFINING_PREDICATES,
                    predicates=_rep_preds,
                    params=AliasMap.members(rep),
                    mut_map=self._mut_map,
                )

            rules = self._get_target_rules(rep, exclude_pred=exclude_pred)

            for pred, rule in rules:
                for dep in rule.deps:
                    self._solve(dep, root=root, parent_rep=rep, exclude_pred=pred)

            self._resolve_rep(rep, exclude_pred=exclude_pred)

            # Target params may keep one non-literal alias iff the target still
            # collapses to a singleton literal
            _target_aliases = (
                [
                    pred
                    for pred, rule in rules
                    if pred.as_expression.get().expr_isinstance(Is)
                    and rule.operand.as_literal.try_get() is None
                ]
                if rep in self._target_reps
                else []
            )

            if (
                _target_aliases
                and not self._superset_utils.read(rep).op_setic_is_singleton()
            ):
                raise SafetyError(
                    reason=SafetyError.Reason.UNRESOLVABLE_PREDICATE,
                    predicates=_target_aliases,
                    params=AliasMap.members(rep),
                    mut_map=self._mut_map,
                )

            self._solved.add(rep)

        finally:
            self._visiting.remove(rep)

    def _resolve_rep(
        self, rep: can_be_operand, exclude_pred: is_predicate | None = None
    ) -> None:
        """Evaluate all rules targeting rep and narrow its superset."""
        rules = self._get_target_rules(rep, exclude_pred=exclude_pred)
        for _, rule in rules:
            self._superset_utils.narrow(rep, self._eval(rule.operand), rule.transform)
        if not rules:
            self._superset_utils.validate_not_empty(rep)

    def _check_reachable_predicates(self, solved: set[can_be_operand]) -> None:
        """Evaluate predicates reachable from the solved closure."""
        deferred_preds = self._overlay.deferred_preds

        preds = {
            (pred, rep)
            for rep in solved
            if self._find_marked_problem_for_unknown(rep) is None
            for pred in _Overlay.get_reachable_predicates(rep)
        }

        for pred in {p for p, _ in preds} & deferred_preds:
            self._check_deferred_preds(pred)

        requested = {
            (pred, rep)
            for pred, rep in preds
            if pred not in deferred_preds and rep in self._target_reps
        }

        targeting_preds = {
            (pred, rep)
            for pred, rep in requested
            if pred in set(_Overlay.get_targeting_preds(rep))
        }

        for pred, _rep in requested - targeting_preds:
            raise SafetyError(
                reason=SafetyError.Reason.UNRESOLVABLE_PREDICATE,
                predicate=pred,
                params=MutatorUtils.get_params_for_expr(pred.as_expression.get()),
                mut_map=self._mut_map,
            )

    def _check_deferred_preds(self, pred: is_predicate) -> None:
        expr = pred.as_expression.get()

        try:
            value = self._eval(expr.as_operand.get())
        except SolverValidationError:
            raise SafetyError(
                reason=SafetyError.Reason.UNSUPPORTED_PREDICATE_SHAPE,
                predicate=pred,
                params=MutatorUtils.get_params_for_expr(expr),
                mut_map=self._mut_map,
            )

        if value.op_setic_equals_singleton(True):
            # assertion holds
            return

        if value.op_setic_equals_singleton(False):
            raise Contradiction(
                _Contradictions.FALSE_PREDICATE,
                involved=[
                    p.as_parameter_operatable.get()
                    for p in MutatorUtils.get_params_for_expr(expr)
                ],
                mutator=self._superset_utils._mutator,
            )

        raise SafetyError(
            reason=SafetyError.Reason.UNRESOLVABLE_PREDICATE,
            predicate=pred,
            params=MutatorUtils.get_params_for_expr(expr),
            mut_map=self._mut_map,
        )

    def _eval(self, operand: can_be_operand) -> is_literal:
        """
        Evaluate an operand to a concrete literal.

        - pass through literals directly
        - return current bound for parameters
        - recursively fold expressions
        """
        if lit := operand.as_literal.try_get():
            return lit

        if param := operand.try_get_sibling_trait(is_parameter):
            return self._superset_utils.read(AliasMap.rep(param))

        expr = not_none(operand.try_get_sibling_trait(F.Expressions.is_expression))

        if (
            result := exec_pure_literal_operands(
                self._g,
                self._tg,
                MutatorUtils.hack_get_expr_type(expr),
                [self._eval(c).as_operand.get() for c in expr.get_operands()],
            )
        ) is None:
            raise SolverValidationError(
                "Simple solver admission bug: failed to fold admitted expression "
                + expr.compact_repr(use_full_name=True)
            )

        return result


class _DiscreteSystemSolver:
    """
    Solves a DiscreteSystem SCC by brute-force search over E-series candidates.

    Uses compiled float-level interval arithmetic (interval_math) to avoid graph
    allocation in the inner loop.
    """

    def __init__(self, resolver: _Resolver) -> None:
        self._resolver = resolver

    def solve(self, marker: DiscreteSystem, root: can_be_operand) -> None:
        """Solve one DiscreteSystem SCC as an atomic DFS node."""
        assert marker.objective_mode == DiscreteSystem.ObjectiveMode.target_center
        # --- Collect closure ---
        unknown_reps = set(AliasMap.rep(param) for param in marker.get_unknown_params())
        objective_rep = AliasMap.rep(marker.get_objective_param())

        if all(rep in self._resolver._solved for rep in unknown_reps):
            return

        if any(rep in self._resolver._visiting for rep in unknown_reps):
            raise SafetyError(
                reason=SafetyError.Reason.CYCLE,
                predicates=self._resolver._get_rep_targeting_preds(
                    self._resolver._visiting | unknown_reps
                ),
                params=(
                    p
                    for r in self._resolver._visiting | unknown_reps
                    for p in AliasMap.members(r)
                ),
                mut_map=self._resolver._mut_map,
            )

        reachable_preds = [
            pred
            for pred in MutatorUtils.get_relevant_predicates(*unknown_reps)
            if not pred.as_expression.get().expr_isinstance(DiscreteSystem)
        ]

        relevant_reps = {
            AliasMap.rep(param)
            for pred in reachable_preds
            for param in MutatorUtils.get_params_for_expr(pred.as_expression.get())
        }

        # --- Validate marker shape ---
        if len(unknown_reps) != 2 or objective_rep in unknown_reps:
            raise SafetyError(
                reason=SafetyError.Reason.UNSUPPORTED_PREDICATE_SHAPE,
                predicates=reachable_preds,
                params=[p for rep in unknown_reps for p in AliasMap.members(rep)],
                mut_map=self._resolver._mut_map,
            )

        # Note: some reachable predicates may be "deferred" by the overlay
        # (e.g. IsSubset(expr, param) where the subset is an expression).
        # The SCC solver verifies these during the candidate search.

        # --- Solve non-SCC deps, prepare, search, apply ---
        self._resolver._visiting.update(unknown_reps)
        try:
            scc_reps = {
                rep
                for rep in relevant_reps
                if rep in unknown_reps
                or any(
                    self._operand_touches_unknowns(rule.operand, unknown_reps, {rep})
                    for _pred, rule in self._resolver._get_target_rules(rep)
                )
            }

            for rep in scc_reps:
                for pred, rule in self._resolver._get_target_rules(rep):
                    for dep in rule.deps - scc_reps:
                        self._resolver._solve(dep, root=root, exclude_pred=pred)

            # Split each rep into (concrete_bound, optional symbolic operand)
            prepared = {
                rep: self._split_rep_for_scc(rep, unknown_reps) for rep in relevant_reps
            }

            # Unknowns must have only concrete bounds (no symbolic definition),
            # and the objective must have a symbolic residual linking it to the unknowns
            for rep in unknown_reps:
                _bound, symbolic = prepared[rep]
                assert symbolic is None

            obj_bound, obj_symbolic = prepared[objective_rep]
            assert obj_symbolic is not None

            candidates = self._build_scc_candidates(unknown_reps, prepared)

            # Compile constraint checks and objective into float-level evaluators
            unknown_slots = {rep: i for i, rep in enumerate(unknown_reps)}

            def _finite_bound(lit: is_literal) -> interval_math.Interval | None:
                nums = lit.switch_cast().cast(F.Literals.Numbers)
                if not nums.is_finite():
                    return None
                return (nums.get_min_value(), nums.get_max_value())

            checks = [
                (
                    bound_iv,
                    self._compile_scc_expression(residual, unknown_slots, prepared),
                )
                for rep, (bound, residual) in prepared.items()
                if rep not in unknown_reps
                and residual is not None
                and (bound_iv := _finite_bound(bound)) is not None
            ]

            obj_lo, obj_hi = not_none(_finite_bound(obj_bound))
            objective_target = (obj_lo + obj_hi) * 0.5
            objective_fn = self._compile_scc_expression(
                obj_symbolic, unknown_slots, prepared
            )

            best = self._search_scc_candidates(
                candidates=candidates,
                checks=checks,
                objective_fn=objective_fn,
                objective_target=objective_target,
            )

            if best is None:
                raise Contradiction(
                    "No candidate assignment satisfies the discrete problem system",
                    involved=[
                        rep.as_parameter_operatable.force_get() for rep in unknown_reps
                    ],
                    mutator=self._resolver._superset_utils._mutator,
                )

            for rep, (c_lo, c_hi) in zip(unknown_reps, best, strict=True):
                bound, _symbolic = prepared[rep]
                winner = (
                    F.Literals.Numbers.bind_typegraph(self._resolver._tg)
                    .create_instance(g=self._resolver._g)
                    .setup_from_min_max(
                        min=c_lo,
                        max=c_hi,
                        unit=bound.switch_cast().cast(F.Literals.Numbers).get_is_unit(),
                    )
                    .is_literal.get()
                )
                self._resolver._superset_utils.narrow(rep, winner)
            self._resolver._solved.update(unknown_reps)
        finally:
            self._resolver._visiting.difference_update(unknown_reps)

    def _split_rep_for_scc(
        self, rep: can_be_operand, unknown_reps: set[can_be_operand]
    ) -> tuple[is_literal, can_be_operand | None]:
        """
        Split a rep's incoming rules into a concrete bound and an optional symbolic
        operand (the single alias operand that depends on SCC unknowns).
        """
        bound = self._resolver._superset_utils.read(rep)
        symbolic: can_be_operand | None = None

        for pred, rule in self._resolver._get_target_rules(rep):
            if not self._operand_touches_unknowns(rule.operand, unknown_reps, {rep}):
                value = self._resolver._eval(rule.operand)
                if rule.transform != _Transform.IDENTITY:
                    value = _SupersetUtils._apply_transform(
                        self._resolver._g, self._resolver._tg, value, rule.transform
                    )
                if (merged := _SupersetUtils._intersect(bound, value)) is None:
                    raise Contradiction(
                        "Empty intersection in SCC rep preparation",
                        involved=[rep.as_parameter_operatable.force_get()],
                        mutator=self._resolver._superset_utils._mutator,
                    )
                bound = merged
                continue

            # Symbolic operands (depending on unknowns) must come from untransformed
            # aliases — other shapes can't be inverted
            if (
                not pred.as_expression.get().expr_isinstance(Is)
                or rule.transform != _Transform.IDENTITY
            ):
                raise SafetyError(
                    reason=SafetyError.Reason.UNSUPPORTED_PREDICATE_SHAPE,
                    predicate=pred,
                    params=MutatorUtils.get_params_for_expr(pred.as_expression.get()),
                    mut_map=self._resolver._mut_map,
                )

            if symbolic is not None and not symbolic.is_same(rule.operand):
                raise SafetyError(
                    reason=SafetyError.Reason.MULTIPLE_DEFINING_PREDICATES,
                    predicates=self._resolver._get_rep_targeting_preds((rep,)),
                    params=AliasMap.members(rep),
                    mut_map=self._resolver._mut_map,
                )

            symbolic = rule.operand

        return bound, symbolic

    def _operand_touches_unknowns(
        self,
        operand: can_be_operand,
        unknown_reps: set[can_be_operand],
        visiting: set[can_be_operand],
    ) -> bool:
        """Check whether evaluating operand requires any rep in unknown_reps."""
        if operand.as_literal.try_get() is not None:
            return False

        if param := operand.try_get_sibling_trait(is_parameter):
            if (rep := AliasMap.rep(param)) in unknown_reps:
                return True

            if rep in visiting:
                raise SafetyError(
                    reason=SafetyError.Reason.CYCLE,
                    predicates=self._resolver._get_rep_targeting_preds(
                        visiting | {rep}
                    ),
                    params=(p for r in visiting | {rep} for p in AliasMap.members(r)),
                    mut_map=self._resolver._mut_map,
                )

            return any(
                self._operand_touches_unknowns(
                    rule.operand, unknown_reps, visiting | {rep}
                )
                for _pred, rule in self._resolver._get_target_rules(rep)
            )

        return any(
            self._operand_touches_unknowns(child, unknown_reps, visiting)
            for child in not_none(
                operand.try_get_sibling_trait(F.Expressions.is_expression)
            ).get_operands()
        )

    def _build_scc_candidates(
        self,
        unknown_reps: set[can_be_operand],
        prepared: dict[can_be_operand, tuple[is_literal, can_be_operand | None]],
    ) -> list[list[interval_math.Interval]]:
        """Build discrete candidate intervals (lo, hi) for each SCC unknown."""

        def _candidates_for(rep: can_be_operand) -> list[interval_math.Interval]:
            bound, _symbolic = prepared[rep]
            nums = bound.switch_cast().cast(F.Literals.Numbers)
            lo, hi = nums.get_min_value(), nums.get_max_value()
            is_eseries = fabll.Traits(rep).get_obj_raw().get_trait(F.is_eseries_value)
            return [
                (c_lo, c_hi)
                for _nominal, c_lo, c_hi in is_eseries.get_candidates_in_range(
                    max(is_eseries.practical_min, lo),
                    min(is_eseries.practical_max, hi),
                )
            ]

        candidates = [_candidates_for(rep) for rep in unknown_reps]

        if any(not c for c in candidates):
            raise Contradiction(
                "No discrete candidates remain for marked solve problem",
                involved=[
                    rep.as_parameter_operatable.force_get() for rep in unknown_reps
                ],
                mutator=self._resolver._superset_utils._mutator,
            )

        return candidates

    def _compile_scc_expression(
        self,
        operand: can_be_operand,
        unknown_slots: dict[can_be_operand, int],
        prepared: dict[can_be_operand, tuple[is_literal, can_be_operand | None]],
    ) -> Callable[[list[interval_math.Interval]], interval_math.IntervalSet]:
        """
        Walk an expression tree once and return a callable that evaluates it on float
        intervals. No graph allocation during evaluation.

        Unknown parameters are looked up by slot index.
        Solved parameters and literals are baked in as constants.
        """
        if lit := operand.as_literal.try_get():
            nums = lit.switch_cast().cast(F.Literals.Numbers)
            const: interval_math.Interval = (nums.get_min_value(), nums.get_max_value())
            return lambda _slots, _c=const: (_c,)

        if param := operand.try_get_sibling_trait(is_parameter):
            if (rep := AliasMap.rep(param)) in unknown_slots:
                idx = unknown_slots[rep]
                return lambda slots, _i=idx: (slots[_i],)

            # If this rep has a symbolic residual (depends on unknowns),
            # compile that expression instead of baking the current bound.

            if rep in prepared:
                _bound, residual = prepared[rep]
                if residual is not None:
                    return self._compile_scc_expression(
                        residual, unknown_slots, prepared
                    )
            bound = self._resolver._superset_utils.read(rep)
            nums = bound.switch_cast().cast(F.Literals.Numbers)
            const = (nums.get_min_value(), nums.get_max_value())
            return lambda _slots, _c=const: (_c,)

        expr = not_none(operand.try_get_sibling_trait(F.Expressions.is_expression))
        op_fn = not_none(
            next(
                (
                    fn
                    for expr_t, fn in _SCC_INTERVAL_OPS.items()
                    if expr.expr_isinstance(expr_t)
                )
            )
        )

        child_fns = [
            self._compile_scc_expression(child, unknown_slots, prepared)
            for child in expr.get_operands()
        ]

        def evaluate(
            slots: list[interval_math.Interval],
            _op: Callable[
                [interval_math.Interval, interval_math.Interval],
                interval_math.IntervalSet,
            ] = op_fn,
            _children: list[
                Callable[[list[interval_math.Interval]], interval_math.IntervalSet]
            ] = child_fns,
        ) -> interval_math.IntervalSet:
            child_results = [fn(slots) for fn in _children]

            # Common case: all children are single intervals
            if all(len(r) == 1 for r in child_results):
                (a,), (b,) = child_results
                return _op(a, b)

            # Multi-interval: pairwise cartesian product
            head, *tail = child_results
            return reduce(
                lambda acc, child_set: interval_math.intervals_merge(
                    [iv for a in acc for b in child_set for iv in _op(a, b)]
                ),
                tail,
                head,
            )

        return evaluate

    @staticmethod
    def _search_scc_candidates(
        *,
        candidates: list[list[interval_math.Interval]],
        checks: list[
            tuple[
                interval_math.Interval,
                Callable[[list[interval_math.Interval]], interval_math.IntervalSet],
            ]
        ],
        objective_fn: Callable[
            [list[interval_math.Interval]], interval_math.IntervalSet
        ],
        objective_target: float,
    ) -> tuple[interval_math.Interval, interval_math.Interval] | None:
        """
        Search candidate pairs using branch-and-bound.

        Candidates are sorted (E-series values increase monotonically). For each c0,
        binary-search the feasible window of c1 per check constraint, then score only
        within the intersected window.
        """

        def _feasible(slots: list[interval_math.Interval]) -> bool:
            return all(
                all(
                    interval_math.interval_is_subset(iv, bound)
                    for iv in check_fn(slots)
                )
                for bound, check_fn in checks
            )

        def _check_center(
            check_fn: Callable[
                [list[interval_math.Interval]], interval_math.IntervalSet
            ],
            slots: list[interval_math.Interval],
        ) -> float:
            if not (result := check_fn(slots)):
                return -math.inf

            return (
                interval_math.intervals_min(result)
                + interval_math.intervals_max(result)
            ) * 0.5

        def _feasible_window(
            c0: interval_math.Interval, c1s: list[interval_math.Interval]
        ) -> range:
            """Intersect per-check feasible windows found by binary search."""
            n = len(c1s)
            win_lo, win_hi = 0, n

            for bound, check_fn in checks:
                if win_lo >= win_hi:
                    return range(0, 0)

                # Determine monotonicity direction from endpoints
                val_lo = _check_center(check_fn, [c0, c1s[win_lo]])
                val_hi = _check_center(check_fn, [c0, c1s[win_hi - 1]])
                increasing = val_hi >= val_lo

                # Binary search for first c1 that enters the bound
                enter_target = bound[0] if increasing else bound[1]
                lo, hi = win_lo, win_hi
                while lo < hi:
                    mid = (lo + hi) // 2
                    val = _check_center(check_fn, [c0, c1s[mid]])
                    if (val < enter_target) if increasing else (val > enter_target):
                        lo = mid + 1
                    else:
                        hi = mid
                check_lo = max(win_lo, lo - 1)

                # Binary search for last c1 that is still in the bound
                exit_target = bound[1] if increasing else bound[0]
                lo, hi = check_lo, win_hi
                while lo < hi:
                    mid = (lo + hi) // 2
                    val = _check_center(check_fn, [c0, c1s[mid]])
                    if (val > exit_target) if increasing else (val < exit_target):
                        hi = mid
                    else:
                        lo = mid + 1
                check_hi = min(win_hi, lo + 1)

                win_lo = max(win_lo, check_lo)
                win_hi = min(win_hi, check_hi)

            return range(win_lo, win_hi)

        best_score = math.inf
        best: tuple[interval_math.Interval, interval_math.Interval] | None = None

        candidates0, candidates1 = candidates
        for c0 in candidates0:
            for j in _feasible_window(c0, candidates1):
                c1 = candidates1[j]
                slots = [c0, c1]
                if not _feasible(slots):
                    continue

                obj_result = objective_fn(slots)

                if not obj_result:
                    continue

                center = (
                    interval_math.intervals_min(obj_result)
                    + interval_math.intervals_max(obj_result)
                ) * 0.5

                score = abs(center - objective_target)

                if score < best_score:
                    best_score = score
                    best = (c0, c1)

        return best


class SimpleSolver:
    @dataclass
    class SolverState:
        mutation_map: MutationMap

        def destroy(self) -> None:
            self.mutation_map.destroy()

        def __del__(self) -> None:
            try:
                self.destroy()
            except Exception:
                pass

        def compressed(self):
            return SimpleSolver.SolverState(mutation_map=self.mutation_map.compressed())

    def __init__(self) -> None:
        self.state: SimpleSolver.SolverState | None = None

    @classmethod
    def from_initial_state(cls, state: SolverState) -> "SimpleSolver":
        out = cls()
        out.state = state
        return out

    def fork(self) -> "SimpleSolver":
        if self.state is None:
            return SimpleSolver()
        return SimpleSolver.from_initial_state(self.state.compressed())

    def _run(
        self, *, mut_map: MutationMap, target_params: list[is_parameter]
    ) -> SolverState:
        AliasMap.build(mut_map.G_out, mut_map.tg_out)

        _Resolver(
            mut_map=mut_map,
            target_reps={AliasMap.rep(p) for p in target_params},
        ).simplify()

        self.state = SimpleSolver.SolverState(mutation_map=mut_map)

        return self.state

    def simplify_for(
        self, *params: is_parameter, terminal: bool = False
    ) -> SolverState:
        del terminal

        ops = [p.as_operand.get() for p in params]
        g, tg = ops[0].g, ops[0].tg
        mm = MutationMap._with_relevance_set(g=g, tg=tg, relevant=ops, copy_types=False)

        try:
            target_params = [
                mapped.as_parameter.force_get()
                for op in ops
                if (
                    mapped := mm.map_forward(
                        op.as_parameter_operatable.force_get()
                    ).maps_to
                )
                is not None
            ]

            return self._run(mut_map=mm, target_params=target_params)
        except Exception:
            mm.destroy()
            raise

    def simplify(
        self,
        g: graph.GraphView | fbrk.TypeGraph,
        tg: fbrk.TypeGraph | graph.GraphView,
        terminal: bool = True,
        relevant: list[can_be_operand] | None = None,
    ) -> SolverState:
        raise NotImplementedError(
            "SimpleSolver requires explicit target parameters; "
            "use simplify_for() instead"
        )

    def extract_superset(
        self,
        value: is_parameter,
        g: graph.GraphView | None = None,
        tg: fbrk.TypeGraph | None = None,
    ) -> is_literal:
        g = g or value.g
        tg = tg or value.tg
        value_po = value.as_parameter_operatable.get()

        if self.state is not None:
            return not_none(
                self.state.mutation_map.try_extract_superset(
                    value_po, domain_default=True
                )
            )
        else:
            ss_lit = value_po.try_extract_superset()
            if ss_lit is None:
                return value.domain_set(g=g, tg=tg)
            return ss_lit

    def try_extract_superset(self, value: is_parameter) -> F.Literals.is_literal | None:
        value_po = value.as_parameter_operatable.get()

        if self.state is not None:
            return self.state.mutation_map.try_extract_superset(
                value_po, domain_default=False
            )
        else:
            return value_po.try_extract_superset()

    def simplify_and_extract_superset(
        self,
        value: is_parameter,
        g: graph.GraphView | None = None,
        tg: fbrk.TypeGraph | None = None,
        terminal: bool = False,
    ) -> is_literal:
        self.simplify_for(value, terminal=terminal)
        return self.extract_superset(value, g=g, tg=tg)

    def commit(self) -> None:
        pass


# TODO: tests in file
