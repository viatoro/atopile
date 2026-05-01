"""Rename has_datasheet_defined → has_datasheet in .ato files."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from ._base import MigrationStep, Topics

log = logging.getLogger(__name__)


class RenameDatasheet(MigrationStep):
    label = "Rename has_datasheet_defined"
    description = (
        "Renames the deprecated has_datasheet_defined trait to the new naming "
        "convention used in the latest standard library."
    )
    topic = Topics.ato_language
    order = 0

    async def run(self, project_path: Path, on_progress=None) -> None:
        def _do():
            failed: list[tuple[Path, str]] = []
            for ato_file in project_path.rglob("*.ato"):
                if ".ato" in ato_file.relative_to(project_path).parts:
                    continue
                try:
                    content = ato_file.read_text()
                    new_content = re.sub(
                        r"\bhas_datasheet_defined\b",
                        "has_datasheet",
                        content,
                    )
                    if new_content != content:
                        self.atomic_write(ato_file, new_content)
                        log.info(
                            "[migrate] Updated %s: "
                            "has_datasheet_defined -> has_datasheet",
                            ato_file,
                        )
                except Exception as exc:
                    failed.append((ato_file, str(exc)))
                    log.warning("[migrate] Failed to process %s: %s", ato_file, exc)
            if failed:
                details = "; ".join(f"{f}: {e}" for f, e in failed)
                raise RuntimeError(
                    f"Failed to migrate {len(failed)} file(s): {details}"
                )

        await asyncio.to_thread(_do)
