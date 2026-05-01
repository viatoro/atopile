"""UI sidebar state workflows for the websocket store."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable, cast

from atopile.data_models import (
    PackageDetails,
    PackagesSummaryData,
    PackageSummaryItem,
    Project,
    UiBOMData,
    UiBuildsByProjectData,
    UiLcscPartsData,
    UiMigrationState,
    UiMigrationStep,
    UiMigrationStepResult,
    UiMigrationTopic,
    UiPackageDetailState,
    UiPartsSearchData,
    UiPinoutData,
    UiProjectFilesData,
    UiProjectState,
    UiSidebarDetails,
    UiStackupData,
    UiStructureData,
    UiVariablesData,
)
from atopile.lsp.lsp_server import reset_type_caches
from atopile.model import migrations, packages, parts, projects
from atopile.model.file_watcher import FileWatcher
from atopile.model.module_introspection import (
    introspect_module_definition,
)
from atopile.server.ui.store import Store


def clear() -> UiSidebarDetails:
    """Return the empty sidebar details state."""
    return UiSidebarDetails()


def refresh_project_structure_data(store: Store, project_root: str | None) -> None:
    """Refresh project structure state synchronously."""
    store.set(
        "structure_data",
        UiStructureData(project_root=project_root)
        if project_root
        else UiStructureData(loading=False),
    )
    if not project_root:
        return

    project_path = Path(project_root)
    modules = []
    error = None

    try:
        modules_result = projects.handle_get_modules(project_root)
        if modules_result:
            for module in modules_result.modules:
                modules.append(introspect_module_definition(project_path, module))
    except Exception:
        reset_type_caches()
        error = "Structure view disabled until you press Refresh."

    store.set(
        "structure_data",
        UiStructureData(
            project_root=project_root,
            modules=modules,
            total=len(modules),
            loading=False,
            error=error,
        ),
    )


async def apply_project_selection(
    *,
    store: Store,
    project_files: FileWatcher,
    sync_selected_layout: Callable[..., Awaitable[None]],
    sync_selected_pinout: Callable[..., Awaitable[None]],
    set_layout_data: Callable[..., None],
    selection_lock: asyncio.Lock,
    project_list: list[Project] | None = None,
    project_state: UiProjectState | None = None,
) -> UiProjectState:
    """Update the project list/state, refresh project metadata, and —
    only when the selected project or target actually changed — reset
    project-scoped panel stores (parts_search, bom, pinout, …).

    The workspace-metadata FileWatcher routes every ``ato.yaml`` edit
    through this function with the same selection, so a blanket reset
    would clobber state the originating handler just refreshed
    (e.g. the parts panel after an install/uninstall). Metadata that
    legitimately changes with ``ato.yaml`` — the project list and the
    module structure (discoverable ``.ato`` files pick up newly
    installed packages) — is refreshed unconditionally.
    """
    from atopile.model import builds

    async with selection_lock:
        previous_project_state = cast(UiProjectState, store.get("project_state"))
        next_project_state = (
            previous_project_state if project_state is None else project_state
        )
        if project_list is not None:
            store.set("projects", project_list)
        store.set("project_state", next_project_state)

        project_root = next_project_state.selected_project_root
        target = next_project_state.selected_target

        selection_changed = not projects.same_selection(
            previous_project_state.selected_project_root,
            previous_project_state.selected_target,
            next_project_state.selected_project_root,
            next_project_state.selected_target,
        )
        if not selection_changed:
            # Structure introspection is still valid to redo: installed
            # packages contribute `.ato` modules that appear via rglob,
            # so dep changes must propagate to the Structure panel.
            asyncio.create_task(
                asyncio.to_thread(refresh_project_structure_data, store, project_root)
            )
            return next_project_state

        store.set("selected_build", builds.get_selected_build(target))
        store.set("variables_data", UiVariablesData())
        store.set(
            "parts_search",
            parts.PartUiState.empty_search_state(
                cast(UiPartsSearchData, store.get("parts_search")),
                project_root=project_root,
            ),
        )
        store.set(
            "bom_data",
            UiBOMData(
                project_root=project_root,
                target=target,
            ),
        )
        store.set(
            "lcsc_parts_data",
            UiLcscPartsData(
                project_root=project_root,
                target=target,
            ),
        )
        store.set(
            "pinout_data",
            UiPinoutData(
                project_root=project_root,
                target=target,
            ),
        )
        store.set(
            "stackup_data",
            UiStackupData(
                project_root=project_root,
                target=target,
            ),
        )
        store.set(
            "builds_by_project_data",
            UiBuildsByProjectData(
                project_root=project_root,
                target=target,
            ),
        )
        store.set(
            "project_files",
            UiProjectFilesData(
                project_root=project_root,
                loading=bool(project_root),
            ),
        )
        set_layout_data(
            project_root,
            target,
            loading=bool(project_root and target),
        )

        await asyncio.gather(
            sync_selected_layout(),
            sync_selected_pinout(),
        )
        if project_root:
            asyncio.create_task(project_files.watch([Path(project_root)]))
        else:
            asyncio.create_task(asyncio.to_thread(project_files.stop))

    # Run outside the lock — introspection is slow and _kill_active()
    # handles cancellation if a new selection comes in.
    asyncio.create_task(
        asyncio.to_thread(refresh_project_structure_data, store, project_root)
    )
    return next_project_state


async def show_package_details(
    store: Store,
    project_root: str | None,
    package_id: str,
    *,
    action_error: str | None = None,
) -> None:
    """Set package details loading state and then resolve the details."""
    packages_summary = cast(PackagesSummaryData, store.get("packages_summary"))
    state = package_details_loading(
        cast(UiSidebarDetails, store.get("sidebar_details")),
        packages_summary,
        project_root,
        package_id,
        action_error=action_error,
    )
    store.set("sidebar_details", state)
    store.set(
        "sidebar_details",
        await load_package_details(
            state,
            packages_summary,
            project_root,
            package_id,
            action_error=action_error,
        ),
    )


async def show_migration_details(store: Store, project_root: str) -> None:
    """Set migration details loading state and then resolve the details."""
    projects_state = cast(list[Project], store.get("projects"))
    state = migration_details_loading(
        cast(UiSidebarDetails, store.get("sidebar_details")),
        projects_state,
        project_root,
    )
    store.set("sidebar_details", state)
    store.set(
        "sidebar_details",
        await load_migration_details(
            state,
            projects_state,
            project_root,
        ),
    )


def package_details_loading(
    state: UiSidebarDetails,
    packages_summary: PackagesSummaryData,
    project_root: str | None,
    package_id: str,
    *,
    action_error: str | None = None,
) -> UiSidebarDetails:
    """Set package details into loading state."""
    return _set_package_details(
        state,
        packages_summary,
        project_root,
        package_id,
        loading=True,
        action_error=action_error,
    )


async def load_package_details(
    state: UiSidebarDetails,
    packages_summary: PackagesSummaryData,
    project_root: str | None,
    package_id: str,
    *,
    action_error: str | None = None,
) -> UiSidebarDetails:
    """Load package detail data and return the next sidebar state."""
    try:
        details = await asyncio.to_thread(
            packages.handle_get_package_details,
            package_id,
            Path(project_root) if project_root else None,
            None,
        )
    except Exception as exc:
        return _set_package_details(
            state,
            packages_summary,
            project_root,
            package_id,
            loading=False,
            error=str(exc),
            action_error=action_error,
        )

    if details is None:
        return _set_package_details(
            state,
            packages_summary,
            project_root,
            package_id,
            loading=False,
            error=f"No details found for {package_id}.",
            action_error=action_error,
        )

    return _set_package_details(
        state,
        packages_summary,
        project_root,
        package_id,
        loading=False,
        details=details,
        action_error=action_error,
    )


def migration_details_loading(
    state: UiSidebarDetails,
    projects_state: list[Project],
    project_root: str,
) -> UiSidebarDetails:
    """Set migration details into loading state."""
    project = projects.find_project(projects_state, project_root)
    return _set_migration_details(
        state,
        UiMigrationState(
            project_root=project_root,
            project_name=_project_name(project_root, project),
            needs_migration=bool(project and project.needs_migration),
            loading=True,
        ),
    )


async def load_migration_details(
    state: UiSidebarDetails,
    projects_state: list[Project],
    project_root: str,
) -> UiSidebarDetails:
    """Load migration metadata and return the next sidebar state."""
    project = projects.find_project(projects_state, project_root)
    try:
        steps = [
            UiMigrationStep.model_validate(step.to_dict())
            for step in migrations.get_all_steps()
        ]
        topics = [
            UiMigrationTopic.model_validate(topic) for topic in migrations.get_topics()
        ]
    except Exception as exc:
        return _set_migration_details(
            state,
            UiMigrationState(
                project_root=project_root,
                project_name=_project_name(project_root, project),
                needs_migration=bool(project and project.needs_migration),
                loading=False,
                error=str(exc),
            ),
        )

    return _set_migration_details(
        state,
        UiMigrationState(
            project_root=project_root,
            project_name=_project_name(project_root, project),
            needs_migration=bool(project and project.needs_migration),
            steps=steps,
            topics=topics,
            step_results=[UiMigrationStepResult(step_id=step.id) for step in steps],
            loading=False,
        ),
    )


def update_migration_project_state(
    state: UiSidebarDetails,
    projects_state: list[Project],
    project_root: str,
) -> UiSidebarDetails:
    """Refresh migration project metadata after project changes."""
    project = projects.find_project(projects_state, project_root)
    return _set_migration_details(
        state,
        state.migration.model_copy(
            update={
                "project_root": project_root,
                "project_name": _project_name(project_root, project),
                "needs_migration": bool(project and project.needs_migration),
                "loading": False,
            }
        ),
    )


def start_migration_run(
    state: UiSidebarDetails,
    selected_steps: list[str],
) -> UiSidebarDetails:
    """Mark selected migration steps as running."""
    return _set_migration_details(
        state,
        state.migration.model_copy(
            update={
                "step_results": [
                    UiMigrationStepResult(
                        step_id=step.id,
                        status="running" if step.id in selected_steps else "idle",
                    )
                    for step in state.migration.steps
                ],
                "loading": False,
                "running": True,
                "completed": False,
                "error": None,
            }
        ),
    )


def finish_migration_step(
    state: UiSidebarDetails,
    step_id: str,
    *,
    error: str | None,
) -> UiSidebarDetails:
    """Apply the result of a single migration step."""
    status = "error" if error else "success"
    return _set_migration_details(
        state,
        state.migration.model_copy(
            update={
                "step_results": [
                    UiMigrationStepResult(
                        step_id=result.step_id,
                        status=status,
                        error=error,
                    )
                    if result.step_id == step_id
                    else result
                    for result in state.migration.step_results
                ],
                "loading": False,
                "running": True,
                "completed": False,
                "error": None,
            }
        ),
    )


def update_migration_step_progress(
    state: UiSidebarDetails,
    step_id: str,
    stage: str,
    message: str,
    completed: int,
    total: int,
) -> UiSidebarDetails:
    """Update sync_progress on a running migration step."""
    from atopile.data_models import UiPackageSyncProgress

    return _set_migration_details(
        state,
        state.migration.model_copy(
            update={
                "step_results": [
                    result.model_copy(
                        update={
                            "sync_progress": UiPackageSyncProgress(
                                stage=stage,
                                message=message,
                                completed=completed,
                                total=total,
                            )
                        }
                    )
                    if result.step_id == step_id
                    else result
                    for result in state.migration.step_results
                ],
            }
        ),
    )


def complete_migration_run(state: UiSidebarDetails) -> tuple[UiSidebarDetails, bool]:
    """Finalize a migration run and report whether it succeeded."""
    has_errors = any(
        result.status == "error" for result in state.migration.step_results
    )
    return (
        _set_migration_details(
            state,
            state.migration.model_copy(
                update={
                    "loading": False,
                    "running": False,
                    "completed": True,
                    "error": None if not has_errors else "Some migration steps failed.",
                }
            ),
        ),
        not has_errors,
    )


def _set_package_details(
    state: UiSidebarDetails,
    packages_summary: PackagesSummaryData,
    project_root: str | None,
    package_id: str,
    *,
    loading: bool,
    error: str | None = None,
    action_error: str | None = None,
    details: PackageDetails | None = None,
) -> UiSidebarDetails:
    return state.model_copy(
        update={
            "view": "package",
            "package": UiPackageDetailState(
                project_root=project_root,
                package_id=package_id,
                summary=_find_package_summary(packages_summary, package_id),
                details=details,
                loading=loading,
                error=error,
                action_error=action_error,
            ),
        }
    )


def _find_package_summary(
    packages_summary: PackagesSummaryData,
    package_id: str,
) -> PackageSummaryItem:
    match = next(
        (pkg for pkg in packages_summary.packages if pkg.identifier == package_id),
        None,
    )
    if match:
        return match

    publisher, _, name = package_id.partition("/")
    return PackageSummaryItem(
        identifier=package_id,
        name=name or package_id,
        publisher=publisher or "unknown",
        installed=False,
    )


def _set_migration_details(
    state: UiSidebarDetails,
    migration: UiMigrationState,
) -> UiSidebarDetails:
    return state.model_copy(update={"view": "migration", "migration": migration})


def _project_name(project_root: str, project: Project | None) -> str:
    return project.name if project else Path(project_root).name
