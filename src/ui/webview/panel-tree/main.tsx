import { useEffect, useMemo, useRef, useState } from "react";
import { GitBranch } from "lucide-react";
import type { ResolvedBuildTarget } from "../../protocol/generated-types";
import { samePath } from "../../protocol/paths";
import { NoDataMessage } from "../common/components";
import { createWebviewLogger } from "../common/logger";
import { render } from "../common/render";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import type {
  TreeBounds,
  TreeEdge,
  TreeEdgeStyle,
  TreeGroup,
  TreeLayout,
  TreeMetaEntry,
  TreeNode,
  TreeNodePosition,
  TreeNodeStyle,
  TreeViewerDocument,
} from "../common/tree-viewer/types";
import "../common/tree-viewer/tree-viewer.css";

type ViewBox = {
  x: number;
  y: number;
  width: number;
  height: number;
};

type TreeResource = {
  exists: boolean;
  treePath: string;
  dataUrl: string;
};

type TreeArtifactId = string;

type TreeArtifact = {
  id: TreeArtifactId;
  label: string;
};

type DataInterfaceTreeEntry = TreeViewerDocument & {
  id: string;
  label: string;
};

type TreeSelection =
  | { kind: "node"; id: string }
  | { kind: "edge"; id: string }
  | null;

type PointerPan = {
  pointerId: number;
  x: number;
  y: number;
  viewBox: ViewBox;
};

type ViewportSize = {
  width: number;
  height: number;
};

type SidebarResize = {
  pointerId: number;
  x: number;
  width: number;
};


const logger = createWebviewLogger("TreeViewer");

const POWER_ARTIFACT: TreeArtifact = { id: "power", label: "Power" };

const EMPTY_DOCUMENT: TreeViewerDocument = {
  nodes: [],
  edges: [],
  groups: [],
};

// Role → color mapping for data interface nodes
const DATA_INTERFACE_ROLE_COLORS: Record<string, string> = {
  controller: "#2F7FD1",
  target: "#2F9FB3",
  node: "#6B8E23",
  end_node: "#D17F2F",
  passive: "#9E9E9E",
  disconnected: "#D14040",
  unknown_role: "#D17F2F",
};

const DATA_INTERFACE_ROLE_LABELS: Record<string, string> = {
  unknown_role: "Unknown role",
};

const DATA_INTERFACE_EDGE_COLOR = "#9CB6D3";

// Per-bus-type whitelist of parameters to preview on the node block.
// All other parameters are visible in the inspector/detail panel.
// "*" matches any role.
const DATA_INTERFACE_PREVIEW_WHITELIST: Record<string, Record<string, Set<string>>> = {
  i2c: {
    target: new Set(["address"]),
    controller: new Set(["frequency"]),
    disconnected: new Set(["address"]),
    unknown_role: new Set(["address"]),
  },
  can: {
    "*": new Set(["baudrate"]),
  },
  can_ttl: {
    "*": new Set(["baudrate"]),
  },
  uart: {
    "*": new Set(["baudrate"]),
  },
};

function isUnresolved(value: string): boolean {
  return !value || value === "?" || value.includes("\u211d");
}

/**
 * Derive nodeStyles, edgeStyles, legend, and preview flags from the node types
 * present in a data interface tree entry.
 */
function applyDataInterfaceStyles(doc: DataInterfaceTreeEntry): TreeViewerDocument {
  const busType = doc.id;

  // Apply styles if not already present
  let result: TreeViewerDocument = doc;
  if (!doc.nodeStyles || doc.nodeStyles.length === 0) {
    const roleSet = new Set(doc.nodes.map((n) => n.type));
    const nodeStyles: TreeNodeStyle[] = [...roleSet].sort().map((role) => ({
      id: role,
      label: DATA_INTERFACE_ROLE_LABELS[role] ?? role.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
      color: DATA_INTERFACE_ROLE_COLORS[role] ?? "#6B8E23",
    }));

    const edgeStyles: TreeEdgeStyle[] = [{ id: "edge", color: DATA_INTERFACE_EDGE_COLOR }];

    const legend: TreeViewerDocument["legend"] = [
      ...nodeStyles.map((s) => ({ kind: "node" as const, styleId: s.id, label: s.label ?? s.id })),
      { kind: "edge" as const, styleId: "edge", label: "Connection" },
    ];

    result = { ...doc, nodeStyles, edgeStyles, legend };
  }

  // Apply preview whitelist — only whitelisted resolved params get preview
  const busWhitelist = DATA_INTERFACE_PREVIEW_WHITELIST[busType] ?? {};
  result = {
    ...result,
    nodes: result.nodes.map((node) => {
      if (!node.meta) return node;
      const allowed = busWhitelist[node.type] ?? busWhitelist["*"] ?? new Set<string>();
      const updatedMeta: Record<string, TreeMetaEntry> = {};
      for (const [key, entry] of Object.entries(node.meta)) {
        const canPreview = allowed.has(key) && !isUnresolved(entry.value);
        updatedMeta[key] = { ...entry, preview: canPreview };
      }
      return { ...node, meta: updatedMeta };
    }),
  };

  return result;
}

// Power tree role → color mapping
const POWER_TREE_ROLE_COLORS: Record<string, string> = {
  source: "#6E8B4A",
  converter: "#B15C76",
  bidirectional_converter: "#B15C76",
  sink: "#C87533",
  disconnected: "#D14040",
};

const POWER_TREE_ROLE_LABELS: Record<string, string> = {
  source: "Source",
  converter: "Converter",
  bidirectional_converter: "Bidirectional",
  sink: "Load",
  disconnected: "Disconnected",
};

const POWER_TREE_EDGE_COLOR = "#C9B8AB";

function applyPowerTreeStyles(doc: TreeViewerDocument): TreeViewerDocument {
  if (doc.nodeStyles && doc.nodeStyles.length > 0) {
    return doc;
  }

  const roleSet = new Set(doc.nodes.map((n) => n.type));
  const nodeStyles: TreeNodeStyle[] = [...roleSet].sort().map((role) => ({
    id: role,
    label: POWER_TREE_ROLE_LABELS[role] ?? role.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
    color: POWER_TREE_ROLE_COLORS[role] ?? "#6B8E23",
  }));

  const edgeStyles: TreeEdgeStyle[] = [{ id: "edge", color: POWER_TREE_EDGE_COLOR }];

  const legend: TreeViewerDocument["legend"] = [
    ...nodeStyles.map((s) => ({ kind: "node" as const, styleId: s.id, label: s.label ?? s.id })),
    { kind: "edge" as const, styleId: "edge", label: "Connection" },
  ];

  return { ...doc, nodeStyles, edgeStyles, legend };
}

type TreeNodeMetrics = Pick<TreeNodePosition, "width" | "height"> & {
  kindLabel: string;
  accent: string;
};

type TreePreviewRow = {
  id: string;
  label: string;
  value: string;
  alternateValue: string | null;
};

type TreeGroupBounds = {
  x: number;
  y: number;
  width: number;
  height: number;
  labelX: number;
  headerY: number;
};

type TreeRenderLayout = TreeLayout & {
  nodeMetrics: Record<string, TreeNodeMetrics>;
  groupBounds: Record<string, TreeGroupBounds>;
};

const TREE_NODE_THEME = {
  minHeight: 82,
  horizontalPadding: 18,
  headerHeight: 36,
  detailSectionPadding: 10,
  detailRowHeight: 22,
  cornerRadius: 10,
  borderWidth: 3,
};

const TREE_GROUP_THEME = {
  sidePadding: 24,
  topPadding: 20,
  bottomPadding: 52,
  labelX: 24,
  labelBottomOffset: 16,
};

const TREE_SIDEBAR_MIN_WIDTH = 300;
const TREE_CANVAS_MIN_WIDTH = 320;
const TREE_TEXT_SIZE = 16;
const TREE_NODE_WIDTH_MIN = 120;
const TREE_NODE_WIDTH_MAX = 480;
const TREE_COLUMN_GAP = 80;
const TREE_ROW_GAP = 108;
const TREE_EDGE_STROKE_WIDTH = 2.8;
const TREE_EDGE_HIGHLIGHT_STROKE_WIDTH = TREE_EDGE_STROKE_WIDTH + 1;
const TREE_FALLBACK_COLOR = "var(--text-muted)";

let textMeasureContext: CanvasRenderingContext2D | null | undefined;

function getTextMeasureContext(): CanvasRenderingContext2D | null {
  if (textMeasureContext !== undefined) {
    return textMeasureContext;
  }
  const canvas = globalThis.document?.createElement("canvas");
  textMeasureContext = canvas?.getContext("2d") ?? null;
  return textMeasureContext;
}

function measureTextWidth(
  text: string,
  options: { fontSize: number; fontWeight: number; letterSpacing?: number },
): number {
  if (!text) {
    return 0;
  }

  const { fontSize, fontWeight, letterSpacing = 0 } = options;
  const context = getTextMeasureContext();
  if (!context) {
    return text.length * fontSize * 0.62 + Math.max(0, text.length - 1) * letterSpacing;
  }

  const fontFamily =
    globalThis.getComputedStyle?.(globalThis.document?.documentElement ?? globalThis.document?.body ?? null)
      .getPropertyValue("--font-sans")
      .trim()
    || "ui-sans-serif, system-ui, sans-serif";
  context.font = `${fontWeight} ${fontSize}px ${fontFamily}`;
  return context.measureText(text).width + Math.max(0, text.length - 1) * letterSpacing;
}

function getPreviewRows(meta?: Record<string, TreeMetaEntry>): TreePreviewRow[] {
  if (!meta) {
    return [];
  }

  return Object.entries(meta)
    .filter(([, entry]) => entry.preview)
    .flatMap(([key, entry]) => {
      const valueLines = entry.value
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
      const alternateLines = entry.alternateValue
        ?.split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
        ?? [];

      return valueLines.map((line, index) => ({
        id: `${key}:${index}`,
        label: entry.label,
        value: line,
        alternateValue:
          alternateLines[index]
          ?? (alternateLines.length === 1 ? alternateLines[0]! : null),
      }));
    })
    .filter((entry) => entry.label && entry.value);
}

function getNodePreviewRows(node: TreeNode): TreePreviewRow[] {
  return getPreviewRows(node.meta);
}

function measureNodeMetrics(
  treeDocument: TreeViewerDocument,
): Record<string, TreeNodeMetrics> {
  const metrics: Record<string, TreeNodeMetrics> = {};
  const textOpts = { fontSize: TREE_TEXT_SIZE, fontWeight: 700 };
  const pad = TREE_NODE_THEME.horizontalPadding;

  for (const node of treeDocument.nodes) {
    const nodeStyle = getNodeStyle(treeDocument, node.type);
    const kindLabel = nodeStyle.label ?? node.type;
    const accent = nodeStyle.color;
    const detailRows = getNodePreviewRows(node);
    const detailHeight = detailRows.length > 0
      ? TREE_NODE_THEME.detailSectionPadding * 2
        + (detailRows.length - 1) * TREE_NODE_THEME.detailRowHeight
        + TREE_TEXT_SIZE
      : 0;
    const contentHeight = Math.ceil(TREE_NODE_THEME.headerHeight + detailHeight);
    const height = detailRows.length === 0
      ? Math.max(TREE_NODE_THEME.minHeight, contentHeight)
      : contentHeight;

    // Auto-width: header has kindLabel (left) + node.label (right) + padding
    const kindW = measureTextWidth(kindLabel, textOpts);
    const labelW = measureTextWidth(node.label, textOpts);
    const headerWidth = kindW + labelW + 3 * pad;
    const width = Math.min(Math.max(headerWidth, TREE_NODE_WIDTH_MIN), TREE_NODE_WIDTH_MAX);

    metrics[node.id] = {
      width,
      height,
      kindLabel,
      accent,
    };
  }

  return metrics;
}

function getGroupBounds(
  group: TreeGroup,
  positions: Record<string, TreeNodePosition>,
): TreeGroupBounds | null {
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;

  for (const id of group.memberIds) {
    const position = positions[id];
    if (!position) {
      continue;
    }
    minX = Math.min(minX, position.x - position.width / 2);
    maxX = Math.max(maxX, position.x + position.width / 2);
    minY = Math.min(minY, position.y - position.height / 2);
    maxY = Math.max(maxY, position.y + position.height / 2);
  }

  if (!Number.isFinite(minX)) {
    return null;
  }

  const labelWidth = measureTextWidth(group.label, {
    fontSize: TREE_TEXT_SIZE,
    fontWeight: 600,
  });
  let x = minX - TREE_GROUP_THEME.sidePadding;
  const y = minY - TREE_GROUP_THEME.topPadding;
  let width = maxX - minX + TREE_GROUP_THEME.sidePadding * 2;
  const height = maxY - minY + TREE_GROUP_THEME.topPadding + TREE_GROUP_THEME.bottomPadding;
  const minimumLabelWidth =
    TREE_GROUP_THEME.labelX
    + labelWidth
    + TREE_GROUP_THEME.sidePadding;

  if (width < minimumLabelWidth) {
    const expand = (minimumLabelWidth - width) / 2;
    x -= expand;
    width = minimumLabelWidth;
  }

  return {
    x,
    y,
    width,
    height,
    labelX: x + TREE_GROUP_THEME.labelX,
    headerY: y + height - TREE_GROUP_THEME.labelBottomOffset,
  };
}

function resolveGroupOverlaps(
  groupBounds: Record<string, TreeGroupBounds>,
): void {
  const entries = Object.entries(groupBounds);
  const padding = TREE_GROUP_THEME.sidePadding;

  // Sort by area descending so larger groups expand to contain smaller ones
  entries.sort(
    ([, a], [, b]) => b.width * b.height - a.width * a.height,
  );

  for (let i = 0; i < entries.length; i++) {
    const outer = entries[i]![1];
    for (let j = i + 1; j < entries.length; j++) {
      const inner = entries[j]![1];

      // Check if bounds overlap
      const overlapX = outer.x < inner.x + inner.width && inner.x < outer.x + outer.width;
      const overlapY = outer.y < inner.y + inner.height && inner.y < outer.y + outer.height;
      if (!overlapX || !overlapY) {
        continue;
      }

      // Expand outer to fully contain inner with padding
      const newX = Math.min(outer.x, inner.x - padding);
      const newY = Math.min(outer.y, inner.y - padding);
      const newRight = Math.max(outer.x + outer.width, inner.x + inner.width + padding);
      const newBottom = Math.max(
        outer.y + outer.height,
        inner.y + inner.height + padding,
      );
      outer.x = newX;
      outer.y = newY;
      outer.width = newRight - newX;
      outer.height = newBottom - newY;

      // Recompute label position at bottom-left
      outer.labelX = outer.x + TREE_GROUP_THEME.labelX;
      outer.headerY = outer.y + outer.height - TREE_GROUP_THEME.labelBottomOffset;
    }
  }
}

function getLayoutBounds(
  positions: Record<string, TreeNodePosition>,
  groupBounds: Record<string, TreeGroupBounds>,
): TreeBounds {
  const minXs = Object.values(positions).map((position) => position.x - position.width / 2);
  const minYs = Object.values(positions).map((position) => position.y - position.height / 2);
  const maxXs = Object.values(positions).map((position) => position.x + position.width / 2);
  const maxYs = Object.values(positions).map((position) => position.y + position.height / 2);

  for (const group of Object.values(groupBounds)) {
    minXs.push(group.x);
    minYs.push(group.y);
    maxXs.push(group.x + group.width);
    maxYs.push(group.y + group.height);
  }

  if (minXs.length === 0) {
    return { minX: 0, minY: 0, maxX: 0, maxY: 0 };
  }

  return {
    minX: Math.min(...minXs),
    minY: Math.min(...minYs),
    maxX: Math.max(...maxXs),
    maxY: Math.max(...maxYs),
  };
}

function getAspectRatio(width: number, height: number): number | null {
  if (!(width > 0) || !(height > 0)) {
    return null;
  }
  return width / height;
}

function buildViewBox(bounds: TreeBounds, aspectRatio: number | null = null): ViewBox {
  const padding = 160;
  const contentWidth = Math.max(1, bounds.maxX - bounds.minX);
  const contentHeight = Math.max(1, bounds.maxY - bounds.minY);
  const paddedWidth = contentWidth + padding * 2;
  const paddedHeight = contentHeight + padding * 2;
  const centerX = (bounds.minX + bounds.maxX) / 2;
  const centerY = (bounds.minY + bounds.maxY) / 2;

  if (!aspectRatio) {
    return {
      x: centerX - paddedWidth / 2,
      y: centerY - paddedHeight / 2,
      width: paddedWidth,
      height: paddedHeight,
    };
  }

  const contentAspectRatio = paddedWidth / paddedHeight;
  const width = contentAspectRatio < aspectRatio
    ? paddedHeight * aspectRatio
    : paddedWidth;
  const height = contentAspectRatio > aspectRatio
    ? paddedWidth / aspectRatio
    : paddedHeight;

  return {
    x: centerX - width / 2,
    y: centerY - height / 2,
    width,
    height,
  };
}

function getNodeStyle(document: TreeViewerDocument, styleId: string): TreeNodeStyle {
  const style = document.nodeStyles?.find((candidate) => candidate.id === styleId);
  if (!style?.color) {
    return { id: styleId, label: style?.label, color: TREE_FALLBACK_COLOR };
  }
  return style;
}

function getEdgeStyle(document: TreeViewerDocument, styleId: string): TreeEdgeStyle {
  const style = document.edgeStyles?.find((candidate) => candidate.id === styleId);
  if (!style?.color) {
    return { id: styleId, color: TREE_FALLBACK_COLOR };
  }
  return style;
}

function validateTreeDocument<T extends TreeViewerDocument>(document: T): T {
  return document;
}

function buildNodeHeaderPath(width: number, height: number, radius: number): string {
  const cappedRadius = Math.min(radius, width / 2, height);
  return [
    `M 0 ${height}`,
    `L 0 ${cappedRadius}`,
    `Q 0 0 ${cappedRadius} 0`,
    `L ${width - cappedRadius} 0`,
    `Q ${width} 0 ${width} ${cappedRadius}`,
    `L ${width} ${height}`,
    "Z",
  ].join(" ");
}

function computeTreeLayout(
  treeDocument: TreeViewerDocument,
): TreeRenderLayout {
  const columnGap = TREE_COLUMN_GAP;
  const rowGap = TREE_ROW_GAP;
  const nodeMetrics = measureNodeMetrics(treeDocument);
  const rowSpacing = rowGap;

  if (treeDocument.nodes.length === 0) {
    return {
      positions: {},
      nodeMetrics,
      groupBounds: {},
      bounds: { minX: 0, minY: 0, maxX: 0, maxY: 0 },
    };
  }

  const positions: Record<string, TreeNodePosition> = {};
  const nodeOrder = new Map(treeDocument.nodes.map((node, index) => [node.id, index]));
  const outgoing = new Map(treeDocument.nodes.map((node) => [node.id, [] as string[]]));
  const children = new Map(treeDocument.nodes.map((node) => [node.id, [] as string[]]));
  const incomingCount = new Map(treeDocument.nodes.map((node) => [node.id, 0]));

  const groupOf = new Map<string, string>();
  for (const node of treeDocument.nodes) {
    if (node.groupId) groupOf.set(node.id, node.groupId);
  }

  const GROUP_VERTICAL_GAP = 0.5;

  function compareByGroup(a: string, b: string): number {
    const ga = groupOf.get(a) ?? "";
    const gb = groupOf.get(b) ?? "";
    if (ga < gb) return -1;
    if (ga > gb) return 1;
    return 0;
  }

  for (const edge of treeDocument.edges) {
    outgoing.get(edge.source)?.push(edge.target);
    if (incomingCount.has(edge.target)) {
      incomingCount.set(edge.target, (incomingCount.get(edge.target) ?? 0) + 1);
    }
  }

  for (const nextIds of outgoing.values()) {
    nextIds.sort((left, right) => (nodeOrder.get(left) ?? 0) - (nodeOrder.get(right) ?? 0));
  }

  const rootIds: string[] = [];
  const claimed = new Set<string>();

  function attachRoot(rootId: string): void {
    rootIds.push(rootId);
    claimed.add(rootId);

    // The underlying data may be a DAG, but the viewer should read like a tree.
    const queue = [rootId];
    for (let index = 0; index < queue.length; index += 1) {
      const nodeId = queue[index]!;
      for (const childId of outgoing.get(nodeId) ?? []) {
        if (claimed.has(childId)) {
          continue;
        }
        children.get(nodeId)?.push(childId);
        claimed.add(childId);
        queue.push(childId);
      }
    }
  }

  for (const node of treeDocument.nodes) {
    if ((incomingCount.get(node.id) ?? 0) === 0) {
      attachRoot(node.id);
    }
  }

  for (const node of treeDocument.nodes) {
    if (!claimed.has(node.id)) {
      attachRoot(node.id);
    }
  }

  // Compute depths via BFS
  const depthMap = new Map<string, number>();
  for (const rootId of rootIds) {
    const queue = [rootId];
    depthMap.set(rootId, 0);
    for (let i = 0; i < queue.length; i++) {
      const nid = queue[i]!;
      const d = depthMap.get(nid)!;
      for (const cid of children.get(nid) ?? []) {
        depthMap.set(cid, d + 1);
        queue.push(cid);
      }
    }
  }

  // Compute max node width per depth column
  const maxWidthPerDepth = new Map<number, number>();
  for (const [nodeId, depth] of depthMap) {
    const w = nodeMetrics[nodeId]?.width ?? TREE_NODE_WIDTH_MIN;
    maxWidthPerDepth.set(depth, Math.max(maxWidthPerDepth.get(depth) ?? 0, w));
  }

  // Compute cumulative column left-edge x offsets
  const maxDepth = Math.max(0, ...maxWidthPerDepth.keys());
  const columnLeft = new Map<number, number>();
  let cumulativeX = 0;
  for (let d = 0; d <= maxDepth; d++) {
    columnLeft.set(d, cumulativeX);
    cumulativeX += (maxWidthPerDepth.get(d) ?? 0) + columnGap;
  }

  const subtreeHeights = new Map<string, number>();

  function getSubtreeHeight(nodeId: string): number {
    const cached = subtreeHeights.get(nodeId);
    if (cached !== undefined) {
      return cached;
    }
    const childIds = children.get(nodeId) ?? [];
    if (childIds.length === 0) {
      subtreeHeights.set(nodeId, 1);
      return 1;
    }
    const sorted = [...childIds].sort(compareByGroup);
    let height = 0;
    let prevGroup: string | undefined;
    for (const childId of sorted) {
      const childGroup = groupOf.get(childId) ?? "";
      if (prevGroup !== undefined && childGroup !== prevGroup) {
        height += GROUP_VERTICAL_GAP;
      }
      height += getSubtreeHeight(childId);
      prevGroup = childGroup;
    }
    subtreeHeights.set(nodeId, height);
    return height;
  }

  function placeSubtree(nodeId: string, depth: number, top: number): void {
    const height = getSubtreeHeight(nodeId);
    const colWidth = maxWidthPerDepth.get(depth) ?? TREE_NODE_WIDTH_MIN;
    const metric = nodeMetrics[nodeId] ?? {
      width: TREE_NODE_WIDTH_MIN,
      height: TREE_NODE_THEME.minHeight,
    };
    const left = columnLeft.get(depth) ?? 0;
    positions[nodeId] = {
      x: left + colWidth / 2,
      y: (top + height / 2) * rowSpacing,
      z: 0,
      width: colWidth,
      height: metric.height,
    };

    const sortedChildren = [...(children.get(nodeId) ?? [])].sort(compareByGroup);
    let childTop = top;
    let prevGroup: string | undefined;
    for (const childId of sortedChildren) {
      const childGroup = groupOf.get(childId) ?? "";
      if (prevGroup !== undefined && childGroup !== prevGroup) {
        childTop += GROUP_VERTICAL_GAP;
      }
      placeSubtree(childId, depth + 1, childTop);
      childTop += getSubtreeHeight(childId);
      prevGroup = childGroup;
    }
  }

  rootIds.sort(compareByGroup);
  let top = 0;
  let prevRootGroup: string | undefined;
  for (const rootId of rootIds) {
    const rootGroup = groupOf.get(rootId) ?? "";
    if (prevRootGroup !== undefined && rootGroup !== prevRootGroup) {
      top += GROUP_VERTICAL_GAP;
    }
    placeSubtree(rootId, 0, top);
    top += getSubtreeHeight(rootId);
    prevRootGroup = rootGroup;
  }

  const groupBounds = Object.fromEntries(
    treeDocument.groups
      .map((group) => [group.id, getGroupBounds(group, positions)] as const)
      .filter((entry): entry is [string, TreeGroupBounds] => entry[1] !== null),
  );

  resolveGroupOverlaps(groupBounds);

  return {
    positions,
    nodeMetrics,
    groupBounds,
    bounds: getLayoutBounds(positions, groupBounds),
  };
}

function buildEdgePath(from: TreeNodePosition, to: TreeNodePosition): string {
  const startX = from.x + from.width / 2;
  const startY = from.y;
  const endX = to.x - to.width / 2;
  const endY = to.y;
  const deltaX = endX - startX;
  return [
    `M ${startX} ${startY}`,
    `C ${startX + deltaX * 0.4} ${startY}, ${startX + deltaX * 0.6} ${endY}, ${endX} ${endY}`,
  ].join(" ");
}

function isSelectedBuildTarget(
  projectRoot: string | null,
  selectedTarget: ResolvedBuildTarget | null,
  buildTarget: ResolvedBuildTarget | null | undefined,
  buildProjectRoot: string | null | undefined,
): boolean {
  return Boolean(
    projectRoot
      && selectedTarget
      && buildTarget
      && samePath(projectRoot, buildProjectRoot)
      && samePath(selectedTarget.root, buildTarget.root)
      && selectedTarget.name === buildTarget.name
      && selectedTarget.entry === buildTarget.entry,
  );
}


function TreeCanvas({
  document,
  recenterVersion,
  selection,
  hoveredElement,
  onSelect,
  onHover,
}: {
  document: TreeViewerDocument;
  recenterVersion: number;
  selection: TreeSelection;
  hoveredElement: TreeSelection;
  onSelect: (selection: TreeSelection) => void;
  onHover: (selection: TreeSelection) => void;
}) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const panRef = useRef<PointerPan | null>(null);
  const [isPanning, setIsPanning] = useState(false);
  const [viewportSize, setViewportSize] = useState<ViewportSize | null>(null);
  const [previewModes, setPreviewModes] = useState<Record<string, boolean>>({});
  const layout = useMemo(
    () => computeTreeLayout(document),
    [document],
  );
  const [viewBox, setViewBox] = useState<ViewBox>(() => buildViewBox(layout.bounds));
  const resolvedViewportSize = viewportSize && viewportSize.width > 0 && viewportSize.height > 0
    ? viewportSize
    : null;
  const viewportAspectRatio = resolvedViewportSize
    ? getAspectRatio(resolvedViewportSize.width, resolvedViewportSize.height)
    : null;
  const viewportUnitsPerPixel = resolvedViewportSize
    ? Math.max(viewBox.width / resolvedViewportSize.width, viewBox.height / resolvedViewportSize.height)
    : 1;
  const edgeStrokeWidth = TREE_EDGE_STROKE_WIDTH * viewportUnitsPerPixel;
  const highlightedEdgeStrokeWidth = TREE_EDGE_HIGHLIGHT_STROKE_WIDTH * viewportUnitsPerPixel;
  const selectedNodeId = selection?.kind === "node" ? selection.id : null;
  const selectedEdgeId = selection?.kind === "edge" ? selection.id : null;
  const hoveredNodeId = hoveredElement?.kind === "node" ? hoveredElement.id : null;
  const hoveredEdgeId = hoveredElement?.kind === "edge" ? hoveredElement.id : null;

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) {
      return;
    }

    const syncViewportSize = (width: number, height: number) => {
      if (!(width > 0) || !(height > 0)) {
        return;
      }
      setViewportSize({ width, height });
    };

    const rect = svg.getBoundingClientRect();
    syncViewportSize(rect.width, rect.height);

    if (typeof ResizeObserver === "undefined") {
      return;
    }

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) {
        return;
      }
      syncViewportSize(entry.contentRect.width, entry.contentRect.height);
    });
    observer.observe(svg);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setViewBox(buildViewBox(layout.bounds, viewportAspectRatio));
    panRef.current = null;
    setIsPanning(false);
  }, [layout, recenterVersion, viewportAspectRatio]);

  useEffect(() => {
    setPreviewModes({});
  }, [document]);

  function handleWheel(event: React.WheelEvent<SVGSVGElement>): void {
    event.preventDefault();
    const svg = svgRef.current;
    if (!svg) {
      return;
    }

    const rect = svg.getBoundingClientRect();
    const nextScale = Math.exp((event.deltaMode === 1 ? event.deltaY * 33 : event.deltaY) * 0.0015);
    const cursorX = viewBox.x + ((event.clientX - rect.left) / rect.width) * viewBox.width;
    const cursorY = viewBox.y + ((event.clientY - rect.top) / rect.height) * viewBox.height;

    setViewBox((current) => {
      const width = Math.max(240, Math.min(24000, current.width * nextScale));
      const height = Math.max(180, Math.min(18000, current.height * nextScale));
      const scaleX = width / current.width;
      const scaleY = height / current.height;
      return {
        x: cursorX - (cursorX - current.x) * scaleX,
        y: cursorY - (cursorY - current.y) * scaleY,
        width,
        height,
      };
    });
  }

  function handlePointerDown(event: React.PointerEvent<SVGSVGElement>): void {
    if (event.button !== 1 && event.button !== 2) {
      return;
    }
    panRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      viewBox,
    };
    setIsPanning(true);
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handlePointerMove(event: React.PointerEvent<SVGSVGElement>): void {
    const pan = panRef.current;
    const svg = svgRef.current;
    if (!pan || !svg) {
      return;
    }
    const rect = svg.getBoundingClientRect();
    const scaleX = pan.viewBox.width / rect.width;
    const scaleY = pan.viewBox.height / rect.height;
    setViewBox({
      x: pan.viewBox.x - (event.clientX - pan.x) * scaleX,
      y: pan.viewBox.y - (event.clientY - pan.y) * scaleY,
      width: pan.viewBox.width,
      height: pan.viewBox.height,
    });
  }

  function endPan(event?: React.PointerEvent<SVGSVGElement>): void {
    const pan = panRef.current;
    if (pan && event && event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    panRef.current = null;
    setIsPanning(false);
  }

  return (
    <svg
      ref={svgRef}
      className={`tree-viewer__canvas-svg${isPanning ? " is-panning" : ""}`}
      preserveAspectRatio="xMidYMid meet"
      viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.width} ${viewBox.height}`}
      onClick={() => onSelect(null)}
      onWheel={handleWheel}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endPan}
      onPointerCancel={endPan}
      onPointerLeave={(event) => {
        onHover(null);
        endPan(event);
      }}
      onContextMenu={(event) => event.preventDefault()}
    >
      <defs>
        <filter id="tree-viewer-node-shadow" x="-24%" y="-28%" width="148%" height="164%">
          <feDropShadow dx="0" dy="4" stdDeviation="7" floodColor="#0f172a" floodOpacity="0.12" />
          <feDropShadow dx="0" dy="1" stdDeviation="2" floodColor="#0f172a" floodOpacity="0.08" />
        </filter>
        <pattern id="tree-bidir-stripe" width="20" height="20"
                 patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="20" height="20" fill="#B15C76" />
          <rect width="10" height="20" fill="#D4849A" />
        </pattern>
      </defs>

      {document.groups.map((group) => {
        const bounds = layout.groupBounds[group.id];
        if (!bounds) {
          return null;
        }

        const stroke = getNodeStyle(document, group.accentKind).color;
        return (
          <g key={group.id} pointerEvents="none">
            <rect
              x={bounds.x}
              y={bounds.y}
              width={bounds.width}
              height={bounds.height}
              rx={14}
              fill="var(--bg-primary)"
              fillOpacity={0.14}
              stroke={stroke}
              strokeOpacity={0.3}
              strokeWidth={3}
            />
            <text
              x={bounds.labelX}
              y={bounds.headerY}
              fill="var(--text-primary)"
              fontSize={TREE_TEXT_SIZE}
              fontWeight={600}
            >
              {group.label}
            </text>
          </g>
        );
      })}

      {document.edges.map((edge) => {
        const from = layout.positions[edge.source];
        const to = layout.positions[edge.target];
        if (!from || !to) {
          return null;
        }

        const edgeKind = edge.kind ?? "edge";
        const edgeColor = getEdgeStyle(document, edgeKind).color;
        const highlighted =
          hoveredEdgeId === edge.id
          || selectedEdgeId === edge.id
          || hoveredNodeId === edge.source
          || hoveredNodeId === edge.target
          || selectedNodeId === edge.source
          || selectedNodeId === edge.target;

        return (
          <path
            key={edge.id}
            d={buildEdgePath(from, to)}
            fill="none"
            stroke={edgeColor}
            strokeWidth={highlighted ? highlightedEdgeStrokeWidth : edgeStrokeWidth}
            strokeOpacity={highlighted ? 0.95 : 0.45}
            pointerEvents="stroke"
            data-style-id={edgeKind}
            className="tree-viewer__edge"
            onClick={(event) => {
              event.stopPropagation();
              onSelect({ kind: "edge", id: edge.id });
            }}
            onMouseEnter={() => onHover({ kind: "edge", id: edge.id })}
            onMouseLeave={() => onHover(null)}
          />
        );
      })}

      {document.nodes.map((node) => {
        const position = layout.positions[node.id];
        if (!position) {
          return null;
        }

        const nodeStyle = getNodeStyle(document, node.type);
        const metric = layout.nodeMetrics[node.id];
        const accent = metric?.accent
          ?? nodeStyle.color;
        const kindLabel = metric?.kindLabel ?? nodeStyle.label ?? node.type;
        const isSelected = selectedNodeId === node.id;
        const border = isSelected ? "var(--accent)" : accent;
        const detailRows = getNodePreviewRows(node);
        const headerCenterY = TREE_NODE_THEME.headerHeight / 2;
        const headerFillHeight = TREE_NODE_THEME.headerHeight;
        const leftX = TREE_NODE_THEME.horizontalPadding;
        const rightX = position.width - TREE_NODE_THEME.horizontalPadding;
        const x = position.x - position.width / 2;
        const y = position.y - position.height / 2;

        return (
          <g
            key={node.id}
            transform={`translate(${x} ${y})`}
            className="tree-viewer__node"
            onClick={(event) => {
              event.stopPropagation();
              onSelect({ kind: "node", id: node.id });
            }}
            onMouseEnter={() => onHover({ kind: "node", id: node.id })}
            onMouseLeave={() => onHover(null)}
          >
            <g filter="url(#tree-viewer-node-shadow)">
              <rect
                width={position.width}
                height={position.height}
                rx={TREE_NODE_THEME.cornerRadius}
                fill="var(--bg-primary)"
              />
              <path
                d={buildNodeHeaderPath(position.width, headerFillHeight, TREE_NODE_THEME.cornerRadius)}
                fill={node.type === "bidirectional_converter" ? "url(#tree-bidir-stripe)" : border}
              />
            </g>
            <rect
              width={position.width}
              height={position.height}
              rx={TREE_NODE_THEME.cornerRadius}
              fill="none"
              stroke={border}
              strokeOpacity={1}
              strokeWidth={TREE_NODE_THEME.borderWidth}
            />
            <text
              x={leftX}
              y={headerCenterY}
              fill="var(--bg-primary)"
              fontSize={TREE_TEXT_SIZE}
              fontWeight={700}
              dominantBaseline="middle"
            >
              {kindLabel}
            </text>
            <text
              x={rightX}
              y={headerCenterY}
              fill="var(--bg-primary)"
              fontSize={TREE_TEXT_SIZE}
              fontWeight={700}
              textAnchor="end"
              dominantBaseline="middle"
            >
              {node.label}
            </text>
            {detailRows.map((row, index) => {
              const rowY =
                headerFillHeight
                + TREE_NODE_THEME.detailSectionPadding
                + index * TREE_NODE_THEME.detailRowHeight;
              const previewKey = `${node.id}:${row.id}`;
              const showAlternate = previewModes[previewKey] ?? true;
              const displayedValue =
                showAlternate && row.alternateValue ? row.alternateValue : row.value;
              const toggleable = Boolean(row.alternateValue);

              return (
                <g key={`${node.id}-detail-${index}`}>
                  <text
                    x={leftX}
                    y={rowY}
                    fill="var(--text-muted)"
                    fontSize={TREE_TEXT_SIZE}
                    fontWeight={400}
                    dominantBaseline="hanging"
                  >
                    {row.label}
                  </text>
                  <text
                    x={rightX}
                    y={rowY}
                    fill="var(--text-primary)"
                    fontSize={TREE_TEXT_SIZE}
                    fontWeight={700}
                    textAnchor="end"
                    dominantBaseline="hanging"
                    className={toggleable ? "tree-viewer__preview-value is-toggleable" : "tree-viewer__preview-value"}
                    onClick={toggleable
                      ? (event) => {
                        event.stopPropagation();
                        setPreviewModes((current) => ({
                          ...current,
                          [previewKey]: !(current[previewKey] ?? true),
                        }));
                      }
                      : undefined}
                  >
                    {displayedValue}
                  </text>
                </g>
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}

function FragmentMeta({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

function getEdgeLabel(edge: TreeEdge, nodeById: Map<string, TreeNode>): string {
  if (edge.label) {
    return edge.label;
  }
  return `${nodeById.get(edge.source)?.label ?? edge.source} \u2192 ${nodeById.get(edge.target)?.label ?? edge.target}`;
}

function Inspector({
  document,
  selection,
}: {
  document: TreeViewerDocument | null;
  selection: TreeSelection;
}) {
  const resolvedDocument = document ?? EMPTY_DOCUMENT;
  const nodeById = useMemo(
    () => new Map(resolvedDocument.nodes.map((node) => [node.id, node])),
    [resolvedDocument],
  );
  const selectedNode = useMemo(() => {
    if (selection?.kind !== "node") {
      return null;
    }
    return resolvedDocument.nodes.find((node) => node.id === selection.id) ?? null;
  }, [resolvedDocument, selection]);
  const selectedEdge = useMemo(() => {
    if (selection?.kind !== "edge") {
      return null;
    }
    return resolvedDocument.edges.find((edge) => edge.id === selection.id) ?? null;
  }, [resolvedDocument, selection]);
  const selectedEdgeKindLabel = "Edge";

  return (
    <section className="tree-viewer__section">
      <div className="tree-viewer__section-title">Inspector</div>
      {!selectedNode && !selectedEdge ? (
        <div className="tree-viewer__inspector-empty">Click a node or edge to inspect it.</div>
      ) : (
        <div>
          <div className="tree-viewer__node-type">
            {selectedNode
              ? getNodeStyle(resolvedDocument, selectedNode.type).label ?? selectedNode.type
              : selectedEdgeKindLabel}
          </div>
          <div className="tree-viewer__node-label">
            {selectedNode?.label ?? (selectedEdge ? getEdgeLabel(selectedEdge, nodeById) : null)}
          </div>
          {selectedNode?.meta && Object.keys(selectedNode.meta).length > 0 ? (
            <dl className="tree-viewer__meta">
              {Object.entries(selectedNode.meta).map(([key, entry]) => (
                <FragmentMeta key={key} label={entry.label} value={entry.value} />
              ))}
            </dl>
          ) : null}
          {selectedEdge ? (
            <dl className="tree-viewer__meta">
              <FragmentMeta
                label="source"
                value={nodeById.get(selectedEdge.source)?.label ?? selectedEdge.source}
              />
              <FragmentMeta
                label="target"
                value={nodeById.get(selectedEdge.target)?.label ?? selectedEdge.target}
              />
              {Object.entries(selectedEdge.meta ?? {}).map(([key, entry]) => (
                <FragmentMeta key={key} label={entry.label} value={entry.value} />
              ))}
            </dl>
          ) : null}
        </div>
      )}
    </section>
  );
}

function Legend({ document }: { document: TreeViewerDocument | null }) {
  if (!document?.legend?.length) {
    return null;
  }

  return (
    <section className="tree-viewer__section">
      <div className="tree-viewer__section-title">Legend</div>
      <div className="tree-viewer__legend">
        {document.legend.map((item) => {
          const nodeStyle = item.kind === "node" ? getNodeStyle(document, item.styleId) : null;
          const edgeKind = item.kind === "edge" ? item.styleId ?? "edge" : null;
          const swatchColor = item.color
            ?? (item.kind === "node"
              ? nodeStyle?.color
              : edgeKind
                ? getEdgeStyle(document, edgeKind).color
                : undefined);
          return (
            <div key={`${item.kind}:${item.styleId}:${item.label}`} className="tree-viewer__legend-item">
              <span
                className={`tree-viewer__legend-swatch${item.kind === "edge" ? " is-edge" : ""}`}
                style={{ background: swatchColor }}
              />
              <span>{item.label}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function App() {
  const projectState = WebviewRpcClient.useSubscribe("projectState");
  const selectedBuild = WebviewRpcClient.useSubscribe("selectedBuild");
  const selectedTarget = projectState.selectedTarget;
  const [activeArtifactId, setActiveArtifactId] = useState<TreeArtifactId>("power");
  const [resource, setResource] = useState<TreeResource | null>(null);
  const [document, setDocument] = useState<TreeViewerDocument | null>(null);
  const [busTreeEntries, setBusTreeEntries] = useState<DataInterfaceTreeEntry[]>([]);
  const busTreeEntriesRef = useRef<DataInterfaceTreeEntry[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selection, setSelection] = useState<TreeSelection>(null);
  const [hoveredElement, setHoveredElement] = useState<TreeSelection>(null);
  const [isSidebarResizing, setIsSidebarResizing] = useState(false);
  const [recenterVersion, setRecenterVersion] = useState(0);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const sidebarRef = useRef<HTMLElement | null>(null);
  const requestRef = useRef(0);
  const resizeRef = useRef<SidebarResize | null>(null);

  const artifacts: TreeArtifact[] = useMemo(
    () => [POWER_ARTIFACT, ...busTreeEntries.map((e) => ({ id: e.id, label: e.label }))],
    [busTreeEntries],
  );
  const activeArtifact = artifacts.find((artifact) => artifact.id === activeArtifactId) ?? artifacts[0]!;

  const buildInProgress = Boolean(
    isSelectedBuildTarget(
      projectState.selectedProjectRoot,
      selectedTarget,
      selectedBuild?.target,
      selectedBuild?.projectRoot,
    ) && (selectedBuild?.status === "queued" || selectedBuild?.status === "building"),
  );

  useEffect(() => {
    const bodyElement = bodyRef.current;
    const sidebarElement = sidebarRef.current;
    if (!bodyElement || !sidebarElement) {
      return;
    }

    const syncWidth = () => {
      const nextWidth = sidebarElement.style.width
        ? sidebarElement.getBoundingClientRect().width
        : getDefaultSidebarWidth();
      sidebarElement.style.width = `${clampSidebarWidth(nextWidth)}px`;
    };

    syncWidth();

    if (typeof ResizeObserver === "undefined") {
      return;
    }

    const observer = new ResizeObserver(syncWidth);
    observer.observe(bodyElement);
    return () => observer.disconnect();
  }, []);

  // Load bus tree entries when target/build changes
  useEffect(() => {
    const setEntries = (entries: DataInterfaceTreeEntry[]) => {
      busTreeEntriesRef.current = entries;
      setBusTreeEntries(entries);
    };

    if (!selectedTarget || !rpcClient) {
      setEntries([]);
      return;
    }

    void rpcClient
      .requestAction<TreeResource>("vscode.resolveDataInterfaceTree", {
        target: selectedTarget,
      })
      .then(async (res) => {
        if (!res.exists) {
          setEntries([]);
          return;
        }
        const response = await fetch(res.dataUrl);
        if (!response.ok) {
          setEntries([]);
          return;
        }
        const entries = (await response.json()) as DataInterfaceTreeEntry[];
        setEntries(Array.isArray(entries) ? entries : []);
      })
      .catch(() => {
        setEntries([]);
      });
  }, [
    selectedBuild?.status,
    selectedTarget?.entry,
    selectedTarget?.name,
    selectedTarget?.root,
  ]);

  // Load tree document for the active artifact
  useEffect(() => {
    requestRef.current += 1;
    const requestId = requestRef.current;
    setResource(null);
    setDocument(null);
    setLoadError(null);
    setSelection(null);
    setHoveredElement(null);

    if (!selectedTarget || !rpcClient) {
      setIsLoading(false);
      return;
    }

    // For bus tabs, use cached data from busTreeEntriesRef
    if (activeArtifact.id !== "power") {
      const entry = busTreeEntries.find((e) => e.id === activeArtifact.id);
      if (entry) {
        setResource({ exists: true, treePath: "", dataUrl: "" });
        setDocument(applyDataInterfaceStyles(validateTreeDocument(entry)));
        setIsLoading(false);
      } else {
        setResource({ exists: false, treePath: "", dataUrl: "" });
        setIsLoading(false);
      }
      return;
    }

    // For power tab, use the existing RPC
    setIsLoading(true);

    void rpcClient.requestAction<TreeResource>("vscode.resolveTreeData", {
      target: selectedTarget,
      treeType: "power",
    })
      .then(async (nextResource) => {
        if (requestRef.current !== requestId) {
          return;
        }

        setResource(nextResource);
        if (!nextResource.exists) {
          setIsLoading(false);
          return;
        }

        const response = await fetch(nextResource.dataUrl);
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const nextDocument = validateTreeDocument(await response.json() as TreeViewerDocument);
        if (requestRef.current !== requestId) {
          return;
        }

        setDocument(applyPowerTreeStyles(nextDocument));
        setIsLoading(false);
      })
      .catch((error: unknown) => {
        if (requestRef.current !== requestId) {
          return;
        }

        const message = error instanceof Error ? error.message : String(error);
        logger.error(`Failed to load ${activeArtifact.id} tree: ${message}`);
        setLoadError(message);
        setIsLoading(false);
      });
  }, [
    activeArtifact.id,
    busTreeEntries,
    selectedBuild?.status,
    selectedTarget?.entry,
    selectedTarget?.name,
    selectedTarget?.root,
  ]);

  function getMaxSidebarWidth(): number {
    const bodyWidth = bodyRef.current?.getBoundingClientRect().width ?? 0;
    if (bodyWidth <= 0) {
      return Number.POSITIVE_INFINITY;
    }
    return Math.max(TREE_SIDEBAR_MIN_WIDTH, bodyWidth - TREE_CANVAS_MIN_WIDTH);
  }

  function getDefaultSidebarWidth(): number {
    const bodyWidth = bodyRef.current?.getBoundingClientRect().width ?? 0;
    if (bodyWidth <= 0) {
      return TREE_SIDEBAR_MIN_WIDTH;
    }
    return clampSidebarWidth(bodyWidth / 3);
  }

  function clampSidebarWidth(nextWidth: number): number {
    return Math.max(TREE_SIDEBAR_MIN_WIDTH, Math.min(nextWidth, getMaxSidebarWidth()));
  }

  function applySidebarWidth(nextWidth: number) {
    const sidebarElement = sidebarRef.current;
    if (!sidebarElement) {
      return;
    }
    sidebarElement.style.width = `${clampSidebarWidth(nextWidth)}px`;
  }

  function endSidebarResize(target: HTMLDivElement, pointerId: number) {
    if (target.hasPointerCapture(pointerId)) {
      target.releasePointerCapture(pointerId);
    }
    resizeRef.current = null;
    setIsSidebarResizing(false);
  }

  function handleSidebarResizeStart(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    resizeRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      width: sidebarRef.current?.getBoundingClientRect().width ?? getDefaultSidebarWidth(),
    };
    setIsSidebarResizing(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handleSidebarResizeMove(event: React.PointerEvent<HTMLDivElement>) {
    const resize = resizeRef.current;
    if (!resize || resize.pointerId !== event.pointerId) {
      return;
    }
    applySidebarWidth(resize.width + resize.x - event.clientX);
  }

  function handleSidebarResizeEnd(event: React.PointerEvent<HTMLDivElement>) {
    if (!resizeRef.current || resizeRef.current.pointerId !== event.pointerId) {
      return;
    }
    endSidebarResize(event.currentTarget, event.pointerId);
  }

  const treeDataReady = Boolean(selectedTarget) && !isLoading && !loadError && Boolean(resource?.exists);
  return (
    <div className="tree-viewer">
      <header className="tree-viewer__toolbar">
        <div className="tree-viewer__toolbar-section tree-viewer__toolbar-section--tabs">
          <div className="tree-viewer__toolbar-tabs" role="tablist" aria-label="Tree artifacts">
            {artifacts.map((artifact) => {
              const isActive = artifact.id === activeArtifact.id;
              return (
                <button
                  key={artifact.id}
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  className={`tree-viewer__toolbar-tab${isActive ? " is-active" : ""}`}
                  onClick={() => setActiveArtifactId(artifact.id)}
                >
                  {artifact.label}
                </button>
              );
            })}
          </div>
        </div>
        <button
          type="button"
          className="tree-viewer__toolbar-action"
          disabled={!document}
          onClick={() => {
            setRecenterVersion((current) => current + 1);
          }}
        >
          Recenter
        </button>
        <span className="tree-viewer__toolbar-brand">atopile</span>
      </header>
      <div
        ref={bodyRef}
        className={`tree-viewer__body${isSidebarResizing ? " is-resizing" : ""}`}
      >
        <div className="tree-viewer__canvas">
          {treeDataReady && document && document.nodes.length > 0 ? (
            <TreeCanvas
              document={document}
              recenterVersion={recenterVersion}
              selection={selection}
              hoveredElement={hoveredElement}
              onSelect={setSelection}
              onHover={setHoveredElement}
            />
          ) : (
            <NoDataMessage
              icon={<GitBranch size={24} />}
              noun="tree"
              hasSelection={Boolean(selectedTarget)}
              isLoading={isLoading}
              buildInProgress={buildInProgress}
              error={loadError}
              hasData={Boolean(document && document.nodes.length > 0)}
              noDataDescription={`Make sure your design exposes ${activeArtifact.label.toLowerCase()} interfaces.`}
            >
              {null}
            </NoDataMessage>
          )}
        </div>

        <div
          className={`tree-viewer__sidebar-resize-handle${isSidebarResizing ? " is-active" : ""}`}
          role="separator"
          aria-label="Resize inspector"
          aria-orientation="vertical"
          onPointerDown={handleSidebarResizeStart}
          onPointerMove={handleSidebarResizeMove}
          onPointerUp={handleSidebarResizeEnd}
          onPointerCancel={handleSidebarResizeEnd}
        />
        <aside ref={sidebarRef} className="tree-viewer__sidebar">
          <Inspector document={document} selection={selection} />
          <Legend document={document} />
        </aside>
      </div>
    </div>
  );
}

render(App);
