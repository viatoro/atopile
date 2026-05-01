import { useEffect, useRef } from "react";
import { Renderer } from "../../common/layout/webgl/renderer";
import { Camera2 } from "../../common/layout/camera";
import { PanAndZoom } from "../../common/layout/pan-and-zoom";
import { RenderLoop } from "../../common/layout/render_loop";
import { computeBBox } from "../../common/layout/painter";
import { paintDiffBoard, paintDiffSelection } from "../../common/diff/diff_painter";
import type { RenderModel } from "../../common/layout/types";
import type { DiffFilterMode, DiffStatus, ViewerLabel } from "../../common/diff/types";
import { Vec2 } from "../../common/layout/math";

export interface DiffViewerHandle {
    camera: Camera2;
    requestFrame: () => void;
    onCameraChange?: () => void;
}

interface DiffViewerWrapperProps {
    model: RenderModel;
    uuidStatusMap: Map<string, DiffStatus>;
    filterMode: DiffFilterMode;
    label: ViewerLabel;
    handleRef?: React.MutableRefObject<DiffViewerHandle | null>;
    onHandleReady?: (handle: DiffViewerHandle | null) => void;
    selectedUuids?: Set<string>;
    hiddenLayers?: Set<string>;
    layerAlphaOverrides?: Map<string, number>;
    style?: React.CSSProperties;
    canvasStyle?: React.CSSProperties;
    className?: string;
    hideLabel?: boolean;
    fpsPosition?: "left" | "right";
}

export function DiffViewerWrapper({
    model,
    uuidStatusMap,
    filterMode,
    label,
    handleRef,
    onHandleReady,
    selectedUuids,
    hiddenLayers,
    layerAlphaOverrides,
    style,
    canvasStyle,
    className,
    hideLabel,
    fpsPosition = "left",
}: DiffViewerWrapperProps) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const fpsRef = useRef<HTMLSpanElement>(null);
    const handleRefLatest = useRef(handleRef);
    handleRefLatest.current = handleRef;
    const onHandleReadyLatest = useRef(onHandleReady);
    onHandleReadyLatest.current = onHandleReady;
    const stateRef = useRef<{
        renderer: Renderer;
        camera: Camera2;
        uuidStatusMap: Map<string, DiffStatus>;
        filterMode: DiffFilterMode;
        selectedUuids: Set<string>;
        hiddenLayers: Set<string>;
        layerAlphaOverrides: Map<string, number>;
        needsRepaint: boolean;
        needsRedraw: boolean;
        needsSelectionRepaint: boolean;
        fitted: boolean;
        selectionStartTime: number;
    } | null>(null);

    // Initialize renderer when model changes
    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;

        const renderer = new Renderer(canvas);
        renderer.setup();
        const camera = new Camera2();

        const state = {
            renderer,
            camera,
            uuidStatusMap,
            filterMode,
            selectedUuids: selectedUuids ?? new Set<string>(),
            hiddenLayers: hiddenLayers ?? new Set<string>(),
            layerAlphaOverrides: layerAlphaOverrides ?? new Map<string, number>(),
            needsRepaint: true,
            needsRedraw: true,
            needsSelectionRepaint: true,
            fitted: false,
            selectionStartTime: 0,
        };

        const handle: DiffViewerHandle = {
            camera,
            requestFrame: () => { state.needsRedraw = true; },
            onCameraChange: undefined,
        };

        const panAndZoom = new PanAndZoom(canvas, camera, () => {
            state.needsRedraw = true;
            handle.onCameraChange?.();
        });

        const bbox = computeBBox(model);

        let fpsFrames = 0;
        let fpsLastTime = performance.now();

        const renderLoop = new RenderLoop(() => {
            // FPS counter
            fpsFrames++;
            const fpsNow = performance.now();
            if (fpsNow - fpsLastTime >= 1000) {
                if (fpsRef.current) {
                    fpsRef.current.textContent = `${fpsFrames} fps`;
                }
                fpsFrames = 0;
                fpsLastTime = fpsNow;
            }
            // Update camera viewport from current canvas CSS size
            const rect = canvas.getBoundingClientRect();
            const w = rect.width || canvas.clientWidth || 0;
            const h = rect.height || canvas.clientHeight || 0;
            if (w <= 0 || h <= 0) return; // Canvas not laid out yet
            camera.viewport_size = new Vec2(w, h);

            // Fit to view on first frame with a real viewport
            if (!state.fitted) {
                state.fitted = true;
                camera.bbox = bbox;
                state.needsRepaint = true;
            }

            // Repaint geometry when status map / filter / hidden layers change
            if (state.needsRepaint) {
                state.needsRepaint = false;
                paintDiffBoard(
                    renderer,
                    model,
                    state.uuidStatusMap,
                    state.hiddenLayers,
                    state.filterMode,
                );
                state.needsRedraw = true;
                // Board repaint disposes dynamic layers, so rebuild selection
                state.needsSelectionRepaint = true;
            }

            // Rebuild hatch overlay when selection changes
            if (state.needsSelectionRepaint) {
                state.needsSelectionRepaint = false;
                paintDiffSelection(renderer, model, state.selectedUuids, state.hiddenLayers, state.uuidStatusMap);
                state.selectionStartTime = performance.now() / 1000;
                state.needsRedraw = true;
            }

            // Always redraw when hatch or glow is active (animation)
            const hasHatch = renderer.has_hatch_layers;
            const hasGlow = renderer.has_glow_layers;
            const now = performance.now() / 1000;
            const glowTime = now - state.selectionStartTime;
            const glowActive = hasGlow && glowTime < 2.5;
            if (!state.needsRedraw && !hasHatch && !glowActive) return;
            state.needsRedraw = false;

            // Update grid dots for visible area
            renderer.updateGrid(camera.bbox, 1.0, camera.zoom);

            renderer.draw(camera.matrix, state.layerAlphaOverrides);

            // Draw hatch overlay on top
            if (hasHatch) {
                renderer.draw_hatch_layers(
                    camera.matrix,
                    1.0,        // alpha
                    now * 2.0,  // time (scrolls 2 stripe/sec)
                    1.0,        // spacing in mm
                    0.65,       // stripe width ratio
                );
            }

            // Draw glow overlay (fades out after ~3 seconds)
            if (glowActive) {
                renderer.draw_glow_layers(
                    camera.matrix,
                    1.0,
                    glowTime,
                );
            }
        });

        stateRef.current = state;
        if (handleRefLatest.current) handleRefLatest.current.current = handle;
        onHandleReadyLatest.current?.(handle);

        renderLoop.start();

        return () => {
            renderLoop.stop();
            stateRef.current = null;
            if (handleRefLatest.current) handleRefLatest.current.current = null;
            onHandleReadyLatest.current?.(null);
        };
    }, [model]);

    // Update when status map or filter changes
    useEffect(() => {
        if (!stateRef.current) return;
        stateRef.current.uuidStatusMap = uuidStatusMap;
        stateRef.current.filterMode = filterMode;
        stateRef.current.needsRepaint = true;
    }, [uuidStatusMap, filterMode]);

    // Update when selection changes
    useEffect(() => {
        if (!stateRef.current) return;
        stateRef.current.selectedUuids = selectedUuids ?? new Set<string>();
        stateRef.current.needsSelectionRepaint = true;
        stateRef.current.selectionStartTime = performance.now() / 1000;
    }, [selectedUuids]);

    // Update when hidden layers change
    useEffect(() => {
        if (!stateRef.current) return;
        stateRef.current.hiddenLayers = hiddenLayers ?? new Set<string>();
        stateRef.current.needsRepaint = true;
    }, [hiddenLayers]);

    // Update when layer alpha overrides change
    useEffect(() => {
        if (!stateRef.current) return;
        stateRef.current.layerAlphaOverrides = layerAlphaOverrides ?? new Map<string, number>();
        stateRef.current.needsRedraw = true;
    }, [layerAlphaOverrides]);

    // Reset fitted flag on container resize so camera re-fits
    useEffect(() => {
        const canvas = canvasRef.current;
        const pane = canvas?.parentElement;
        if (!pane) return;
        let timer: ReturnType<typeof setTimeout>;
        const ro = new ResizeObserver(() => {
            clearTimeout(timer);
            timer = setTimeout(() => {
                if (stateRef.current) {
                    stateRef.current.fitted = false;
                }
            }, 100);
        });
        ro.observe(pane);
        return () => { ro.disconnect(); clearTimeout(timer); };
    }, [model]);

    return (
        <div className={"pcb-diff-canvas-pane" + (className ? " " + className : "")} style={style}>
            {!hideLabel && (
                <div className="pcb-diff-canvas-label">
                    <span className="pcb-diff-label-file">{label.fileName}</span>
                    {label.commitDate && <span className="pcb-diff-label-date">{label.commitDate}</span>}
                    {label.commitHash && <span className="pcb-diff-label-hash">{label.commitHash}</span>}
                    {label.commitMessage && <span className="pcb-diff-label-msg">{label.commitMessage}</span>}
                    {label.authorName && <span className="pcb-diff-label-author">{label.authorName}</span>}
                </div>
            )}
            <canvas ref={canvasRef} className="pcb-diff-canvas" style={canvasStyle} />
            <span ref={fpsRef} className={`pcb-diff-fps${fpsPosition === "right" ? " pcb-diff-fps-right" : ""}`} />
        </div>
    );
}
