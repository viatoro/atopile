# Autolayout panel (frontend)

The autolayout panel is the standalone webview that drives an AI
placement/routing run for the currently selected build target. It
opens in its own editor column and triggers the layout viewer to open
beside it for previewing candidates.

## Layout

```
panel-autolayout/
├── main.tsx          — App + render() (subscriptions, RPC actions, top-level wiring)
├── PhaseSection.tsx  — one phase's UI (Run/Cancel, pipeline, candidates, advanced, history)
├── CandidateCard.tsx — recommended/applied/active card
├── PreCheckList.tsx  — small readiness-checks list
├── StepPipeline.tsx  — chevron progress indicator
├── helpers.ts        — pure formatters + job/candidate helpers (no React, easy to test)
└── autolayout.css    — styles (uses --ctp-* palette vars, no hardcoded hex)
```

The split rule is: anything that's pure presentation derivation goes in
`helpers.ts`; small reusable visual components get their own file;
`PhaseSection` keeps its local UI state (toggles, pending action,
viewing-history) co-located with the JSX it drives; `main.tsx` is just
wiring + the two `PhaseSection` instances.

## Building & installing the extension

After editing this panel (or any extension/webview code) run from the
repo's open-atopile venv:

```bash
.venv/bin/ato dev compile && .venv/bin/ato dev install vscode
```

`ato dev compile` rebuilds the webview bundles and re-packages the
`.vsix`. `ato dev install vscode` installs it into your local VS Code.
You still need to **reload the VS Code window** (`Developer: Reload
Window`) for the new extension to be picked up.

If `ato dev install vscode` fails with `FileNotFoundError: 'code'`,
your `code` CLI isn't on PATH. In a remote VS Code session it lives
at `~/.vscode-server/cli/servers/Stable-*/server/bin/remote-cli/code`
— add the latest one to PATH first.

## Architecture

### Statelessness

This panel is intentionally **as stateless as possible**. The backend
`AutolayoutService` (`src/atopile/autolayout/service.py`) owns *all*
domain state — jobs, candidates, preview lifecycle, preflight,
recommendations, readiness pre-checks. The panel:

1. Subscribes to one store key: `autolayoutData` (a
   `UiAutolayoutData`).
2. Renders that data.
3. Sends actions back to the backend: `submitAutolayoutJob`,
   `previewAutolayoutCandidate`, `applyAutolayoutCandidate`,
   `cancelAutolayoutJob`, etc.

All derivations the UI used to do — best candidate, routed-pct,
display state, pre-checks — are now backend-pushed. If you need a new
displayed value, **add it to the wire model** rather than computing
it in this file.

| Wire field | Owner | Replaces frontend logic |
| --- | --- | --- |
| `job.displayState` | `_job_display_state` in websocket.py | enum collapse |
| `job.recommendedCandidateId` | `svc.recommended_candidate_id(job)` | best-candidate scan |
| `candidate.routedPct` / `viaCount` | `_candidate_to_ui` | metadata digging |
| `data.placementReadiness` / `routingReadiness` | `svc.placement_readiness()` / `routing_readiness()` | pre-check derivation |

### What state IS local

Only true UI state lives in `useState` (all inside `PhaseSection`):

- `processingMinutes` — pending form-input value before submit (RPC-on-keystroke would be silly)
- `showAdvanced` / `showHistory` / `showAllCandidates` — disclosure toggles
- `viewingHistoryJobId` — which past job the user is inspecting (viewer-side navigation, not a domain concept)
- `pendingAction` — which button was just clicked, for optimistic spinner feedback
- `errorDismissedJobId` — which failed job's banner the user explicitly closed

Two subtleties worth calling out:

- `pendingAction` is **cleared from wire-confirmed state**: `"run"`
  clears when a non-idle job appears, `"preview-<cid>"` clears when
  `previewJobId`/`previewCandidateId` match on the wire, `"apply-<cid>"`
  clears when `appliedCandidateId === cid`. It cannot outlive the
  action that set it.
- The failure banner is **sticky, not auto-dismissed**. Derived from
  `state === "failed" && errorDismissedJobId !== latestJob.jobId` —
  so if a new job fails, its banner re-surfaces automatically because
  the dismissed id won't match.

### Panel sizing

The panel opens in `ViewColumn.Beside` (a regular editor column, not
a sidebar view). Editor-column webviews can't be sized via the
webview API, so `webviewHost.ts` calls `vscode.setEditorLayout` once
on initial open to give the autolayout column ~30% of the editor area
and the layout viewer ~70%. Subsequent reveals don't re-resize so a
manual user resize is preserved.

If you want a different default ratio, edit `_narrowSplitRatio` in
`src/vscode-atopile/src/webviewHost.ts`.

### Lifecycle on open

1. User opens the panel from the sidebar tools.
2. Panel mounts; `useEffect` calls `getAutolayoutData` (which
   `_sync_selected_layout`s on the backend so the layout viewer is on
   the project file, not a stale preview).
3. Once `getAutolayoutData` resolves, the panel calls
   `vscode.openPanel(panel-layout)` and sets the layout-panel title
   to the build target name.
4. `getAutolayoutPreflight` is called whenever the project/target
   changes; subsequent recomputes happen automatically on the
   backend (layout listener) and arrive via the store subscription.
5. When a job completes, the backend auto-previews the recommended
   candidate (no frontend action needed).

## Common gotchas

- **Don't add new derivation here.** If you find yourself writing
  `if (a > b) ...` over `metadata`, the backend should be doing it.
- **Don't add new `useState` without justification.** The list above
  is the vetted set. Any new local state should be a form input, a
  disclosure toggle, or viewer-side navigation — if it's anything
  else, it probably belongs on the backend or derived from the wire.
- **CSS palette**: use `--ctp-*` vars for status colors, not hardcoded
  hex. Status badges and pre-check icons must match other panels.
- **Container queries**: `@container (max-width: ...)` rules at the
  bottom of `autolayout.css` progressively hide pieces (recommended
  badge, stat units, candidate stats) as the column gets narrower.
  Pick a default split ratio that doesn't permanently trigger them.
- **The `timeoutMinutes` wire field is still called that.** The UI
  label is "Processing time" (it's what you're paying for), but the
  action payload field stays `timeoutMinutes` because DeepPCB calls
  it a timeout. Rename is UI-only.
- **No pre-existing TS errors are autolayout's**: `tsc --noEmit` in
  `src/ui/webview` reports errors in `protocol/types.ts` and
  `panel-pcb-diff/main.tsx` — those are unrelated. Make sure your
  changes don't add new ones in `panel-autolayout`.
