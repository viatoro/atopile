import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { render } from "../common/render";
import { rpcClient, WebviewRpcClient } from "../common/webviewRpcClient";
import { RpcDiffClient } from "../common/diff/client";
import type { DiffResult, DiffFilterMode, GitCommitInfo, VersionSource, ViewerLabel, AutolayoutCandidateRef } from "../common/diff/types";
import type { UiAutolayoutData } from "../../protocol/generated-types";
import { buildUuidStatusMap, elementId } from "../common/diff/diff_state";
import { GitCompareArrows } from "lucide-react";
import { NoDataMessage } from "../common/components";
import { Spinner } from "../common/components/Spinner";
import { DiffSidebar } from "./components/DiffSidebar";
import { DiffViewerWrapper, type DiffViewerHandle } from "./components/DiffViewerWrapper";
import { DiffLayerPanel, type DiffLayout } from "./components/DiffLayerPanel";
import { ArrowOverlay } from "./components/ArrowOverlay";
import { OverlaySlider } from "./components/OverlaySlider";
import { VersionSelector } from "./components/VersionSelector";
import { useSyncedCamera } from "./hooks/useSyncedCamera";
import { useKeyboardNav } from "./hooks/useKeyboardNav";
import type { RenderModel } from "../common/layout/types";
import { computeBBox } from "../common/layout/painter";
import { BBox, Vec2 } from "../common/layout/math";
import "./styles/pcb-diff.css";

export type LayerDimMode = "normal" | "dim" | "hide";
export type DiffViewMode = "side-by-side" | "overlay-swap" | "overlay-alpha";

/** Collect layer IDs that the selected elements live on */
function getActiveLayers(model: RenderModel, selectedUuids: Set<string>): Set<string> {
    const active = new Set<string>();
    if (selectedUuids.size === 0) return active;

    for (const track of model.tracks) {
        if (track.uuid && selectedUuids.has(track.uuid) && track.layer) {
            active.add(track.layer);
        }
    }
    for (const via of model.vias) {
        if (via.uuid && selectedUuids.has(via.uuid)) {
            for (const l of via.copper_layers) active.add(l);
        }
    }
    for (const fp of model.footprints) {
        if (fp.uuid && selectedUuids.has(fp.uuid)) {
            active.add(fp.layer);
            for (const pad of fp.pads) {
                for (const l of pad.layers) active.add(l);
            }
        }
    }
    for (const drawing of model.drawings) {
        if (drawing.uuid && selectedUuids.has(drawing.uuid) && drawing.layer) {
            active.add(drawing.layer);
        }
    }
    for (const text of model.texts) {
        if (text.uuid && selectedUuids.has(text.uuid) && text.layer) {
            active.add(text.layer);
        }
    }
    for (const zone of model.zones) {
        if (zone.uuid && selectedUuids.has(zone.uuid)) {
            for (const l of zone.layers) {
                active.add(l);
                active.add(`zone:${l}`);
            }
        }
    }
    return active;
}

/** Build per-layer alpha overrides for dim/hide of non-active layers.
 *  Uses a combined active-layer set so both sides dim the same layers. */
function buildAlphaOverrides(
    model: RenderModel,
    activeLayers: Set<string>,
    mode: LayerDimMode,
    dimOpacity: number,
): Map<string, number> {
    if (mode === "normal" || activeLayers.size === 0) return new Map();

    const alpha = mode === "dim" ? dimOpacity : 0.0;
    const overrides = new Map<string, number>();

    for (const layer of model.layers) {
        if (!activeLayers.has(layer.id)) {
            overrides.set(layer.id, alpha);
            overrides.set(`zone:${layer.id}`, alpha);
        }
    }
    return overrides;
}

/** Compute a world-space BBox covering the selected elements in a model. */
function computeSelectionBBox(model: RenderModel, selectedUuids: Set<string>): BBox | null {
    const points: Vec2[] = [];

    for (const t of model.tracks) {
        if (!t.uuid || !selectedUuids.has(t.uuid)) continue;
        points.push(new Vec2(t.start.x, t.start.y));
        points.push(new Vec2(t.end.x, t.end.y));
    }
    for (const v of model.vias) {
        if (!v.uuid || !selectedUuids.has(v.uuid)) continue;
        const r = v.size / 2;
        points.push(new Vec2(v.at.x - r, v.at.y - r));
        points.push(new Vec2(v.at.x + r, v.at.y + r));
    }
    for (const fp of model.footprints) {
        if (!fp.uuid || !selectedUuids.has(fp.uuid)) continue;
        for (const pad of fp.pads) {
            const hw = (pad.size?.w ?? 0) / 2;
            const hh = (pad.size?.h ?? 0) / 2;
            const wx = fp.at.x + pad.at.x;
            const wy = fp.at.y + pad.at.y;
            points.push(new Vec2(wx - hw, wy - hh));
            points.push(new Vec2(wx + hw, wy + hh));
        }
        if (points.length === 0) {
            points.push(new Vec2(fp.at.x, fp.at.y));
        }
    }
    for (const d of model.drawings) {
        if (!d.uuid || !selectedUuids.has(d.uuid)) continue;
        if ("start" in d) points.push(new Vec2(d.start.x, d.start.y));
        if ("end" in d) points.push(new Vec2(d.end.x, d.end.y));
    }
    for (const t of model.texts) {
        if (!t.uuid || !selectedUuids.has(t.uuid)) continue;
        points.push(new Vec2(t.at.x, t.at.y));
    }
    for (const z of model.zones) {
        if (!z.uuid || !selectedUuids.has(z.uuid)) continue;
        for (const p of z.outline) {
            points.push(new Vec2(p.x, p.y));
        }
    }

    if (points.length === 0) return null;
    return BBox.from_points(points);
}

function basename(path: string): string {
    return path.split("/").pop() ?? path;
}

function makeLabel(version: VersionSource): ViewerLabel {
    const fileName = basename(version.filePath);
    if (version.autolayoutRef) {
        const ref = version.autolayoutRef;
        const score = ref.score != null ? ` (${ref.score.toFixed(1)}%)` : "";
        return {
            fileName,
            commitMessage: `${ref.label}${score} — ${ref.jobType}`,
        };
    }
    if (!version.commitInfo) {
        return { fileName, commitMessage: "Local (working copy)" };
    }
    const c = version.commitInfo;
    return {
        fileName,
        commitHash: c.shortHash,
        commitDate: new Date(c.date).toLocaleDateString(),
        commitMessage: c.message,
        authorName: c.authorName,
    };
}


function OverlayLabel({ label, className, opacity }: { label: ViewerLabel; className: string; opacity: number }) {
    return (
        <div className={`pcb-diff-canvas-label ${className}`} style={{ opacity }}>
            <span className="pcb-diff-label-file">{label.fileName}</span>
            {label.commitDate && <span className="pcb-diff-label-date">{label.commitDate}</span>}
            {label.commitHash && <span className="pcb-diff-label-hash">{label.commitHash}</span>}
            {label.commitMessage && <span className="pcb-diff-label-msg">{label.commitMessage}</span>}
            {label.authorName && <span className="pcb-diff-label-author">{label.authorName}</span>}
        </div>
    );
}

function App() {
    const projectState = WebviewRpcClient.useSubscribe("projectState");
    const selectedBuildInProgress = WebviewRpcClient.useSubscribe("selectedBuildInProgress");
    const pcbPath = projectState?.selectedTarget?.pcbPath ?? null;
    const autolayoutData = WebviewRpcClient.useSubscribe("autolayoutData") as UiAutolayoutData | null;
    const autolayoutJobs = autolayoutData?.jobs ?? [];

    const [diffResult, setDiffResult] = useState<DiffResult | null>(null);
    const [loading, setLoading] = useState(false);
    const [loadingStatus, setLoadingStatus] = useState<string>("");
    const [error, setError] = useState<string | null>(null);
    const [filterMode, setFilterMode] = useState<DiffFilterMode>("components");
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [canvasWidth, setCanvasWidth] = useState(0);
    const [cameraVersion, setCameraVersion] = useState(0);
    const [hiddenLayers, setHiddenLayers] = useState<Set<string>>(new Set());
    const [layerDimMode, setLayerDimMode] = useState<LayerDimMode>("dim");
    const [dimOpacity, setDimOpacity] = useState(0.15);
    const [layout, setLayout] = useState<DiffLayout>("horizontal");
    const [viewMode, setViewMode] = useState<DiffViewMode>("overlay-swap");
    const [overlaySlider, setOverlaySlider] = useState(0.5);
    const hiddenLayersInitialized = useRef(false);

    // Git state
    const [gitLog, setGitLog] = useState<GitCommitInfo[]>([]);
    const [versionA, setVersionA] = useState<VersionSource | null>(null);
    const [versionB, setVersionB] = useState<VersionSource | null>(null);

    const handleARef = useRef<DiffViewerHandle | null>(null);
    const handleBRef = useRef<DiffViewerHandle | null>(null);
    const [handleA, setHandleA] = useState<DiffViewerHandle | null>(null);
    const [handleB, setHandleB] = useState<DiffViewerHandle | null>(null);
    const canvasAreaRef = useRef<HTMLDivElement>(null);
    const searchInputRef = useRef<HTMLInputElement>(null);
    const initRef = useRef<string | null>(null);

    const diffClient = useMemo(
        () => (rpcClient ? new RpcDiffClient(rpcClient) : null),
        [],
    );

    const onCameraChange = useCallback(() => {
        setCameraVersion((v) => v + 1);
    }, []);

    useSyncedCamera(handleA, handleB, onCameraChange);

    // Initialize hidden layers from model's default_visible on first load
    useEffect(() => {
        if (!diffResult) {
            hiddenLayersInitialized.current = false;
            return;
        }
        if (hiddenLayersInitialized.current) return;
        hiddenLayersInitialized.current = true;
        const initial = new Set<string>();
        for (const layer of diffResult.model_b.layers) {
            if (!layer.default_visible) {
                initial.add(layer.id);
            }
        }
        if (initial.size > 0) {
            setHiddenLayers(initial);
        }
    }, [diffResult]);

    const { mapA, mapB } = useMemo(
        () => (diffResult ? buildUuidStatusMap(diffResult.elements) : { mapA: new Map(), mapB: new Map() }),
        [diffResult],
    );

    // Convert selectedId → Set<string> of UUIDs for each side
    const { selectedUuidsA, selectedUuidsB } = useMemo(() => {
        const a = new Set<string>();
        const b = new Set<string>();
        if (!selectedId || !diffResult) return { selectedUuidsA: a, selectedUuidsB: b };

        const netMatch = selectedId.match(/^net:(\d+)$/);
        if (netMatch) {
            const netNum = parseInt(netMatch[1]!, 10);
            for (const el of diffResult.elements) {
                if (el.net === netNum) {
                    if (el.uuid_a) a.add(el.uuid_a);
                    if (el.uuid_b) b.add(el.uuid_b);
                }
            }
        } else {
            for (const el of diffResult.elements) {
                const id = elementId(el);
                if (id === selectedId) {
                    if (el.uuid_a) a.add(el.uuid_a);
                    if (el.uuid_b) b.add(el.uuid_b);
                    break;
                }
            }
        }
        return { selectedUuidsA: a, selectedUuidsB: b };
    }, [selectedId, diffResult]);

    // Compute combined active layers from both sides so dim/hide is synced
    const activeLayers = useMemo(() => {
        if (!diffResult) return new Set<string>();
        const a = getActiveLayers(diffResult.model_a, selectedUuidsA);
        const b = getActiveLayers(diffResult.model_b, selectedUuidsB);
        for (const l of b) a.add(l);
        return a;
    }, [diffResult, selectedUuidsA, selectedUuidsB]);

    const layerAlphaOverridesA = useMemo(
        () => diffResult ? buildAlphaOverrides(diffResult.model_a, activeLayers, layerDimMode, dimOpacity) : new Map<string, number>(),
        [diffResult, activeLayers, layerDimMode, dimOpacity],
    );
    const layerAlphaOverridesB = useMemo(
        () => diffResult ? buildAlphaOverrides(diffResult.model_b, activeLayers, layerDimMode, dimOpacity) : new Map<string, number>(),
        [diffResult, activeLayers, layerDimMode, dimOpacity],
    );

    const itemIds = useMemo(() => {
        if (!diffResult) return [];
        return diffResult.elements
            .filter((el) => el.status !== "unchanged")
            .map(elementId);
    }, [diffResult]);

    const handleCenterView = useCallback(() => {
        if (!diffResult || !handleA) return;
        const bbox = computeBBox(diffResult.model_b);
        handleA.camera.bbox = bbox;
        handleA.requestFrame();
        handleA.onCameraChange?.();
    }, [diffResult, handleA]);

    const applyFollowSelected = useCallback(() => {
        if (!handleA) return;
        if (!selectedId || !diffResult) { handleCenterView(); return; }

        const bboxA = computeSelectionBBox(diffResult.model_a, selectedUuidsA);
        const bboxB = computeSelectionBBox(diffResult.model_b, selectedUuidsB);
        const boxes = [bboxA, bboxB].filter((b): b is BBox => b !== null);
        if (boxes.length === 0) { handleCenterView(); return; }

        const merged = BBox.combine(boxes);
        const cam = handleA.camera;
        const { viewport_size } = cam;
        const itemSize = Math.max(merged.w, merged.h, 0.001);
        const targetScreenSize = Math.max(viewport_size.x, viewport_size.y) * 0.05;

        const boardBBox = computeBBox(diffResult.model_b);
        const fitZoom = Math.min(viewport_size.x / boardBBox.w, viewport_size.y / boardBBox.h);
        cam.zoom = Math.min(Math.max(targetScreenSize / itemSize, fitZoom), 400);
        cam.center = merged.center;
        handleA.requestFrame();
        handleA.onCameraChange?.();
    }, [handleA, handleCenterView, selectedId, diffResult, selectedUuidsA, selectedUuidsB]);

    const [followSelected, setFollowSelected] = useState(true);

    useEffect(() => {
        if (!followSelected) return;
        applyFollowSelected();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [followSelected, selectedId]);

    const handleToggleLayer = useCallback((layer: string) => {
        setHiddenLayers((prev) => {
            const next = new Set(prev);
            if (next.has(layer)) next.delete(layer);
            else next.add(layer);
            return next;
        });
    }, []);

    const handleToggleLayers = useCallback((layers: string[], visible: boolean) => {
        setHiddenLayers((prev) => {
            const next = new Set(prev);
            for (const l of layers) {
                if (visible) next.delete(l);
                else next.add(l);
            }
            return next;
        });
    }, []);

    const isOverlay = viewMode !== "side-by-side";

    const paneStyleA: React.CSSProperties | undefined =
        isOverlay && viewMode === "overlay-swap"
            ? { clipPath: `inset(0 0 0 ${overlaySlider * 100}%)` }
            : undefined;

    const paneStyleB: React.CSSProperties | undefined =
        isOverlay && viewMode === "overlay-swap"
            ? { clipPath: `inset(0 ${(1 - overlaySlider) * 100}% 0 0)` }
            : undefined;

    // Blend mode: apply opacity to canvas only so labels remain visible
    const canvasStyleA: React.CSSProperties | undefined =
        viewMode === "overlay-alpha" ? { opacity: 1 - overlaySlider } : undefined;

    const canvasStyleB: React.CSSProperties | undefined =
        viewMode === "overlay-alpha" ? { opacity: overlaySlider } : undefined;

    // Re-center view when switching view modes
    useEffect(() => {
        handleCenterView();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [viewMode, layout]);

    // Re-center view when canvas area resizes (window resize, sidebar collapse, etc.)
    useEffect(() => {
        const el = canvasAreaRef.current;
        if (!el) return;
        let timer: ReturnType<typeof setTimeout>;
        const ro = new ResizeObserver(() => {
            clearTimeout(timer);
            timer = setTimeout(() => handleCenterView(), 100);
        });
        ro.observe(el);
        return () => { ro.disconnect(); clearTimeout(timer); };
    }, [handleCenterView]);

    useKeyboardNav({ items: itemIds, selectedId, onSelect: setSelectedId, searchInputRef });

    // Compute diff from two version sources
    const runDiff = useCallback(async (a: VersionSource, b: VersionSource, force = false) => {
        if (!diffClient) {
            setError("RPC client not available");
            return;
        }
        setLoading(true);
        setLoadingStatus("Computing diff...");
        setError(null);
        setDiffResult(null);
        setSelectedId(null);
        try {
            const result = await diffClient.computeDiff(a.filePath, b.filePath, undefined, force);
            setLoadingStatus("Rendering...");
            setDiffResult(result);
            if (canvasAreaRef.current) {
                setCanvasWidth(canvasAreaRef.current.clientWidth);
            }
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setLoading(false);
            setLoadingStatus("");
        }
    }, [diffClient]);

    const handleBrowseFile = useCallback(async (): Promise<string | undefined> => {
        return rpcClient?.requestAction<string | undefined>("vscode.browseFile", {
            title: "Select .kicad_pcb file",
            filters: { "KiCad PCB": ["kicad_pcb"] },
        });
    }, []);

    const handleReload = () => {
        if (versionA && versionB) {
            void runDiff(versionA, versionB, true);
        }
    };

    // Store-driven diff paths: callers set these before opening the panel.
    const diffPathA = autolayoutData?.diffPathA ?? null;
    const diffPathB = autolayoutData?.diffPathB ?? null;
    const previewJobId = autolayoutData?.previewJobId ?? null;
    const previewCandidateId = autolayoutData?.previewCandidateId ?? null;
    const lastDiffKey = useRef<string | null>(null);

    // Unified load effect: explicit paths take priority over git-diff default.
    // Uses a `cancelled` flag so that when the effect re-runs (e.g. diffPathA
    // arrives after git-diff already started), the stale async work cannot
    // overwrite state set by the newer run.
    useEffect(() => {
        if (!pcbPath || !diffClient) return;

        let cancelled = false;

        // Case 1: Explicit diff paths provided by caller (e.g. autolayout preview)
        if (diffPathA) {
            if (!diffPathB) return; // pathB still loading — wait
            const key = `${diffPathA}:${diffPathB}`;
            if (lastDiffKey.current === key) return; // already showing this diff
            lastDiffKey.current = key;
            initRef.current = pcbPath;

            const loadExplicit = async () => {
                setLoading(true);
                setLoadingStatus("Loading diff...");
                setError(null);
                try {
                    // Fetch git log if we don't have it yet (for version selectors)
                    if (gitLog.length === 0) {
                        const commits = await diffClient.getGitLog(pcbPath);
                        if (cancelled) return;
                        setGitLog(commits);
                    }

                    // Build VersionSource for side A
                    const versionSourceA: VersionSource = {
                        filePath: diffPathA,
                        commitHash: null,
                        commitInfo: null,
                        autolayoutRef: null,
                    };

                    // Build VersionSource for side B with autolayout metadata if available
                    const job = previewJobId ? autolayoutJobs.find((j) => j.jobId === previewJobId) : null;
                    const candidate = job && previewCandidateId
                        ? job.candidates.find((c) => c.candidateId === previewCandidateId)
                        : null;
                    const autolayoutRef: AutolayoutCandidateRef | null =
                        previewJobId && previewCandidateId
                            ? {
                                  jobId: previewJobId,
                                  candidateId: previewCandidateId,
                                  jobType: (job?.jobType ?? "Routing") as "Routing" | "Placement",
                                  label: candidate?.label ?? `Candidate ${previewCandidateId.slice(0, 8)}`,
                                  score: candidate?.score ?? null,
                              }
                            : null;
                    const versionSourceB: VersionSource = {
                        filePath: diffPathB,
                        commitHash: null,
                        commitInfo: null,
                        autolayoutRef,
                    };

                    if (cancelled) return;
                    setVersionA(versionSourceA);
                    setVersionB(versionSourceB);
                    await runDiff(versionSourceA, versionSourceB, true);
                } catch (e) {
                    if (cancelled) return;
                    setError(e instanceof Error ? e.message : String(e));
                    setLoading(false);
                    setLoadingStatus("");
                }
            };
            void loadExplicit();
            return () => { cancelled = true; };
        }

        // Case 2: No explicit paths — default to git diff (once per pcbPath)
        if (initRef.current === pcbPath) return;
        initRef.current = pcbPath;

        const initGitDiff = async () => {
            setLoading(true);
            try {
                setLoadingStatus("Fetching git history...");
                const commits = await diffClient.getGitLog(pcbPath);
                if (cancelled) return;
                setGitLog(commits);

                const localVersion: VersionSource = {
                    filePath: pcbPath,
                    commitHash: null,
                    commitInfo: null,
                    autolayoutRef: null,
                };

                if (commits.length > 0) {
                    const lastCommit = commits[0]!;
                    setLoadingStatus(`Extracting ${lastCommit.shortHash}...`);
                    const tempPath = await diffClient.getFileAtCommit(pcbPath, lastCommit.hash);
                    if (cancelled) return;
                    const commitVersion: VersionSource = {
                        filePath: tempPath,
                        commitHash: lastCommit.hash,
                        commitInfo: lastCommit,
                        autolayoutRef: null,
                    };
                    setVersionA(commitVersion);
                    setVersionB(localVersion);
                    await runDiff(commitVersion, localVersion);
                } else {
                    if (cancelled) return;
                    setVersionA(localVersion);
                    setVersionB(localVersion);
                    await runDiff(localVersion, localVersion);
                }
            } catch (e) {
                if (cancelled) return;
                const msg = e instanceof Error ? e.message : String(e);
                if (msg.includes("File not found")) {
                    setError("No PCB file available, run `build` first");
                } else {
                    setError(msg);
                }
                setLoading(false);
                setLoadingStatus("");
            }
        };
        void initGitDiff();
        return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [diffPathA, diffPathB, pcbPath, diffClient, runDiff]);

    // Re-run diff when versions change (user-initiated)
    const handleVersionAChange = useCallback(async (v: VersionSource) => {
        setVersionA(v);
        if (versionB) {
            await runDiff(v, versionB);
        }
    }, [versionB, runDiff]);

    const handleVersionBChange = useCallback(async (v: VersionSource) => {
        setVersionB(v);
        if (versionA) {
            await runDiff(versionA, v);
        }
    }, [versionA, runDiff]);

    const hasSelection = Boolean(projectState?.selectedProjectRoot && projectState?.selectedTarget);
    const hasDiffData = Boolean(diffResult && versionA && versionB);

    if (!hasSelection || (error && !diffResult) || !hasDiffData) {
        return (
            <NoDataMessage
                icon={<GitCompareArrows size={24} />}
                noun="PCB diff"
                hasSelection={hasSelection}
                isLoading={loading && !diffResult}
                buildInProgress={selectedBuildInProgress}
                error={error && !diffResult ? error : null}
                hasData={hasDiffData}
                noDataDescription="Run a build to generate the PCB layout."
            >
                {null}
            </NoDataMessage>
        );
    }

    const labelA = makeLabel(versionA);
    const labelB = makeLabel(versionB);

    return (
        <div className="pcb-diff-root">
            <DiffSidebar
                result={diffResult}
                filterMode={filterMode}
                onFilterModeChange={setFilterMode}
                selectedId={selectedId}
                onSelectItem={setSelectedId}
                onReload={handleReload}
                loading={loading}
                versionSelector={
                    diffClient ? (
                        <div className="pcb-diff-version-selectors">
                            <VersionSelector
                                label="Old"
                                version={versionA}
                                gitLog={gitLog}
                                pcbPath={pcbPath}
                                diffClient={diffClient}
                                onVersionChange={handleVersionAChange}
                                onBrowseFile={handleBrowseFile}
                                autolayoutJobs={autolayoutJobs}
                            />
                            <VersionSelector
                                label="New"
                                version={versionB}
                                gitLog={gitLog}
                                pcbPath={pcbPath}
                                diffClient={diffClient}
                                onBrowseFile={handleBrowseFile}
                                onVersionChange={handleVersionBChange}
                                autolayoutJobs={autolayoutJobs}
                            />
                        </div>
                    ) : null
                }
            />
            <div
                className={`pcb-diff-canvases${layout === "vertical" ? " vertical" : ""}${isOverlay ? " overlay" : ""}`}
                ref={canvasAreaRef}
            >
                <DiffViewerWrapper
                    model={diffResult.model_a}
                    uuidStatusMap={mapA}
                    filterMode={filterMode}
                    label={labelA}
                    handleRef={handleARef}
                    onHandleReady={setHandleA}
                    selectedUuids={selectedUuidsA}
                    hiddenLayers={hiddenLayers}
                    layerAlphaOverrides={layerAlphaOverridesA}
                    style={paneStyleA}
                    canvasStyle={canvasStyleA}
                    hideLabel={isOverlay}
                />
                {!isOverlay && <div className="pcb-diff-canvas-separator" />}
                <DiffViewerWrapper
                    model={diffResult.model_b}
                    uuidStatusMap={mapB}
                    filterMode={filterMode}
                    label={labelB}
                    handleRef={handleBRef}
                    onHandleReady={setHandleB}
                    selectedUuids={selectedUuidsB}
                    hiddenLayers={hiddenLayers}
                    layerAlphaOverrides={layerAlphaOverridesB}
                    style={paneStyleB}
                    canvasStyle={canvasStyleB}
                    className={isOverlay ? "overlay-pane-b" : undefined}
                    hideLabel={isOverlay}
                    fpsPosition="right"
                />
                {isOverlay && (
                    <>
                        <OverlayLabel label={labelA} className="overlay-label-a" opacity={0.4 + 0.6 * (1 - overlaySlider)} />
                        <OverlayLabel label={labelB} className="overlay-label-b" opacity={0.4 + 0.6 * overlaySlider} />
                    </>
                )}
                <DiffLayerPanel
                    model={diffResult.model_b}
                    hiddenLayers={hiddenLayers}
                    onToggleLayer={handleToggleLayer}
                    onToggleLayers={handleToggleLayers}
                    layerDimMode={layerDimMode}
                    onLayerDimModeChange={setLayerDimMode}
                    dimOpacity={dimOpacity}
                    onDimOpacityChange={setDimOpacity}
                    onCenterView={handleCenterView}
                    followSelected={followSelected}
                    onToggleFollowSelected={() => setFollowSelected((v) => !v)}
                    layout={layout}
                    onLayoutChange={setLayout}
                    viewMode={viewMode}
                    onViewModeChange={setViewMode}
                />
                {viewMode === "side-by-side" && (
                    <ArrowOverlay
                        elements={diffResult.elements}
                        selectedId={selectedId}
                        handleA={handleA}
                        handleB={handleB}
                        containerWidth={canvasWidth}
                        cameraVersion={cameraVersion}
                    />
                )}
                {isOverlay && (
                    <OverlaySlider
                        value={overlaySlider}
                        onChange={setOverlaySlider}
                        mode={viewMode as "overlay-swap" | "overlay-alpha"}
                    />
                )}
            </div>
        </div>
    );
}

render(App);
