export interface LayoutViewerShellElements {
  root: HTMLElement;
  canvas: HTMLCanvasElement;
  initialLoadingEl: HTMLElement;
  layerPanelEl: HTMLElement;
  statusEl: HTMLElement;
  coordsEl: HTMLElement;
  busyEl: HTMLElement;
  fpsEl: HTMLElement;
  helpEl: HTMLElement;
}

const SHELL_HTML = `
  <div class="layout-viewer-shell">
    <canvas id="editor-canvas"></canvas>
    <div id="initial-loading" role="status" aria-live="polite" aria-busy="true">
      <div class="initial-loading-content">
        <div class="initial-loading-spinner"></div>
        <div class="initial-loading-message">Loading PCB</div>
        <div class="initial-loading-subtext">Preparing layout editor...</div>
      </div>
    </div>
    <div id="layer-panel"></div>
    <div id="status-left">
      <span id="status-coords"></span>
      <span id="status-busy" aria-hidden="true">
        <span class="status-busy-spinner"></span>
        Syncing...
      </span>
      <span id="status-fps"></span>
    </div>
    <div id="status-right">
      <button id="status-help" type="button" title="Keyboard shortcuts">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
          <circle cx="8" cy="8" r="7" stroke="currentColor" stroke-width="1.2"/>
          <text x="8" y="11.5" text-anchor="middle" fill="currentColor" font-size="10" font-weight="600">?</text>
        </svg>
        Controls
      </button>
    </div>
  </div>
`;

function requireElement<T extends Element>(
  container: ParentNode,
  selector: string,
  expectedType: { new (): T },
): T {
  const element = container.querySelector(selector);
  if (!(element instanceof expectedType)) {
    throw new Error(`Layout viewer shell missing required element: ${selector}`);
  }
  return element;
}

export function setOverlayState(
  shell: LayoutViewerShellElements | null,
  message: string,
  subtext: string,
  options?: {
    isError?: boolean;
    showSpinner?: boolean;
  },
): void {
  const overlay = shell?.initialLoadingEl;
  if (!overlay) {
    return;
  }
  const { isError = false, showSpinner = false } = options ?? {};

  overlay.classList.remove("hidden");
  overlay.classList.toggle("error", isError);
  overlay.setAttribute("aria-busy", showSpinner ? "true" : "false");

  const spinnerEl = overlay.querySelector(".initial-loading-spinner") as HTMLElement | null;
  const messageEl = overlay.querySelector(".initial-loading-message") as HTMLElement | null;
  const subtextEl = overlay.querySelector(".initial-loading-subtext") as HTMLElement | null;
  if (spinnerEl) {
    spinnerEl.hidden = !showSpinner;
  }
  if (messageEl) {
    messageEl.textContent = message;
  }
  if (subtextEl) {
    subtextEl.textContent = subtext;
  }
}

export function ensureLayoutViewerShell(
  host: HTMLElement,
): LayoutViewerShellElements {
  host.replaceChildren();
  host.insertAdjacentHTML("beforeend", SHELL_HTML);

  const root = requireElement(host, ".layout-viewer-shell", HTMLElement);
  return {
    root,
    canvas: requireElement(root, "#editor-canvas", HTMLCanvasElement),
    initialLoadingEl: requireElement(root, "#initial-loading", HTMLElement),
    layerPanelEl: requireElement(root, "#layer-panel", HTMLElement),
    statusEl: requireElement(root, "#status-left", HTMLElement),
    coordsEl: requireElement(root, "#status-coords", HTMLElement),
    busyEl: requireElement(root, "#status-busy", HTMLElement),
    fpsEl: requireElement(root, "#status-fps", HTMLElement),
    helpEl: requireElement(root, "#status-help", HTMLElement),
  };
}
