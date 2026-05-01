"""KiCad lifecycle service — launch, focus, and readiness polling."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from atopile.logging import get_logger

log = get_logger(__name__)


def _clean_env_for_kicad() -> dict[str, str]:
    """Return a copy of os.environ with VIRTUAL_ENV stripped.

    KiCad's internal Python scripting breaks when our venv is active,
    so we remove VIRTUAL_ENV and its bin dir from PATH.
    """
    env = dict(os.environ)
    venv = env.pop("VIRTUAL_ENV", None)
    if venv:
        path_dirs = env.get("PATH", "").split(os.pathsep)
        venv_bins = [
            os.path.join(venv, "bin"),  # Linux / macOS
            os.path.join(venv, "Scripts"),  # Windows
        ]
        env["PATH"] = os.pathsep.join(d for d in path_dirs if d not in venv_bins)
    return env


def _launch_pcbnew(pcb_path: Path | None) -> None:
    """Spawn pcbnew as a detached subprocess.

    Raises on immediate spawn failure (binary not found, permission denied).
    """
    from faebryk.libs.kicad.paths import find_pcbnew

    exe = find_pcbnew()
    args = [str(exe)]
    if pcb_path is not None:
        args.append(str(pcb_path))

    log.info("Launching pcbnew: %s", args)
    proc = subprocess.Popen(
        args,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_clean_env_for_kicad(),
    )

    # Brief sleep to catch immediate crashes (binary missing, permission denied).
    import time

    time.sleep(0.5)
    rc = proc.poll()
    if rc is not None and rc != 0:
        raise RuntimeError(f"pcbnew exited immediately with code {rc}")


def _focus_pcbnew() -> None:
    """Best-effort attempt to focus the already-running pcbnew window."""
    try:
        platform = sys.platform
        if platform == "darwin":
            subprocess.run(
                ["osascript", "-e", 'tell application "pcbnew" to activate'],
                capture_output=True,
            )
        elif platform == "win32":
            log.debug("Window focus not implemented on Windows")
        else:
            # Linux (X11 only) — wmctrl may not be installed
            result = subprocess.run(
                ["wmctrl", "-a", "pcbnew"],
                capture_output=True,
            )
            if result.returncode != 0:
                log.warning("Failed to focus pcbnew (wmctrl): %s", result.stderr)
    except Exception:
        log.warning("Failed to focus pcbnew", exc_info=True)


class KicadService:
    """Business logic for KiCad launch/focus orchestration."""

    async def open_kicad(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Check if KiCad is running; focus it or launch + poll for readiness.

        Returns ``{"status": "focused"}`` or ``{"status": "launched"}``.
        Raises on failure.
        """
        from faebryk.libs.kicad.ipc import opened_in_pcbnew

        target = msg.get("target") or {}
        pcb_path_str = str(target.get("pcbPath") or "")
        # pcb_path=None is intentional: kipy's opened_in_pcbnew(None) checks
        # whether *any* KiCad instance is running, which is fine when no
        # specific PCB path is configured for the target.
        pcb_path = Path(pcb_path_str) if pcb_path_str else None

        try:
            already_running = await asyncio.to_thread(opened_in_pcbnew, pcb_path)
        except Exception:
            # Socket exists but KiCad not ready — treat as not running
            already_running = False
        log.info(
            "openKicad: already_running=%s pcb_path=%s",
            already_running,
            pcb_path,
        )

        if already_running:
            await asyncio.to_thread(_focus_pcbnew)
            return {"status": "focused"}

        log.info("openKicad: launching pcbnew directly")
        await asyncio.to_thread(_launch_pcbnew, pcb_path)

        # Poll until pcbnew IPC is responsive (20 × 500ms = 10s)
        ready = False
        for _ in range(20):
            await asyncio.sleep(0.5)
            try:
                ready = await asyncio.to_thread(opened_in_pcbnew, pcb_path)
                if ready:
                    break
            except Exception:
                # KiCad socket exists but not ready yet — keep polling
                pass

        if not ready:
            raise RuntimeError(
                "KiCad was launched but did not become responsive within 10 s"
            )

        return {"status": "launched"}
