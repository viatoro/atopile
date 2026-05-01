"""Reporting and manufacturing tools.

TODO: Add optional filtering (e.g. by component/module) so the model
can request a subset of BOM/variables data instead of the full blob.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any, TypedDict

from atopile.agent.scope import Scope
from atopile.agent.tools.registry import tool
from atopile.model import artifacts
from atopile.model import manufacturing as manufacturing_domain


class ReportBomArgs(TypedDict, total=False):
    target: str


@tool("Read generated BOM data for a build target.", label="Read BOM")
async def report_bom(args: ReportBomArgs, scope: Scope) -> dict[str, Any]:
    target = str(args.get("target", "default"))
    data = await asyncio.to_thread(
        artifacts.read_artifact,
        str(scope.project_root),
        target,
        ".bom.json",
    )
    if data is None:
        raise ValueError(f"BOM not found for target '{target}'. Run build_run first.")
    return data


class ReportVariablesArgs(TypedDict, total=False):
    target: str


@tool("Read computed variables data for a build target.", label="Read variables")
async def report_variables(args: ReportVariablesArgs, scope: Scope) -> dict[str, Any]:
    target = str(args.get("target", "default"))
    data = await asyncio.to_thread(
        artifacts.read_artifact,
        str(scope.project_root),
        target,
        ".variables.json",
    )
    if data is None:
        raise ValueError(
            f"Variables not found for target '{target}'. Run build_run first."
        )
    return data


class ManufacturingSummaryArgs(TypedDict, total=False):
    target: str
    quantity: int


@tool(
    "Get build outputs and a basic manufacturing cost estimate.",
    label="Summarized build",
)
async def manufacturing_summary(
    args: ManufacturingSummaryArgs,
    scope: Scope,
) -> dict[str, Any]:
    target = str(args.get("target", "default"))
    quantity = int(args.get("quantity", 10))

    outputs = await asyncio.to_thread(
        manufacturing_domain.get_build_outputs,
        str(scope.project_root),
        target,
    )
    estimate = await asyncio.to_thread(
        manufacturing_domain.estimate_cost,
        str(scope.project_root),
        [target],
        quantity,
    )

    return {
        "target": target,
        "outputs": asdict(outputs),
        "cost_estimate": asdict(estimate),
    }
