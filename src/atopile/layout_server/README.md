# PCB Layout Editor

Interactive PCB layout viewer/editor with a WebGL2 renderer. Loads `.kicad_pcb` files via faebryk's parser, renders board edges, tracks, vias, zones, and footprints (pads + silkscreen + text properties such as Reference/Value), and supports drag-to-move and rotate editing with undo/redo.

This is the standalone browser server for direct debugging. The VS Code layout
panel uses the core RPC websocket in `src/atopile/server/ui/websocket.py`
instead of this HTTP app.

The standalone page also speaks the same layout RPC protocol now:
- `action=getLayoutRenderModel`
- `action=executeLayoutAction`
- `action=subscribeLayout` / `unsubscribeLayout`
- `action_result` responses plus `layout_updated` / `layout_delta` push messages

## Build & Run

```bash
# 1. Build the frontend
cd src/atopile/layout_server/frontend
bun install
bun run build

# 2. Start the server
.venv/bin/python -m atopile.layout_server path/to/board.kicad_pcb

# 3. Open http://localhost:8100
```

## Controls

| Input | Action |
|---|---|
| Scroll wheel | Zoom |
| Middle-click drag | Pan |
| Left-click | Select footprint |
| Left-drag | Move selected footprint |
| R | Rotate selected footprint 90° |
| F | Flip selected footprint (front/back) |
| Ctrl+Z | Undo |
| Ctrl+Shift+Z / Ctrl+Y | Redo |

## Tests

```bash
.venv/bin/python -m pytest test/layout_server/ -v
```
