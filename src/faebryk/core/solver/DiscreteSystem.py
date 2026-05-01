# This file is part of the faebryk project
# SPDX-License-Identifier: MIT

from enum import StrEnum

import faebryk.core.node as fabll
import faebryk.library._F as F
from faebryk.library.Expressions import (
    OperandPointer,
    OperandSequence,
    get_operand_path,
    is_assertable,
    is_expression,
    is_expression_type,
)


class DiscreteSystem(fabll.Node):
    """
    Predicate marking a system of coupled discrete parameters.

    Fields:
      objective: the parameter to optimize (OperandPointer)
      unknowns:  the parameters to search over (OperandSequence)
    """

    class ObjectiveMode(StrEnum):
        target_center = "target_center"

    can_be_operand = fabll.Traits.MakeEdge(F.Parameters.can_be_operand.MakeChild())
    is_parameter_operatable = fabll.Traits.MakeEdge(
        F.Parameters.is_parameter_operatable.MakeChild()
    )
    is_assertable = fabll.Traits.MakeEdge(is_assertable.MakeChild())
    is_expression_type = fabll.Traits.MakeEdge(
        is_expression_type.MakeChild(
            repr_style=is_expression_type.ReprStyle(
                symbol="DiscreteSystem",
                placement=is_expression_type.ReprStyle.Placement.PREFIX,
            )
        ).put_on_type()
    )
    is_expression = fabll.Traits.MakeEdge(is_expression.MakeChild())
    is_logic = fabll.Traits.MakeEdge(F.Expressions.is_logic.MakeChild())

    objective = OperandPointer.MakeChild()
    unknowns = OperandSequence.MakeChild()
    objective_mode_ = F.Collections.Pointer.MakeChild()

    @property
    def objective_mode(self) -> ObjectiveMode:
        return (
            self.objective_mode_.get()
            .deref()
            .cast(F.Literals.AbstractEnums, check=False)
            .get_single_value_typed(self.ObjectiveMode)
        )

    def get_unknown_params(self) -> list["F.Parameters.is_parameter"]:
        return [
            node.cast(F.Parameters.can_be_operand).get_sibling_trait(
                F.Parameters.is_parameter
            )
            for node in self.unknowns.get().as_list()
        ]

    def get_objective_param(self) -> "F.Parameters.is_parameter":
        return (
            self.objective.get()
            .deref()
            .cast(F.Parameters.can_be_operand)
            .get_sibling_trait(F.Parameters.is_parameter)
        )

    @classmethod
    def MakeChild(
        cls,
        *,
        unknowns: list[fabll.RefPath],
        objective: fabll.RefPath,
        objective_mode: ObjectiveMode = ObjectiveMode.target_center,
        assert_: bool = True,
    ) -> fabll._ChildField["DiscreteSystem"]:
        assert len(unknowns) == 2, "exactly two unknowns required"
        out = fabll._ChildField(DiscreteSystem)

        if assert_:
            out.add_dependant(
                fabll.Traits.MakeEdge(F.Expressions.is_predicate.MakeChild(), [out]),
            )

        out.add_dependant(
            OperandPointer.MakeEdge([out, cls.objective], get_operand_path(objective))
        )

        for index, ref in enumerate(unknowns):
            out.add_dependant(
                OperandSequence.MakeEdge(
                    [out, cls.unknowns], get_operand_path(ref), index
                )
            )

        F.Collections.Pointer.MakeEdgeForField(
            out,
            [out, cls.objective_mode_],
            F.Literals.AbstractEnums.MakeChild(objective_mode),
        )

        return out
