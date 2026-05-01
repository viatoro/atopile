"""DeepPCB board-status → internal state mapping and revision parsing.

Pure provider-shaped helpers. Kept here (rather than under
``deeppcb/``) because the outputs are our internal models; the inputs
are raw JSON dicts returned from the DeepPCB client.
"""

from __future__ import annotations

from typing import Any

from atopile.autolayout.models import AutolayoutCandidate, AutolayoutState


def map_board_status(status: Any) -> AutolayoutState:
    """Map a DeepPCB BoardStatus to our internal state."""
    s = str(status) if status else ""
    if s in ("Done",):
        return AutolayoutState.COMPLETED
    if s in ("Failed",):
        return AutolayoutState.FAILED
    if s in ("Stopped", "StopRequested", "StopFailed"):
        return AutolayoutState.CANCELLED
    if s in ("Running", "Starting", "ReceivingRevisions"):
        return AutolayoutState.RUNNING
    if s in ("Pending",):
        return AutolayoutState.QUEUED
    return AutolayoutState.RUNNING


_STATUS_LABELS: dict[str, str] = {
    "Pending": "Pending",
    "Starting": "Starting",
    "Running": "Running",
    "ReceivingRevisions": "Receiving",
    "Done": "Completed",
    "Failed": "Failed",
    "Stopped": "Cancelled",
    "StopRequested": "Cancelling",
    "StopFailed": "Cancelled",
}


def friendly_status(board_status: str) -> str:
    return _STATUS_LABELS.get(board_status, board_status)


def revisions_to_candidates(revisions: list[Any]) -> list[AutolayoutCandidate]:
    """Convert DeepPCB board revisions (raw JSON dicts) to candidates."""
    candidates = []
    for rev in revisions:
        rev_id = rev.get("id")
        rev_num = rev.get("revisionNumber")
        result = rev.get("result") or {}

        if not rev_id:
            continue

        metadata: dict[str, Any] = {"revision_number": rev_num}
        score: float | None = None

        connected = result.get("airWiresConnected")
        total = result.get("totalAirWires")
        nets = result.get("netsConnected")
        wire_len = result.get("totalWireLength")
        vias = result.get("viaAdded")

        if connected is not None and total and total > 0:
            score = round(connected / total * 100, 1)

        metadata.update(
            {
                "airWiresConnected": connected,
                "airWiresNotConnected": result.get("airWiresNotConnected"),
                "totalAirWires": total,
                "netsConnected": nets,
                "totalWireLength": wire_len,
                "viaAdded": vias,
                "totalComponents": result.get("totalComponents"),
                "placedComponents": result.get("placedComponents"),
                "committed": rev.get("isConvergenceRevision"),
                "creditsBurned": rev.get("creditsBurned"),
                "generatedOn": rev.get("generatedOn"),
            }
        )

        candidates.append(
            AutolayoutCandidate(
                candidate_id=str(rev_id),
                label=f"Revision {rev_num}" if rev_num is not None else None,
                score=score,
                metadata=metadata,
            )
        )

    return candidates
