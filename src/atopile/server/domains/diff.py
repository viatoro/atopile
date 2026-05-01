"""Diff domain service: loads PCBs, runs engine, caches results."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from atopile.server.domains.diff_engine import DiffEngine
from atopile.server.domains.diff_models import DiffConfig, DiffResult
from atopile.server.domains.layout_pcb_manager import PcbManager

log = logging.getLogger(__name__)


class DiffService:
    """Compute and cache diffs between two PCB files."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], DiffResult] = {}

    def compute_diff(
        self,
        path_a: Path,
        path_b: Path,
        config: DiffConfig | None = None,
    ) -> DiffResult:
        key = (str(path_a.resolve()), str(path_b.resolve()))
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        resolved_a = path_a.resolve()
        resolved_b = path_b.resolve()
        log.info("Computing PCB diff: %s vs %s", resolved_a, resolved_b)
        log.info(
            "File sizes: A=%d bytes, B=%d bytes, same=%s",
            resolved_a.stat().st_size,
            resolved_b.stat().st_size,
            resolved_a.stat().st_size == resolved_b.stat().st_size,
        )

        manager_a = PcbManager()
        manager_a.load(resolved_a)
        model_a = manager_a.get_render_model()

        manager_b = PcbManager()
        manager_b.load(resolved_b)
        model_b = manager_b.get_render_model()

        log.info(
            "Render models: A tracks=%d vias=%d fps=%d, B tracks=%d vias=%d fps=%d",
            len(model_a.tracks),
            len(model_a.vias),
            len(model_a.footprints),
            len(model_b.tracks),
            len(model_b.vias),
            len(model_b.footprints),
        )

        engine = DiffEngine(config)
        result = engine.compute(model_a, model_b)

        # Enrich net_names from the KiCad PCB net lists
        for net in manager_a.pcb.nets:
            if net.number and net.name and net.number not in result.net_names:
                result.net_names[net.number] = net.name
        for net in manager_b.pcb.nets:
            if net.number and net.name and net.number not in result.net_names:
                result.net_names[net.number] = net.name

        self._cache[key] = result
        return result

    async def compute_diff_async(
        self,
        path_a: Path,
        path_b: Path,
        config: DiffConfig | None = None,
    ) -> DiffResult:
        return await asyncio.to_thread(self.compute_diff, path_a, path_b, config)

    def invalidate(self, path_a: Path, path_b: Path) -> None:
        key = (str(path_a.resolve()), str(path_b.resolve()))
        self._cache.pop(key, None)

    def get_cached(self, key: tuple[str, str]) -> DiffResult | None:
        return self._cache.get(key)

    def clear_cache(self) -> None:
        self._cache.clear()


diff_service = DiffService()
