---
name: solver
description: "How the Faebryk parameter solver works (Sets/Literals, Parameters, Expressions), the core invariants enforced during mutation, and practical workflows for debugging and extending the solver. Use when implementing or modifying constraint solving, parameter bounds, or debugging expression simplification."
---

# Solver Module

The solver is the heart of atopile's **parameter subsystem**: it symbolically simplifies and checks constraint systems built from **Parameters**, **Literals (Sets)**, and **Expressions**.

If you are touching solver internals, read these first:

- `src/faebryk/core/solver/README.md` (concepts, set correlation, append-only graphs, canonicalization)
- `src/faebryk/core/solver/symbolic/invariants.py` (the *actual* invariants enforced during expression insertion)

## Quick Start

```python
import faebryk.core.node as fabll
import faebryk.library._F as F
from faebryk.core.solver import Solver
from faebryk.libs.test.boundexpressions import BoundExpressions

E = BoundExpressions()

class _App(fabll.Node):
    x = F.Parameters.NumericParameter.MakeChild(unit=E.U.dl)

app = _App.bind_typegraph(tg=E.tg).create_instance(g=E.g)
x = app.x.get().can_be_operand.get()
E.is_subset(x, E.lit_op_range(((9, E.U.dl), (11, E.U.dl))), assert_=True)

solver = Solver()
solver.simplify_for(app.x.get().as_operand.get())
lit = solver.extract_superset(app.x.get())
assert lit is not None
```

## Relevant Files

- Solver runtime + orchestration:
  - `src/faebryk/core/solver/__init__.py` (default solver selection)
  - `src/faebryk/core/solver/simple_solver.py` (default fast target-driven solver)
  - `src/faebryk/core/solver/solver.py` (full symbolic solver + helper APIs)
- Mutation machinery (this is where “graphs are append-only” is handled):
  - `src/faebryk/core/solver/mutator.py` (`Mutator`, `Transformations`, `MutationStage`, `MutationMap`, tracebacks)
- Symbolic layer (canonical forms + invariants):
  - `src/faebryk/core/solver/symbolic/invariants.py` (`insert_expression(...)` invariant pipeline)
  - `src/faebryk/core/solver/symbolic/canonical.py` (canonicalization passes)
  - `src/faebryk/core/solver/symbolic/*` (structural + expression-wise algorithms)
- Domain objects (what users actually create in graphs):
  - `src/faebryk/library/Parameters.py` (ParameterOperatables, domains, compact repr)
  - `src/faebryk/library/Expressions.py` (expression node types, predicates, assertables)
  - `src/faebryk/library/Literals.py` (Sets; numeric/boolean/enum literals)
- Test helpers:
  - `src/faebryk/libs/test/boundexpressions.py` (concise graph + expression construction for tests)

## Dependants (Call Sites)

- Library components (`src/faebryk/library/`): define parameters/constraints (e.g. `R.resistance`)
- Compiler + frontends: translate `ato` constraints into solver expressions
- Picker backend: uses solver simplification + bounds extraction to prune candidate parts

## How to Work With / Develop / Test

### Mental Model (the parts that matter for correctness)

### 1) Literals are Sets (and correlation is subtle)
- A literal like `100kOhm +/- 10%` is a **Set** (a range), not a scalar.
- **Singleton sets are self-correlated**; all other sets are treated as **uncorrelated**, even with themselves.
  - This is why `X - X` is not necessarily `{0}` when `X` is a range, but *is* `{0}` when `X` is a singleton.

### 2) Symbols (Parameters) introduce correlation
- A `Parameter` behaves like a mathematical symbol (variable), not a Python variable.
- Correlation between symbols is created via *asserted* constraints, most notably:
  - `Is(A, B).assert_()` / `A.alias_is(B)` creates a strong “these are the same” correlation.
  - `IsSubset(A, X).assert_()` / `A.constrain_subset(X)` constrains `A` to be within `X`.
  - `IsSubset(X, A).assert_()` / `A.constrain_superset(X)` constrains `A` to accept at least `X`.

### 3) Expressions are graph objects (not just Python trees)
Expressions are nodes in the Faebryk graph that point at operand nodes. This matters because…

### 4) The underlying graphs are append-only
The solver cannot “edit” an expression in-place. Instead it:
- builds a new graph containing transformed/copied nodes,
- records a mapping from old nodes → new nodes (`MutationMap`),
- leaves the old graph untouched.

### Development Workflow

1) Reproduce in a minimal graph (prefer tests + `BoundExpressions`).
2) Prefer `Solver().simplify_for(...)` and inspect extracted bounds or the returned `MutationMap`.
   This also applies when you import the concrete full symbolic solver directly.
3) If you’re changing rewrite logic, make sure you understand and preserve the invariant pipeline in
   `src/faebryk/core/solver/symbolic/invariants.py::insert_expression`.
4) Add/adjust algorithms in `src/faebryk/core/solver/symbolic/*` (most logic lives there, not in `mutator.py`).

### Testing
- Solver tests live in `test/core/solver/`:
  - `test/core/solver/test_solver.py`
  - `test/core/solver/test_literal_folding.py`
  - `test/core/solver/test_solver_util.py`

Run a tight loop while iterating:

- `ato dev test --llm test/core/solver -k invariant -q`
- `ato dev test --llm test/core/solver/test_solver.py::test_simplify -q`

## Best Practices

### Prefer `simplify_for(...)`
The solver API is target-driven. In new code, prefer:

```python
solver = Solver()
state = solver.simplify_for(param.as_operand.get(), terminal=False)
mutation_map = state.mutation_map
```

This preference also applies to the concrete full symbolic solver. `simplify(...)` remains available for compatibility and still appears in some full-solver-only tests, but it is not the preferred entry point for new code.

### Use the `Mutator`/`insert_expression` pipeline, not ad-hoc rewrites
When you “create” or “rewrite” an expression, you are really requesting that the solver insert something into the
transient graph while upholding invariants. The canonical place where this happens is:

- `src/faebryk/core/solver/symbolic/invariants.py::insert_expression`

If you bypass this, you will almost certainly violate an invariant and get:
- duplicate/congruent expressions,
- multiple incompatible bounds on an operand,
- predicates used as operands,
- missed literal folding, or
- contradictions that don’t point back to the real root cause.

## Core Invariants (source of truth: `insert_expression`)

The invariant pipeline is sequencing-sensitive. At a high level it enforces (paraphrased):

- No predicate operands: `Op(P!, ...)` is rewritten to use boolean literals where possible
- Predicate literal rules: `P{S|True} -> P!`; `P!{S/P|False} -> Contradiction`; `P!{S|True} -> P!`
- No literal inequalities: inequalities involving literals are rewritten into subset constraints
- No singleton supersets as operands: `f(A{S|{x}}, ...) -> f(x, ...)`
- No congruence: congruent expressions are deduplicated (with optional rules for uncorrelated congruence)
- Minimal subsumption: stronger constraints subsume weaker ones; redundant ones become irrelevant
- Single “merged” superset/subset per operand (e.g. intersected supersets)
- No empty supersets/subsets: empty-set constraints are contradictions
- Fold pure literal expressions into literals (and re-express as subset/superset where appropriate)
- Terminate certain literal subset constraints to stop churn
- Canonical form: expressions are created/normalized into canonical operators

When adding a new algorithm, the easiest way to stay correct is to construct a new `ExpressionBuilder`
and let `insert_expression` do the hard work.

## Internals & Runtime Behavior

### Instantiation & Dependencies
- **`Solver()` holds state**: after `simplify_for(...)`, the solver keeps the resulting `MutationMap` for bound extraction.
- **Terminal vs non-terminal**:
  - On the simple solver, `terminal` is accepted for API compatibility but solving is target-driven through `simplify_for(...)`.
  - On the full symbolic solver, `simplify(...)` still distinguishes terminal vs non-terminal symbolic phases.
- **Graph scoping**: on the simple solver, `simplify_for(...)` is the scoping mechanism. On the full symbolic solver, `simplify(..., relevant=[...])` still exists.

### Data Structures
- `MutationStage`: one algorithm application over an input graph → output graph, with a `Transformations` object.
- `MutationMap`: a chain of stages; lets you:
  - map old → new operables (`map_forward`)
  - map new → old sources (`map_backward`)
  - extract current bounds as literals (`try_extract_superset`; subset extraction is typically via the mapped operable’s `try_extract_subset()`)
  - generate tracebacks for “why did this change?” (see `Traceback` in `mutator.py`)

### Debugging & Logging
Useful config flags (see `src/faebryk/core/solver/utils.py`):

- `SLOG=1`: debug logging for solver/mutator
- `SPRINT_START=1`: log start of each phase
- `SVERBOSE_TABLE=1`: verbose mutation tables
- `SSHOW_SS_IS=1`: include subset/is predicates in graph printouts
- `SMAX_ITERATIONS=N`: raise early if stuck looping (helps catch bad rewrites)

In failures, look for `Contradiction` / `ContradictionByLiteral` output: it prints mutation tracebacks back to
origin expressions/parameters, which is usually the shortest path to the actual bug.

### Performance
- Prefer restricting scope via `relevant=[...]` when you can.
- Avoid creating huge numbers of near-duplicate expressions; congruence + subsumption help, but churn still costs.
- If you add an algorithm, make it *idempotent* (or explicitly mark/terminate what you produce) to avoid infinite iteration.
