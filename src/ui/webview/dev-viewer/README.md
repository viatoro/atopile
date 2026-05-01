# Sidebar Dev Viewer

Standalone browser-based viewer for the atopile sidebar, bypassing VS Code.
Useful for rapid UI iteration — gives you Vite HMR and works over Tailscale.

Vite proxies the WebSocket to the core server, so remote access works
without any extra tools or binding the core server to 0.0.0.0.

## Quick start

```bash
./devctl up
```

This starts the core server and Vite, then prints local + Tailscale URLs.

## Manual start

**1. Core server** (terminal 1):

```bash
cd apps/open-atopile
ATOPILE_CORE_SERVER_PORT=18730 uv run ato serve core
```

**2. Vite dev server** (terminal 2):

```bash
cd apps/open-atopile/src/ui/webview/dev-viewer
bunx vite
```

Open http://localhost:5199 (or your Tailscale IP on the same port).

## Query params

| Param | Default | Description |
|---|---|---|
| `project` | `.` | Path for project discovery (relative to core server CWD) |
