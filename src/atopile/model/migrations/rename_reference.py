"""Rename has_single_electric_reference_shared → has_single_electric_reference."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from ._base import MigrationStep, Topics

log = logging.getLogger(__name__)


class RenameReference(MigrationStep):
    label = "Rename has_single_electric_reference_shared"
    description = (
        "Renames the deprecated has_single_electric_reference_shared trait "
        "to match the updated API."
    )
    topic = Topics.ato_language
    order = 10

    async def run(self, project_path: Path, on_progress=None) -> None:
        def _do():
            failed: list[tuple[Path, str]] = []
            for ato_file in project_path.rglob("*.ato"):
                if ".ato" in ato_file.relative_to(project_path).parts:
                    continue
                try:
                    content = ato_file.read_text()
                    new_content = re.sub(
                        r"\bhas_single_electric_reference_shared\b",
                        "has_single_electric_reference",
                        content,
                    )
                    new_content = re.sub(
                        r"\bgnd_only\b",
                        "ground_only",
                        new_content,
                    )
                    if new_content != content:
                        self.atomic_write(ato_file, new_content)
                        log.info(
                            "[migrate] Updated %s: reference trait renames",
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
