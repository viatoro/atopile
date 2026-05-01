# Autolayout (backend)

The autolayout module owns everything related to AI-driven PCB
placement and routing for the open-atopile app: job lifecycle, provider
communication, candidate management, preview/apply, preflight metric
computation, and **all UI-facing state**.

The websocket layer (`atopile/server/ui/websocket.py`) is a thin
dispatcher — it translates incoming actions into method calls on the
service and adapts service models to wire models. It does **not** hold
autolayout state.

## Layout

```
autolayout/
├── __init__.py         — public re-exports (AutolayoutService, etc.)
├── service.py          — AutolayoutService: orchestrator, composes the below
├── models.py           — AutolayoutJob / AutolayoutCandidate / AutolayoutState
│                         + PreCheckItem / PreviewResult
├── ui_state.py         — AutolayoutUIState: loading/error/preview/preflight
│                         flags (composed as `service.ui`)
├── job_runner.py       — JobRunner: background submit + poll threads
├── job_store.py        — JSON persistence (persist/load)
├── client_factory.py   — DeepPCBClientProvider: token-aware client cache
├── eda_convert.py      — KiCad ↔ HL ↔ DeepPCB conversion + apply-diff
├── readiness.py        — pure preflight → pre-check list
├── status_mapping.py   — DeepPCB board status + revision → candidate
├── preflight.py        — board metrics computed from a KicadPcb
├── hl_diff.py          — HL-level diff used to apply only what changed
└── deeppcb/            — typed client for the DeepPCB provider API
```

`service.py` is intentionally a thin orchestrator: it holds the jobs
dict + lock, and delegates to the sibling modules above. The split is
by concern (UI state vs. persistence vs. provider plumbing vs. EDA
format plumbing), not by layer.

> **Note — stage/swap-to-HL branch:** `eda_convert.py` will be
> overhauled when the stage/swap-to-HL branch merges. At that point
> the HL model becomes the primary in-memory representation of the
> PCB, and much of the load/save plumbing here (HL ↔ KiCad
> round-tripping, fingerprint-based diff application) moves into the
> HL layer itself. Treat the current shape as transitional.

## Architecture

### State ownership

`AutolayoutService` is the single source of truth for autolayout state.
External callers (currently only the websocket dispatcher) **must** read
state via the service's `@property` accessors and mutate it through
explicit methods. Direct attribute writes from outside are not allowed.

Public mutators (each notifies observers atomically):

| Method | Purpose |
| --- | --- |
| `begin_loading` / `end_loading(error=None)` | data fetch lifecycle |
| `begin_submitting` / `end_submitting(error=None)` | job submit lifecycle |
| `set_error` / `clear_error` | sticky error display |
| `set_project_root(root)` | switch active project; clears stale preview |
| `start_job(...)` | submit a new job (returns immediately, polls in bg) |
| `refresh_job(job_id)` | poll provider for status |
| `select_candidate(job_id, cid)` | mark a candidate selected (no preview) |
| `begin_preview(job_id, cid)` | convert + cache + mark as previewing |
| `end_preview()` | clear preview state |
| `auto_preview_best(job_id)` | pick best by routed-pct, then `begin_preview` |
| `apply_candidate(job_id, cid?)` | write the chosen candidate to the .kicad_pcb |
| `cancel_job(job_id)` | cancel a running job |
| `compute_and_store_preflight(...)` | compute board metrics |

(Credit balance lives on the shared `authGetBalance` action used by the
sidebar header — the autolayout service doesn't duplicate it.)

### Ownership & observer pattern

There is no singleton. `CoreSocket` constructs one `AutolayoutService`
in its `__init__` and passes the instance to anyone who needs it
(e.g. `DiffRpcSession`):

```python
self._autolayout_service = AutolayoutService(
    layout_service=layout_service,
    on_state_changed=self._on_autolayout_state_changed,
    on_job_completed=self._on_autolayout_job_completed,
)
```

All three kwargs are optional so tests can construct the service
standalone.

Every mutator above fires `on_state_changed` (either directly via
`_notify()` on the service, or indirectly through `AutolayoutUIState`
when the mutator touches UI state), which the websocket converts into
a `_push_autolayout_store()`. Adding new state fields to the wire
format is therefore a 4-step change:

1. Add the read-only `@property` + mutator on `AutolayoutUIState`
   (`ui_state.py`), and a pass-through property on `AutolayoutService`
   so the websocket can still read `svc.<field>` directly.
2. Add the corresponding `Ui*` field in `atopile/data_models.py`.
3. Regenerate types: `python src/atopile/generate_types.py`.
4. Read the new property in the websocket's `_push_autolayout_store`.

### Layout-viewer integration

Passing `layout_service=...` to `AutolayoutService(...)` (or calling
`attach_layout_service` on a standalone instance) wires the service to
the shared `LayoutService` so it can:

- Auto-recompute preflight metrics whenever the layout changes (skipped
  during a candidate preview because the preview file is not the real
  project layout).
- Own preview lifecycle state.

The websocket layer still owns the layout-viewer side effect — i.e.
calling `layout_service.open(path)` and broadcasting `layout_updated`.
The split rule is: the service owns *autolayout* state; the websocket
owns *UI-shell* side effects (which file is shown in which editor
column, what's read-only, etc.).

### Persistence

Jobs are persisted to `~/.atopile/autolayout/jobs.json` via
`job_store.persist` / `job_store.load`. On startup the service loads
the jobs dict and hands any non-terminal job with a `provider_job_ref`
back to `JobRunner.resume(...)` so polling picks up where it left off.
Backups of overwritten layouts land next to the original as
`*.kicad_pcb.bak.<jobid8>` (max `MAX_BACKUP_FILES` per layout, oldest
evicted).

### DeepPCB client

The provider client lives in `deeppcb/`. The service talks to it
directly — there is no provider-abstraction layer. `DeepPCBClientProvider`
(`client_factory.py`) wraps the client with transparent token-refresh
handling: callers use `provider.require()` and get back a client built
against the current gateway token (rebuilt if the token rotated).

If you need a piece of board data the typed `BoardWithRevisionsDto`
doesn't expose, prefer `client.get_board_raw(board_id)` (returns the
raw dict) over reaching into `client._client.get(...)`.

### Best-candidate policy

`recommended_candidate_id(job)` is the single source of truth for
"which candidate to recommend". It picks the candidate with the highest
routed-air-wires percentage, falling back to the first candidate. The
frontend never re-derives this — it reads `recommendedCandidateId` from
the wire model.

## Testing

```bash
ato dev test
```

Just the DeepPCB unit tests:

```bash
.venv/bin/pytest src/atopile/autolayout/deeppcb/tests/ -q
```

Note: `test_client.py` requires `respx` (HTTP mock); other tests do
not.

## Common gotchas

- **One instance per server**: `CoreSocket` constructs the service
  once in its `__init__` and hands the same instance to
  `DiffRpcSession`. Don't add module-level globals or lazy factories
  back in — if a new consumer appears, wire it through constructor
  injection.
- **Background threads**: `start_job` returns immediately; submission
  and polling happen on a daemon thread owned by `JobRunner`.
  Callbacks fired from those threads (via `_notify` →
  `on_state_changed`) need to be marshalled to the event loop on the
  websocket side (`loop.call_soon_threadsafe`).
- **Preview state vs layout viewer state**: the service tracks
  *which candidate* is being previewed and *where the artifact lives*;
  the websocket layer is responsible for actually swapping the layout
  viewer to that path and toggling read-only. Don't try to do the
  swap from the service.
- **PCB apply diff**: `eda_convert.save_hl` (→ `_save_hl_to_kicad`)
  applies an HL-level diff on top of the original .kicad_pcb so
  format-specific metadata the HL model doesn't carry (zone fill
  settings, custom layers, etc.) is preserved. Placement jobs ignore
  routing changes from the provider and vice versa — anything outside
  the job's scope is logged as a warning and dropped. See the
  stage/swap-to-HL note above: this plumbing is expected to move into
  the HL layer when that branch merges.
