import { Editor } from "./editor";
import {
    type LayoutClientLogger,
    type LayoutTransport,
} from "./layout_client";

export interface LayoutViewerMountOptions {
    canvas: HTMLCanvasElement;
    client: LayoutTransport;
    readOnly?: boolean;
    logger?: LayoutClientLogger;
    initialLoadingEl?: HTMLElement | null;
    layerPanelEl?: HTMLElement | null;
    statusEl?: HTMLElement | null;
    coordsEl?: HTMLElement | null;
    busyEl?: HTMLElement | null;
    fpsEl?: HTMLElement | null;
    helpEl?: HTMLElement | null;
}

export interface LayoutViewerHandle {
    editor: Editor;
    dispose(): void;
}

const CONTROLS: [string, string][] = [
    ["Scroll", "Zoom"],
    ["Middle-click", "Pan"],
    ["Click", "Group / select"],
    ["Shift+drag", "Box-select"],
    ["Double-click", "Select single"],
    ["Esc", "Clear selection"],
    ["R", "Rotate"],
    ["F", "Flip"],
    ["Ctrl+Z", "Undo"],
    ["Ctrl+Shift+Z", "Redo"],
];
const READ_ONLY_CONTROLS: [string, string][] = [
    ["Scroll", "Zoom"],
    ["Middle-click", "Pan"],
];

function setLoadingState(
    initialLoadingEl: HTMLElement | null | undefined,
    message: string,
    subtext: string,
    isError = false,
): void {
    if (!initialLoadingEl) return;
    const messageEl = initialLoadingEl.querySelector(".initial-loading-message") as HTMLElement | null;
    const subtextEl = initialLoadingEl.querySelector(".initial-loading-subtext") as HTMLElement | null;
    initialLoadingEl.classList.remove("hidden");
    initialLoadingEl.classList.toggle("error", isError);
    initialLoadingEl.setAttribute("aria-busy", isError ? "false" : "true");
    if (messageEl) messageEl.textContent = message;
    if (subtextEl) subtextEl.textContent = subtext;
}

function hideLoadingState(initialLoadingEl: HTMLElement | null | undefined): void {
    if (!initialLoadingEl) return;
    initialLoadingEl.classList.add("hidden");
    initialLoadingEl.classList.remove("error");
    initialLoadingEl.setAttribute("aria-busy", "false");
}

function formatErrorDetail(error: unknown): string {
    if (error instanceof Error) {
        return error.stack || error.message || String(error);
    }
    return String(error);
}

// FRAPS-style 7-segment digits: stamp axis-aligned rects per segment and use
// an SVG dilate filter to paint a single black outline around the union, so
// overlapping segments don't leak interior seams.
const FRAPS_DIGIT_WIDTH = 30;
const FRAPS_DIGIT_HEIGHT = 48;
const FRAPS_DIGIT_GAP = 8;
const FRAPS_STROKE = 6;

type Segment = "top" | "mid" | "bot" | "tl" | "tr" | "bl" | "br";
type Rect = readonly [number, number, number, number];

const FRAPS_SEGMENTS: Readonly<Record<Segment, Rect>> = (() => {
    const w = FRAPS_DIGIT_WIDTH;
    const h = FRAPS_DIGIT_HEIGHT;
    const t = FRAPS_STROKE;
    const halfH = (h + t) / 2;
    const midY = (h - t) / 2;
    return {
        top: [0, 0, w, t],
        mid: [0, midY, w, t],
        bot: [0, h - t, w, t],
        tl: [0, 0, t, halfH],
        tr: [w - t, 0, t, halfH],
        bl: [0, h - halfH, t, halfH],
        br: [w - t, h - halfH, t, halfH],
    };
})();

const FRAPS_DIGIT_SEGMENTS: Readonly<Record<string, ReadonlyArray<Segment>>> = {
    "0": ["top", "bot", "tl", "tr", "bl", "br"],
    "1": ["tr", "br"],
    "2": ["top", "tr", "mid", "bl", "bot"],
    "3": ["top", "tr", "mid", "br", "bot"],
    "4": ["tl", "mid", "tr", "br"],
    "5": ["top", "tl", "mid", "br", "bot"],
    "6": ["top", "tl", "mid", "bl", "br", "bot"],
    "7": ["top", "tr", "br"],
    "8": ["top", "tl", "tr", "mid", "bl", "br", "bot"],
    "9": ["top", "tl", "tr", "mid", "br", "bot"],
};

function renderFrapsDigits(value: number): string {
    const str = String(Math.max(0, Math.floor(value)));
    const totalWidth = str.length * FRAPS_DIGIT_WIDTH
        + Math.max(0, str.length - 1) * FRAPS_DIGIT_GAP;
    let shapes = "";
    for (let i = 0; i < str.length; i += 1) {
        const ox = i * (FRAPS_DIGIT_WIDTH + FRAPS_DIGIT_GAP);
        const segments = FRAPS_DIGIT_SEGMENTS[str[i]] ?? [];
        for (const name of segments) {
            const [x, y, w, h] = FRAPS_SEGMENTS[name];
            shapes += `<rect x="${x + ox}" y="${y}" width="${w}" height="${h}"/>`;
        }
    }
    return `<svg viewBox="-2 -2 ${totalWidth + 4} ${FRAPS_DIGIT_HEIGHT + 4}" `
        + `preserveAspectRatio="xMaxYMid meet" xmlns="http://www.w3.org/2000/svg">`
        + `<defs><filter id="fraps-outline" x="-15%" y="-15%" `
        + `width="130%" height="130%">`
        + `<feMorphology in="SourceAlpha" operator="dilate" radius="3.5" `
        + `result="d"/>`
        + `<feFlood flood-color="#000" result="b"/>`
        + `<feComposite in="b" in2="d" operator="in" result="o"/>`
        + `<feMerge><feMergeNode in="o"/><feMergeNode in="SourceGraphic"/>`
        + `</feMerge></filter></defs>`
        + `<g filter="url(#fraps-outline)">${shapes}</g></svg>`;
}

export function mountLayoutViewer(options: LayoutViewerMountOptions): LayoutViewerHandle {
    const {
        canvas,
        client,
        readOnly = false,
        logger,
        initialLoadingEl = document.getElementById("initial-loading"),
        statusEl = document.getElementById("status-left"),
        coordsEl = document.getElementById("status-coords"),
        busyEl = document.getElementById("status-busy"),
        fpsEl = document.getElementById("status-fps"),
        helpEl = document.getElementById("status-help"),
    } = options;

    logger?.info?.("Initializing layout viewer");

    const editor = new Editor(canvas, client, { readOnly, logger });
    let disposed = false;
    let fpsAnimationId: number | null = null;
    const cleanupFns: Array<() => void> = [];

    function attachGridSettings(): void {
        if (!statusEl) return;
        const gridSizes = [0.05, 0.1, 0.2, 0.25, 0.5, 1, 1.27, 2.54];
        let gridSize: number | null = 0.25;
        let settingsPopup: HTMLElement | null = null;

        const settingsBtn = document.createElement("span");
        settingsBtn.id = "settings-btn";
        settingsBtn.title = "Grid settings";
        settingsBtn.textContent = "\u2699";

        editor.setSnapDelta((dx, dy) => {
            if (gridSize === null) return { dx, dy };
            return {
                dx: Math.round(dx / gridSize) * gridSize,
                dy: Math.round(dy / gridSize) * gridSize,
            };
        });

        statusEl.insertBefore(settingsBtn, statusEl.firstChild);
        cleanupFns.push(() => settingsBtn.remove());

        settingsBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            if (settingsPopup) {
                settingsPopup.remove();
                settingsPopup = null;
                return;
            }

            settingsPopup = document.createElement("div");
            settingsPopup.className = "settings-popup";
            const label = document.createElement("div");
            label.className = "settings-label-title";
            label.textContent = "Grid snap";
            settingsPopup.appendChild(label);

            const btnRow = document.createElement("div");
            btnRow.className = "settings-source-buttons";
            const allOptions: { label: string; value: number | null }[] = [
                { label: "OFF", value: null },
                ...gridSizes.map((s) => ({ label: String(s), value: s })),
            ];
            const buttons: HTMLButtonElement[] = [];
            for (const opt of allOptions) {
                const btn = document.createElement("button");
                btn.className = "source-btn";
                if (gridSize === opt.value) btn.classList.add("active");
                btn.textContent = opt.label;
                btn.addEventListener("click", () => {
                    gridSize = opt.value;
                    for (const b of buttons) b.classList.remove("active");
                    btn.classList.add("active");
                });
                buttons.push(btn);
                btnRow.appendChild(btn);
            }

            settingsPopup.appendChild(btnRow);
            document.body.appendChild(settingsPopup);

            const removeListeners = () => {
                document.removeEventListener("mousedown", closeOnOutside);
                document.removeEventListener("keydown", closeOnEsc);
            };
            const closeOnOutside = (ev: MouseEvent) => {
                if (
                    settingsPopup
                    && !settingsPopup.contains(ev.target as Node)
                    && ev.target !== settingsBtn
                    && !settingsBtn.contains(ev.target as Node)
                ) {
                    settingsPopup.remove();
                    settingsPopup = null;
                    removeListeners();
                }
            };
            const closeOnEsc = (ev: KeyboardEvent) => {
                if (ev.key === "Escape" && settingsPopup) {
                    settingsPopup.remove();
                    settingsPopup = null;
                    removeListeners();
                }
            };
            document.addEventListener("mousedown", closeOnOutside);
            document.addEventListener("keydown", closeOnEsc);
        });
    }

    function attachStatusBar(): void {
        if (helpEl) {
            const controls = readOnly ? READ_ONLY_CONTROLS : CONTROLS;
            let popover: HTMLElement | null = null;

            const closePopover = () => {
                if (popover) {
                    popover.remove();
                    popover = null;
                }
            };

            helpEl.addEventListener("click", (e) => {
                e.stopPropagation();
                if (popover) { closePopover(); return; }

                popover = document.createElement("div");
                popover.className = "controls-popover";
                const table = document.createElement("table");
                for (const [key, desc] of controls) {
                    const row = table.insertRow();
                    row.insertCell().textContent = key;
                    row.insertCell().textContent = desc;
                }
                popover.appendChild(table);
                document.body.appendChild(popover);

                const onClickOutside = (ev: MouseEvent) => {
                    if (popover && !popover.contains(ev.target as Node) && ev.target !== helpEl && !helpEl!.contains(ev.target as Node)) {
                        closePopover();
                        document.removeEventListener("mousedown", onClickOutside);
                        document.removeEventListener("keydown", onEsc);
                    }
                };
                const onEsc = (ev: KeyboardEvent) => {
                    if (ev.key === "Escape") {
                        closePopover();
                        document.removeEventListener("mousedown", onClickOutside);
                        document.removeEventListener("keydown", onEsc);
                    }
                };
                document.addEventListener("mousedown", onClickOutside);
                document.addEventListener("keydown", onEsc);
            });

            cleanupFns.push(() => closePopover());
        }

        const onMouseEnter = () => {
            if (coordsEl) coordsEl.dataset.hover = "1";
        };
        const onMouseLeave = () => {
            if (coordsEl) {
                delete coordsEl.dataset.hover;
                coordsEl.textContent = "";
            }
        };

        canvas.addEventListener("mouseenter", onMouseEnter);
        canvas.addEventListener("mouseleave", onMouseLeave);
        cleanupFns.push(() => {
            canvas.removeEventListener("mouseenter", onMouseEnter);
            canvas.removeEventListener("mouseleave", onMouseLeave);
        });

        editor.setOnMouseMove((x, y) => {
            if (coordsEl && coordsEl.dataset.hover) {
                coordsEl.textContent = `X: ${x.toFixed(2)}  Y: ${y.toFixed(2)}`;
            }
        });
        editor.setOnActionBusyChanged((busy) => {
            if (!busyEl) return;
            busyEl.classList.toggle("visible", busy);
            busyEl.setAttribute("aria-hidden", busy ? "false" : "true");
        });
    }

    let lastFps = 0;
    const renderFps = (): void => {
        if (!fpsEl) return;
        if (fpsEl.classList.contains("fraps-mode")) {
            fpsEl.innerHTML = renderFrapsDigits(lastFps);
        } else {
            fpsEl.textContent = `${lastFps} fps`;
        }
    };

    function startFpsCounter(): void {
        if (!fpsEl) return;
        let frames = 0;
        let lastTime = performance.now();
        const tick = () => {
            if (disposed) return;
            frames += 1;
            const now = performance.now();
            if (now - lastTime >= 1000) {
                lastFps = frames;
                renderFps();
                frames = 0;
                lastTime = now;
            }
            fpsAnimationId = requestAnimationFrame(tick);
        };
        fpsAnimationId = requestAnimationFrame(tick);
    }

    function setupKonamiCode(): void {
        if (!fpsEl) return;
        const sequence = [
            "ArrowUp", "ArrowUp",
            "ArrowDown", "ArrowDown",
            "ArrowLeft", "ArrowRight",
            "ArrowLeft", "ArrowRight",
            "b", "a",
        ];
        const matches = (step: string, key: string): boolean =>
            step.length === 1 ? key.toLowerCase() === step : key === step;
        let index = 0;
        const onKeyDown = (e: KeyboardEvent) => {
            if (matches(sequence[index], e.key)) {
                index += 1;
                if (index === sequence.length) {
                    fpsEl.classList.toggle("fraps-mode");
                    renderFps();
                    index = 0;
                }
            } else {
                index = matches(sequence[0], e.key) ? 1 : 0;
            }
        };
        window.addEventListener("keydown", onKeyDown);
        cleanupFns.push(() => window.removeEventListener("keydown", onKeyDown));
    }

    if (!readOnly) {
        attachGridSettings();
    }
    attachStatusBar();
    startFpsCounter();
    setupKonamiCode();

    setLoadingState(initialLoadingEl, "Loading PCB", "Building scene geometry...");
    void editor.init().then(() => {
        if (disposed) return;
        logger?.info?.("Layout viewer initialized successfully.");
        hideLoadingState(initialLoadingEl);
    }).catch((err) => {
        if (disposed) return;
        logger?.error?.(`Failed to initialize editor: ${formatErrorDetail(err)}`);
        setLoadingState(initialLoadingEl, "Load failed", "Could not initialize PCB viewer.", true);
    });

    return {
        editor,
        dispose() {
            if (disposed) return;
            disposed = true;
            if (fpsAnimationId !== null) {
                cancelAnimationFrame(fpsAnimationId);
            }
            editor.setOnLayersChanged(() => {});
            editor.setOnMouseMove(() => {});
            editor.setOnActionBusyChanged(() => {});
            for (const cleanup of cleanupFns) {
                cleanup();
            }
            editor.dispose();
        },
    };
}
