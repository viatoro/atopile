"""JSON persistence for autolayout jobs.

Plain module-level functions that operate on the service's jobs dict
and a state path. Kept out of the service class so the concern —
serialize/deserialize + disk I/O — stands on its own.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from atopile.autolayout.models import AutolayoutJob

log = logging.getLogger(__name__)

MAX_JOBS = 200


def persist(state_path: Path, jobs: dict[str, AutolayoutJob]) -> None:
    """Write jobs to disk, keeping at most ``MAX_JOBS`` most-recent entries.

    Caller must hold any lock guarding the ``jobs`` dict.
    """
    state_path.parent.mkdir(parents=True, exist_ok=True)
    data = {jid: job.model_dump(mode="json") for jid, job in jobs.items()}
    if len(data) > MAX_JOBS:
        sorted_ids = sorted(
            data, key=lambda k: data[k].get("created_at", ""), reverse=True
        )
        data = {k: data[k] for k in sorted_ids[:MAX_JOBS]}
    state_path.write_text(json.dumps(data, indent=2))


def load(state_path: Path) -> dict[str, AutolayoutJob]:
    """Read jobs from disk. Returns an empty dict if the file is missing
    or corrupt."""
    if not state_path.exists():
        return {}
    jobs: dict[str, AutolayoutJob] = {}
    try:
        raw = json.loads(state_path.read_text())
        for jid, jdata in raw.items():
            if not jdata.get("layout_path"):
                log.warning("Skipping job %s with no layout_path", jid)
                continue
            jdata.pop("phase", None)  # removed field, strip for backward compat
            if jdata.get("message") is None:
                jdata["message"] = ""
            jobs[jid] = AutolayoutJob.model_validate(jdata)
    except Exception:
        log.exception("Failed to load autolayout jobs from %s", state_path)
        return {}
    return jobs
