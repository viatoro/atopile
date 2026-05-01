"""CLI command definition for `ato build`."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import typer
from typing_extensions import Annotated

from atopile.buildutil import generate_build_id
from atopile.data_models import (
    Build,
    BuildStatus,
    ResolvedBuildTarget,
)
from atopile.logging import get_logger
from atopile.logging_utils import BuildPrinter
from atopile.model.build_queue import BuildQueue
from atopile.telemetry.telemetry import capture

logger = get_logger(__name__)

# Constants
DEFAULT_WORKER_COUNT = os.cpu_count() or 4


def discover_projects(root: Path) -> list[Path]:
    """
    Discover ato projects in a directory.

    If root contains ato.yaml, returns [root].
    Otherwise, finds all directories containing ato.yaml below root.
    """
    config_file = root / "ato.yaml"
    if config_file.exists():
        return [root]

    # Find all ato.yaml files below root (non-recursive in .ato/modules)
    projects = []
    for path in root.rglob("ato.yaml"):
        # Skip .ato/modules (dependencies)
        if ".ato" in path.parts:
            continue
        projects.append(path.parent)

    return sorted(projects)


def _run_build_queue(
    builds: list[Build],
    *,
    jobs: int,
    verbose: bool,
) -> dict[str, int]:
    """Run builds through a local BuildQueue and return build_id -> exit code."""
    from atopile.model.sqlite import BuildHistory

    if not builds:
        return {}

    # Initialize database
    BuildHistory.init_db()

    # Verbose mode runs sequentially (one build at a time)
    max_concurrent = 1 if verbose else jobs
    queue = BuildQueue(max_concurrent=max_concurrent)

    submitted_builds = queue.submit_builds(builds)
    build_ids = [build.build_id for build in submitted_builds if build.build_id]
    display_names = {
        build.build_id: build.name for build in submitted_builds if build.build_id
    }

    started: set[str] = set()
    reported: set[str] = set()

    TERMINAL_STATUSES = (
        BuildStatus.SUCCESS,
        BuildStatus.WARNING,
        BuildStatus.FAILED,
        BuildStatus.CANCELLED,
    )

    with BuildPrinter(verbose=verbose) as printer:

        def on_update() -> None:
            for build_id in build_ids:
                build = BuildHistory.get(build_id)
                if not build:
                    continue

                display_name = display_names.get(build_id, build.name)

                # Build started
                if (
                    build.status in (BuildStatus.BUILDING, *TERMINAL_STATUSES)
                    and build_id not in started
                ):
                    printer.build_started(
                        build_id, display_name, total=build.total_stages
                    )
                    started.add(build_id)

                # Stage updates
                if build.stages:
                    printer.stage_update(build_id, build.stages, build.total_stages)

                # Build completed
                if build.status in TERMINAL_STATUSES and build_id not in reported:
                    printer.build_completed(
                        build_id,
                        build.status,
                        warnings=build.warnings,
                        errors=build.errors,
                    )
                    reported.add(build_id)

        results = queue.wait_for_builds(
            build_ids, on_update=on_update, poll_interval=0.1
        )

        # Print build summary boxes after all builds complete
        completed_builds = [
            b for build_id in build_ids if (b := BuildHistory.get(build_id))
        ]
        printer.print_summary(completed_builds)

        return results


def _build_all_projects(
    root: Path,
    jobs: int,
    frozen: bool | None = None,
    selected_builds: list[str] | None = None,
    verbose: bool = False,
    targets: list[str] | None = None,
    exclude_targets: list[str] | None = None,
    keep_picked_parts: bool | None = None,
    keep_net_names: bool | None = None,
    keep_designators: bool | None = None,
    standardize_designators: bool | None = None,
) -> None:
    """
    Build all projects in a directory.

    Discovers all ato.yaml files and builds all their targets.
    Use -b to filter to specific build targets across all projects.
    """
    from atopile.config import ProjectConfig

    # Discover projects
    projects = discover_projects(root)

    if not projects:
        logger.error("No ato projects found in %s", root)
        raise typer.Exit(1)

    logger.info("Found %d projects", len(projects))

    builds: list[Build] = []

    for project_path in projects:
        project_name = project_path.name
        resolved_project_root = str(project_path.resolve())

        # Load project config to get build targets
        project_config = ProjectConfig.from_path(project_path)
        if project_config is None:
            logger.warning("Skipping %s: could not load config", project_name)
            continue

        # Get builds to run for this project
        if selected_builds:
            # Use specified builds if they exist in this project
            build_names = [b for b in selected_builds if b in project_config.builds]
        else:
            # Build ALL targets in the project
            build_names = list(project_config.builds.keys())

        if not build_names:
            logger.warning("Skipping %s: no matching builds", project_name)
            continue

        for build_name in build_names:
            build_cfg = project_config.builds[build_name]
            build_target = ResolvedBuildTarget(
                name=build_name,
                entry=build_cfg.address or "",
                pcb_path=str(build_cfg.paths.layout),
                model_path=str(build_cfg.paths.output_base.with_suffix(".pcba.glb")),
                root=resolved_project_root,
            )
            started_at = time.time()
            builds.append(
                Build(
                    build_id=generate_build_id(
                        build_target.root, build_target.name, started_at
                    ),
                    name=build_target.name,
                    project_root=build_target.root,
                    project_name=project_name,
                    target=build_target,
                    frozen=frozen,
                    status=BuildStatus.QUEUED,
                    started_at=started_at,
                    include_targets=targets or [],
                    exclude_targets=exclude_targets or [],
                    keep_picked_parts=keep_picked_parts,
                    keep_net_names=keep_net_names,
                    keep_designators=keep_designators,
                    standardize_designators=standardize_designators,
                    verbose=verbose,
                )
            )

    if not builds:
        logger.error("No builds to run")
        raise typer.Exit(1)

    logger.info(
        "Building %d targets across %d projects (max %d concurrent)",
        len(builds),
        len(projects),
        jobs,
    )

    results = _run_build_queue(builds, jobs=jobs, verbose=verbose)

    build_by_id = {build.build_id: build for build in builds if build.build_id}
    failed = [
        build_by_id[build_id].name
        for build_id, code in results.items()
        if code != 0 and build_id in build_by_id
    ]
    exit_code = _report_build_results(
        failed=failed,
        total=len(builds),
        failed_names=failed[:10],
    )
    if exit_code != 0:
        raise typer.Exit(exit_code)


@capture(
    "cli:build_start",
    "cli:build_end",
)
def build(
    entry: Annotated[
        str | None,
        typer.Argument(
            help="Path to the project directory or build target address "
            '("path_to.ato:Module")'
        ),
    ] = None,
    selected_builds: Annotated[
        list[str], typer.Option("--build", "-b", envvar="ATO_BUILD")
    ] = [],
    target: Annotated[
        list[str], typer.Option("--target", "-t", envvar="ATO_TARGET")
    ] = [],
    exclude_target: Annotated[
        list[str], typer.Option("--exclude-target", "-x", envvar="ATO_EXCLUDE_TARGET")
    ] = [],
    frozen: Annotated[
        bool | None,
        typer.Option(
            help="PCB must be rebuilt without changes. Useful in CI",
            envvar="ATO_FROZEN",
        ),
    ] = None,
    keep_picked_parts: Annotated[
        bool | None,
        typer.Option(
            help="Keep previously picked parts from PCB",
            envvar="ATO_KEEP_PICKED_PARTS",
        ),
    ] = None,
    keep_net_names: Annotated[
        bool | None,
        typer.Option(
            help="Keep net names from PCB",
            envvar="ATO_KEEP_NET_NAMES",
        ),
    ] = None,
    keep_designators: Annotated[
        bool | None,
        typer.Option(
            help="Keep designators from PCB",
            envvar="ATO_KEEP_DESIGNATORS",
        ),
    ] = None,
    standardize_designators: Annotated[
        bool | None,
        typer.Option(
            help="Standardize designator positions on silkscreen",
            envvar="ATO_STANDARDIZE_DESIGNATORS",
        ),
    ] = None,
    standalone: bool = False,
    open_layout: Annotated[
        bool | None, typer.Option("--open", envvar="ATO_OPEN_LAYOUT")
    ] = None,
    all_projects: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Build all projects in directory (recursively finds ato.yaml)",
        ),
    ] = False,
    jobs: Annotated[
        int,
        typer.Option(
            "--jobs",
            "-j",
            help=f"Max concurrent builds (default: {DEFAULT_WORKER_COUNT})",
        ),
    ] = DEFAULT_WORKER_COUNT,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Run sequentially without live display",
        ),
    ] = False,
):
    """
    Build the specified --target(s) or the targets specified by the build config.
    Optionally specify a different entrypoint with the argument ENTRY.

    Use --all to build all projects in a directory (e.g., `ato build --all`).

    eg. `ato build --target my_target path/to/source.ato:module.path`
    """
    from atopile.config import config
    from faebryk.libs.app.pcb import open_pcb
    from faebryk.libs.kicad.ipc import reload_pcb

    # Check for verbose mode from CLI flag or environment variable (for workers)
    if verbose or os.environ.get("ATO_VERBOSE") == "1":
        logging.getLogger().setLevel(logging.DEBUG)

    # Enable faulthandler for crash debugging in workers
    if os.environ.get("ATO_SAFE"):
        import faulthandler

        faulthandler.enable()

    # Multi-project mode: discover and build all projects
    if all_projects:
        _build_all_projects(
            root=Path.cwd(),
            jobs=jobs,
            frozen=frozen,
            selected_builds=selected_builds,
            verbose=verbose,
            targets=target,
            exclude_targets=exclude_target,
            keep_picked_parts=keep_picked_parts,
            keep_net_names=keep_net_names,
            keep_designators=keep_designators,
            standardize_designators=standardize_designators,
        )
        return

    # Single project mode
    config.apply_options(
        entry=entry,
        selected_builds=selected_builds,
        include_targets=target,
        exclude_targets=exclude_target,
        standalone=standalone,
        frozen=frozen,
        keep_picked_parts=keep_picked_parts,
        keep_net_names=keep_net_names,
        keep_designators=keep_designators,
        standardize_designators=standardize_designators,
    )

    if open_layout is not None:
        config.project.open_layout_on_build = open_layout

    # Get the list of builds to run
    build_names = list(config.selected_builds)
    project_root = config.project.paths.root
    resolved_project_root = str(project_root.resolve())

    builds: list[Build] = []
    for build_name in build_names:
        build_target = (
            ResolvedBuildTarget(
                name=build_name,
                entry=entry or "",
                pcb_path="",
                model_path="",
                root=resolved_project_root,
            )
            if standalone
            else ResolvedBuildTarget(
                name=build_name,
                entry=config.project.builds[build_name].address or "",
                pcb_path=str(config.project.builds[build_name].paths.layout),
                model_path=str(
                    config.project.builds[build_name].paths.output_base.with_suffix(
                        ".pcba.glb"
                    )
                ),
                root=resolved_project_root,
            )
        )
        started_at = time.time()
        build_id = generate_build_id(build_target.root, build_name, started_at)
        builds.append(
            Build(
                build_id=build_id,
                name=build_name,
                project_root=resolved_project_root,
                target=build_target,
                standalone=standalone,
                frozen=frozen,
                status=BuildStatus.QUEUED,
                started_at=started_at,
                include_targets=target,
                exclude_targets=exclude_target,
                keep_picked_parts=keep_picked_parts,
                keep_net_names=keep_net_names,
                keep_designators=keep_designators,
                standardize_designators=standardize_designators,
                verbose=verbose,
            )
        )

    results = _run_build_queue(builds, jobs=jobs, verbose=verbose)

    build_by_id = {build.build_id: build for build in builds if build.build_id}
    failed = [
        build_by_id[build_id].name
        for build_id, code in results.items()
        if code != 0 and build_id in build_by_id
    ]

    build_exit_code = _report_build_results(
        failed=failed,
        total=len(build_names),
        failed_names=failed,
    )

    # Open layouts if requested
    for build_name in build_names:
        build_cfg = config.project.builds[build_name]

        opened = False
        if config.should_open_layout_on_build():
            try:
                open_pcb(build_cfg.paths.layout)
                opened = True
            except FileNotFoundError:
                continue
            except RuntimeError:
                pass

        if not opened:
            try:
                reload_pcb(
                    build_cfg.paths.layout, backup_path=build_cfg.paths.output_base
                )
            except Exception as e:
                logger.warning(f"{e}\nReload pcb manually in KiCAD")

    from atopile.logging import AtoLogger

    AtoLogger.close_all()

    if build_exit_code != 0:
        raise typer.Exit(build_exit_code)


def _report_build_results(
    *,
    failed: list[str],
    total: int,
    failed_names: list[str] | None = None,
) -> int:
    """Report build results and return exit code (0 for success, 1 for failure)."""
    if failed:
        from atopile.errors import log_discord_banner

        log_discord_banner()
        logger.error("Build failed! %d of %d targets failed", len(failed), total)
        if failed_names:
            for name in failed_names:
                logger.error("  - %s", name)
        remaining = len(failed) - (len(failed_names) if failed_names else 0)
        if remaining > 0:
            logger.error("  ... and %d more", remaining)
        return 1

    if total > 1:
        logger.info("Build successful! 🚀 (%d targets)", total)
    else:
        logger.info("Build successful! 🚀")
    return 0
