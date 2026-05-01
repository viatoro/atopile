__all__ = ["Solver"]


def __getattr__(name: str):
    if name == "Solver":
        from faebryk.core.solver.utils import FULL_SOLVER

        if FULL_SOLVER:
            from faebryk.core.solver.solver import Solver
        else:
            from faebryk.core.solver.simple_solver import SimpleSolver as Solver
        return Solver
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
