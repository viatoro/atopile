import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { render } from "../common/render";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import { createWebviewLogger } from "../common/logger";
import {
  getLayoutFootprintId,
  getLayoutPadId,
  mountLayoutViewer,
  StaticLayoutClient,
  type LayoutViewerHandle,
  type PadDecoration,
  type RenderModel,
  type Color,
} from "../common/layout";
import { Cpu } from "lucide-react";
import {
  Badge,
  EmptyState,
  NoDataMessage,
  PanelSearchBox,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../common/components";
import type {
  PinoutLead,
  PinSignalType,
} from "../../protocol/generated-types";
import "../common/components/LayoutPreview.css";
import "./main.css";

const logger = createWebviewLogger("PanelPinout");

type ConnectionFilter = "all" | "connected" | "unconnected";
type SortKey = "padNumbers" | "leadDesignator" | "signalType" | "interfaces" | "netName" | "isConnected";
type SortDirection = "asc" | "desc";
type SelectOption<T extends string> = { label: string; value: T };

const PINOUT_MIN_TABLE_WIDTH = 280;
const PINOUT_MIN_PREVIEW_WIDTH = 320;
const PINOUT_SPLITTER_WIDTH = 14;
const PINOUT_SIGNAL_PREVIEW_COLORS: Record<PinSignalType, Color> = {
  logic: [137 / 255, 180 / 255, 250 / 255, 0.92],
  signal: [166 / 255, 227 / 255, 161 / 255, 0.92],
  power: [243 / 255, 139 / 255, 168 / 255, 0.94],
  nc: [127 / 255, 132 / 255, 156 / 255, 0.82],
};
const PINOUT_HIGHLIGHT_COLOR: Color = [249 / 255, 80 / 255, 21 / 255, 0.98];
const CONNECTION_ITEMS: SelectOption<ConnectionFilter>[] = [
  { label: "All leads", value: "all" },
  { label: "Connected", value: "connected" },
  { label: "Unconnected", value: "unconnected" },
];
const SORTABLE_COLUMNS: { className: string; key: SortKey; label: string }[] = [
  { className: "pinout-column-pads", key: "padNumbers", label: "Pad Number" },
  { className: "pinout-column-signal", key: "leadDesignator", label: "Lead Designator" },
  { className: "pinout-column-type", key: "signalType", label: "Type" },
  { className: "pinout-column-interfaces", key: "interfaces", label: "Interfaces" },
  { className: "pinout-column-net", key: "netName", label: "Net Name" },
  { className: "pinout-column-connected", key: "isConnected", label: "Connected" },
];

function clampTablePaneWidth(nextWidth: number, containerWidth: number): number {
  const maxWidth = Math.max(
    PINOUT_MIN_TABLE_WIDTH,
    containerWidth - PINOUT_MIN_PREVIEW_WIDTH - PINOUT_SPLITTER_WIDTH,
  );
  return Math.max(PINOUT_MIN_TABLE_WIDTH, Math.min(nextWidth, maxWidth));
}

/**
 * Builds a minimal RenderModel containing only the selected footprint UUID
 */
function buildFootprintPreviewModel(
  model: RenderModel,
  footprintUuid: string,
): RenderModel | null {
  const footprint = model.footprints.find((candidate) => candidate.uuid === footprintUuid);
  if (!footprint) {
    return null;
  }
  return {
    board: {
      edges: [],
      width: 0,
      height: 0,
      origin: { x: 0, y: 0 },
    },
    layers: model.layers,
    drawings: [],
    texts: [],
    footprints: [footprint],
    footprint_groups: [],
    tracks: [],
    vias: [],
    zones: [],
  };
}

function getSignalTypeBadgeVariant(signalType: PinSignalType) {
  switch (signalType) {
    case "signal":
      return "success";
    case "logic":
      return "info";
    case "power":
      return "destructive";
    case "nc":
      return "secondary";
  }
}

function getSignalTypeLabel(signalType: PinSignalType): string {
  switch (signalType) {
    case "signal":
      return "Signal";
    case "logic":
      return "Logic";
    case "power":
      return "Power";
    case "nc":
      return "NC";
  }
}

function getLeadKey(lead: PinoutLead): string {
  return JSON.stringify([lead.padNumbers, lead.leadDesignator, lead.netName]);
}

function PinoutPreview({
  model,
  leads,
  hoveredLeadId,
  onPadHover,
}: {
  model: RenderModel;
  leads: PinoutLead[];
  hoveredLeadId: string | null;
  onPadHover: (padId: string | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const loadingRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<LayoutViewerHandle | null>(null);
  const clientRef = useRef<StaticLayoutClient | null>(null);

  /**
   * Mounts the read-only footprint viewer once and disposes it on unmount.
   */
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }

    clientRef.current = new StaticLayoutClient(model);
    viewerRef.current = mountLayoutViewer({
      canvas,
      client: clientRef.current,
      readOnly: true,
      initialLoadingEl: loadingRef.current,
      layerPanelEl: null,
      statusEl: null,
      coordsEl: null,
      busyEl: null,
      fpsEl: null,
      helpEl: null,
      logger,
    });

    return () => {
      clientRef.current = null;
      viewerRef.current?.dispose();
      viewerRef.current = null;
    };
  }, []);

  /**
   * Replaces the current preview scene without tearing down the viewer instance.
   * Triggers when the selected footprint model changes.
   */
  useEffect(() => {
    clientRef.current?.setRenderModel(model);
    viewerRef.current?.editor.setRenderModel(model, true);
  }, [model]);

  /**
   * Applies pad decorations and hover wiring for the current lead set in the mounted viewer.
   * Triggers when the hovered lead, lead data, footprint selection, or hover callback changes.
   */
  useEffect(() => {
    const editor = viewerRef.current?.editor;
    const footprint = model.footprints[0];
    if (!editor || !footprint) {
      return;
    }

    const footprintId = getLayoutFootprintId(footprint, 0);
    const padDecorations = new Map<string, PadDecoration>();
    for (const lead of leads) {
      for (const padNumber of lead.padNumbers) {
        padDecorations.set(getLayoutPadId(footprintId, padNumber), {
          color: PINOUT_SIGNAL_PREVIEW_COLORS[lead.signalType],
          highlightColor: PINOUT_HIGHLIGHT_COLOR,
          outlined: !lead.isConnected,
          highlighted: getLeadKey(lead) === hoveredLeadId,
        });
      }
    }
    editor.setDecorations({ pads: padDecorations });
    editor.setOnPadHover(onPadHover);

    return () => {
      editor.setOnPadHover(null);
    };
  }, [hoveredLeadId, leads, model.footprints, onPadHover]);

  return (
    <div className="layout-preview pinout-preview">
      <div className="layout-preview__shell">
        <canvas ref={canvasRef} className="layout-preview__canvas" />
        <div ref={loadingRef} className="layout-preview__loading" aria-busy="true">
          <div className="initial-loading-content">
            <div className="initial-loading-spinner" />
            <div className="initial-loading-message">Loading preview</div>
            <div className="initial-loading-subtext">Preparing focused footprint view...</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function App() {
  const projectState = WebviewRpcClient.useSubscribe("projectState");
  const selectedBuildInProgress = WebviewRpcClient.useSubscribe("selectedBuildInProgress");
  const layoutData = WebviewRpcClient.useSubscribe("layoutData");
  const pinoutData = WebviewRpcClient.useSubscribe("pinoutData");

  const [selectedComponentIndex, setSelectedComponentIndex] = useState(0);
  const [search, setSearch] = useState("");
  const [signalTypeFilter, setSignalTypeFilter] = useState<PinSignalType | "all">("all");
  const [connectionFilter, setConnectionFilter] = useState<ConnectionFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("padNumbers");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
  const [hoveredLeadId, setHoveredLeadId] = useState<string | null>(null);
  const [layoutModel, setLayoutModel] = useState<RenderModel | null>(null);
  const [isLayoutModelLoading, setIsLayoutModelLoading] = useState(false);
  const [layoutModelError, setLayoutModelError] = useState<string | null>(null);
  const [tablePaneWidth, setTablePaneWidth] = useState<number | null>(null);
  const [isResizing, setIsResizing] = useState(false);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const tableWrapRef = useRef<HTMLDivElement | null>(null);
  const resizeRef = useRef<{ startX: number; startWidth: number } | null>(null);

  const hasSelection = Boolean(projectState.selectedProjectRoot && projectState.selectedTarget);

  /**
   * Resets local pinout UI state when the selected project or build target changes.
   * Triggers on changes to the selected project root or selected target name.
   */
  useEffect(() => {
    setSelectedComponentIndex(0);
    setSearch("");
    setSignalTypeFilter("all");
    setConnectionFilter("all");
    setSortKey("padNumbers");
    setSortDirection("asc");
    setHoveredLeadId(null);
    setTablePaneWidth(null);
  }, [projectState.selectedProjectRoot, projectState.selectedTarget?.name]);

  /**
   * Loads the latest layout render model used for footprint previews.
   * Triggers when the layout path or layout revision changes and cancels stale async results on cleanup.
   */
  useEffect(() => {
    let cancelled = false;
    const layoutPath = layoutData.path;
    const resetLayoutModel = (error: string | null) => {
      setLayoutModel(null);
      setIsLayoutModelLoading(false);
      setLayoutModelError(error);
    };

    if (!layoutPath || !rpcClient) {
      resetLayoutModel(layoutPath ? "RPC client is unavailable." : null);
      return;
    }

    setLayoutModel(null);
    setIsLayoutModelLoading(true);
    setLayoutModelError(null);

    void rpcClient.requestAction<RenderModel>("getLayoutRenderModel")
      .then((model) => {
        if (!cancelled) {
          setLayoutModel(model);
          setIsLayoutModelLoading(false);
        }
      })
      .catch((error) => {
        if (cancelled) {
          return;
        }
        const message = error instanceof Error ? error.message : String(error);
        logger.error(`Failed to load layout render model for pinout preview: ${message}`);
        resetLayoutModel(message);
      });
    return () => {
      cancelled = true;
    };
  }, [layoutData.path, layoutData.revision, rpcClient]);

  const componentCount = pinoutData.components.length;
  const selectedIndex = componentCount ? Math.min(selectedComponentIndex, componentCount - 1) : 0;
  const component = componentCount ? pinoutData.components[selectedIndex] : null;
  const leads = component?.leads ?? [];
  const signalTypes = [...new Set(leads.map((lead) => lead.signalType))].sort((left, right) => left.localeCompare(right));
  const componentItems = pinoutData.components.map((entry) => ({
    label: entry.descriptor,
    value: entry.atoAddress,
  }));
  const signalTypeItems = [
    { label: "All types", value: "all" },
    ...signalTypes.map((signalType) => ({
      label: getSignalTypeLabel(signalType),
      value: signalType,
    })),
  ];
  const previewModel = useMemo(
    () => (
      layoutModel && component?.footprintUuid
        ? buildFootprintPreviewModel(layoutModel, component.footprintUuid)
        : null
    ),
    [layoutModel, component?.footprintUuid],
  );
  const previewFootprint = previewModel?.footprints[0];
  const contentStyle = tablePaneWidth === null
    ? undefined
    : ({ "--pinout-table-width": `${tablePaneWidth}px` } as CSSProperties);

  useEffect(() => {
    const content = contentRef.current;
    if (!content || !component?.footprintUuid) {
      return;
    }

    const resizeObserver = new ResizeObserver(() => {
      setTablePaneWidth((current) => (
        current === null ? current : clampTablePaneWidth(current, content.clientWidth)
      ));
    });
    resizeObserver.observe(content);
    return () => {
      resizeObserver.disconnect();
    };
  }, [component?.footprintUuid]);

  const filteredLeads = (() => {
    const query = search.trim().toLowerCase();
    return leads.filter((lead) => {
      if (signalTypeFilter !== "all" && lead.signalType !== signalTypeFilter) {
        return false;
      }
      if (connectionFilter === "connected" && !lead.isConnected) {
        return false;
      }
      if (connectionFilter === "unconnected" && lead.isConnected) {
        return false;
      }
      if (!query) {
        return true;
      }
      return (
        lead.leadDesignator.toLowerCase().includes(query)
        || lead.padNumbers.some((value) => value.toLowerCase().includes(query))
        || lead.signalType.toLowerCase().includes(query)
        || getSignalTypeLabel(lead.signalType).toLowerCase().includes(query)
        || (lead.isConnected ? "connected" : "unconnected").includes(query)
        || lead.netName?.toLowerCase().includes(query)
        || lead.interfaces.some((value) => value.toLowerCase().includes(query))
      );
    }).sort((left, right) => {
      const leftValue = (
        sortKey === "padNumbers" ? left.padNumbers.join(",")
          : sortKey === "interfaces" ? left.interfaces.join(",")
            : sortKey === "netName" ? (left.netName ?? "")
              : sortKey === "isConnected" ? (left.isConnected ? "connected" : "unconnected")
              : left[sortKey]
      );
      const rightValue = (
        sortKey === "padNumbers" ? right.padNumbers.join(",")
          : sortKey === "interfaces" ? right.interfaces.join(",")
            : sortKey === "netName" ? (right.netName ?? "")
              : sortKey === "isConnected" ? (right.isConnected ? "connected" : "unconnected")
              : right[sortKey]
      );
      const result = leftValue.localeCompare(rightValue, undefined, {
        numeric: true,
        sensitivity: "base",
      });
      return sortDirection === "asc" ? result : -result;
    });
  })();
  function toggleSort(nextSortKey: SortKey) {
    if (nextSortKey === sortKey) {
      setSortDirection((current) => (current === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(nextSortKey);
    setSortDirection("asc");
  }

  function endTableResize(element?: HTMLDivElement | null, pointerId?: number) {
    if (element && pointerId !== undefined && element.hasPointerCapture(pointerId)) {
      element.releasePointerCapture(pointerId);
    }
    resizeRef.current = null;
    setIsResizing(false);
  }

  function onTableResizePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    const tableWrap = tableWrapRef.current;
    if (!tableWrap) {
      return;
    }

    event.preventDefault();
    resizeRef.current = {
      startX: event.clientX,
      startWidth: tableWrap.getBoundingClientRect().width,
    };
    setIsResizing(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function onTableResizePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const state = resizeRef.current;
    const content = contentRef.current;
    if (!state || !content) {
      return;
    }

    setTablePaneWidth(
      clampTablePaneWidth(state.startWidth + event.clientX - state.startX, content.clientWidth),
    );
  }

  function onTableResizePointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    endTableResize(event.currentTarget, event.pointerId);
  }

  function onTableResizePointerCancel(event: ReactPointerEvent<HTMLDivElement>) {
    endTableResize(event.currentTarget, event.pointerId);
  }

  if (!hasSelection || !pinoutData.components.length || pinoutData.error) {
    return (
      <div className="pinout-panel">
        <NoDataMessage
          icon={<Cpu size={24} />}
          noun="pinout"
          hasSelection={hasSelection}
          buildInProgress={selectedBuildInProgress}
          error={pinoutData.error}
          hasData={pinoutData.components.length > 0}
        >
          {null}
        </NoDataMessage>
      </div>
    );
  }

  if (!component) {
    return (
      <div className="pinout-panel">
        <EmptyState title="Pinout unavailable" description="No pinout component is currently selected." />
      </div>
    );
  }

  const previewMessage = layoutData.error
    ? String(layoutData.error)
    : layoutModelError
      ? layoutModelError
      : isLayoutModelLoading
        ? "Loading footprint preview..."
        : !layoutData.path
          ? selectedBuildInProgress
            ? "Build in progress. Footprint data will appear when the build completes."
            : "Run a build to generate footprint data."
          : previewModel
            ? null
            : "Footprint not found in the current layout.";

  return (
    <div className="pinout-panel">
      <div className="pinout-header">
        <div className="pinout-header-main">
          <h1 className="pinout-title">Pinout Viewer</h1>
          {componentCount > 1 ? (
            <div className="pinout-artifact-picker">
              <span className="pinout-artifact-label">Component</span>
              <Select
                className="pinout-select-root pinout-component-select"
                items={componentItems}
                value={component?.atoAddress ?? null}
                onValueChange={(value) => {
                  if (!value) {
                    return;
                  }
                  const nextIndex = pinoutData.components.findIndex((entry) => entry.atoAddress === value);
                  setSelectedComponentIndex(nextIndex >= 0 ? nextIndex : 0);
                }}
              >
                <SelectTrigger className="pinout-select-trigger">
                  <SelectValue placeholder="Select component" />
                </SelectTrigger>
                <SelectContent>
                  {componentItems.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}
        </div>
        {component.warnings.length ? (
          <div className="pinout-warnings">
            {component.warnings.map((warning) => (
              <Badge key={warning} variant="secondary" className="pinout-badge pinout-warning-chip">
                {warning}
              </Badge>
            ))}
          </div>
        ) : null}
      </div>

      <div className="pinout-filters">
        <div className="pinout-search-box">
          <PanelSearchBox
            value={search}
            onChange={setSearch}
            placeholder="Search leads, pads, interfaces, nets..."
          />
        </div>
        <Select
          className="pinout-select-root"
          items={signalTypeItems}
          value={signalTypeFilter}
          onValueChange={(value) => {
            if (value) {
              setSignalTypeFilter(value as PinSignalType | "all");
            }
          }}
        >
          <SelectTrigger className="pinout-select-trigger">
            <SelectValue placeholder="Signal type" />
          </SelectTrigger>
          <SelectContent>
            {signalTypeItems.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          className="pinout-select-root"
          items={CONNECTION_ITEMS}
          value={connectionFilter}
          onValueChange={(value) => {
            if (value) {
              setConnectionFilter(value as ConnectionFilter);
            }
          }}
        >
          <SelectTrigger className="pinout-select-trigger">
            <SelectValue placeholder="Connection" />
          </SelectTrigger>
          <SelectContent>
            {CONNECTION_ITEMS.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="pinout-count">
          {filteredLeads.length} of {leads.length} leads
        </span>
      </div>

      <div
        ref={contentRef}
        className={isResizing ? "pinout-content is-resizing" : "pinout-content"}
        style={contentStyle}
      >
        <div
          ref={tableWrapRef}
          className={component.footprintUuid ? "pinout-table-wrap has-footprint" : "pinout-table-wrap full-width"}
        >
          <Table className="pinout-table">
            <colgroup>
              <col className="pinout-col pinout-col-pads" />
              <col className="pinout-col pinout-col-signal" />
              <col className="pinout-col pinout-col-type" />
              <col className="pinout-col pinout-col-interfaces" />
              <col className="pinout-col pinout-col-net" />
              <col className="pinout-col pinout-col-connected" />
            </colgroup>
            <TableHeader className="pinout-table-header">
              <TableRow className="pinout-head-row">
                {SORTABLE_COLUMNS.map((column) => (
                  <TableHead
                    key={column.key}
                    className={`pinout-sort-header ${column.className}`}
                    aria-sort={sortKey === column.key ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}
                  >
                    <button
                      type="button"
                      className="pinout-sort"
                      data-active={sortKey === column.key ? "true" : undefined}
                      onClick={() => toggleSort(column.key)}
                    >
                      <span>{column.label}</span>
                      <span className="pinout-sort-indicator" aria-hidden="true">
                        {sortKey === column.key ? (sortDirection === "asc" ? "↑" : "↓") : "↕"}
                      </span>
                    </button>
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredLeads.map((lead) => {
                const leadKey = getLeadKey(lead);
                const isHovered = leadKey === hoveredLeadId;
                const padNumbers = lead.padNumbers.join(", ");

                return (
                  <TableRow
                    key={leadKey}
                    className={isHovered ? "pinout-row is-hovered" : "pinout-row"}
                    onMouseEnter={() => setHoveredLeadId(leadKey)}
                    onMouseLeave={() => setHoveredLeadId((current) => (
                      current === leadKey ? null : current
                    ))}
                  >
                    <TableCell className="pinout-cell pinout-cell-nowrap">{padNumbers || "-"}</TableCell>
                    <TableCell className="pinout-cell pinout-cell-nowrap">{lead.leadDesignator}</TableCell>
                    <TableCell className="pinout-cell pinout-cell-nowrap">
                      <Badge
                        variant={getSignalTypeBadgeVariant(lead.signalType)}
                        className="pinout-badge pinout-signal-badge"
                        data-signal-type={lead.signalType}
                      >
                        {getSignalTypeLabel(lead.signalType)}
                      </Badge>
                    </TableCell>
                    <TableCell className="pinout-cell">
                      {lead.interfaces.length ? (
                        <div className="pinout-tag-list">
                          {lead.interfaces.map((value) => (
                            <Badge key={value} variant="secondary" className="pinout-badge pinout-interface-tag">
                              {value}
                            </Badge>
                          ))}
                        </div>
                      ) : (
                        <span className="pinout-placeholder">-</span>
                      )}
                    </TableCell>
                    <TableCell className="pinout-cell pinout-cell-nowrap">
                      {lead.netName ?? <span className="pinout-placeholder">-</span>}
                    </TableCell>
                    <TableCell className="pinout-cell pinout-cell-nowrap">
                      {lead.isConnected ? null : (
                        <Badge
                          variant="secondary"
                          className="pinout-badge pinout-note-tag-warning"
                        >
                          Unconnected
                        </Badge>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
          {!filteredLeads.length ? (
            <div className="pinout-no-results">
              <EmptyState title="No leads match filters" description="Adjust search or filter settings." />
            </div>
          ) : null}
        </div>

        {component.footprintUuid ? (
          <>
            <div
              className="pinout-splitter"
              aria-label="Resize pinout list and footprint preview"
              title="Drag to resize. Double-click to reset."
              onPointerDown={onTableResizePointerDown}
              onPointerMove={onTableResizePointerMove}
              onPointerUp={onTableResizePointerUp}
              onPointerCancel={onTableResizePointerCancel}
              onDoubleClick={() => setTablePaneWidth(null)}
            />
            <div className="pinout-footprint">
              {previewMessage ? (
                <div className="pinout-footprint-empty">
                  <EmptyState title="No footprint data" description={previewMessage} />
                </div>
              ) : (
                <PinoutPreview
                  model={previewModel!}
                  leads={leads}
                  hoveredLeadId={hoveredLeadId}
                  onPadHover={(padId) => {
                    if (!padId || !previewFootprint) {
                      setHoveredLeadId(null);
                      return;
                    }

                    const footprintId = getLayoutFootprintId(previewFootprint, 0);
                    for (const lead of leads) {
                      for (const padNumber of lead.padNumbers) {
                        if (getLayoutPadId(footprintId, padNumber) === padId) {
                          setHoveredLeadId(getLeadKey(lead));
                          return;
                        }
                      }
                    }
                    setHoveredLeadId(null);
                  }}
                />
              )}
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}

render(App);
