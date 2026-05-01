export interface TreeNodePosition {
  x: number;
  y: number;
  z: number;
  width: number;
  height: number;
}

export interface TreeBounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export interface TreeNodeStyle {
  id: string;
  label?: string;
  color: string;
}

export interface TreeEdgeStyle {
  id: string;
  color: string;
}

export interface TreeLegendItem {
  kind: "node" | "edge";
  styleId: string;
  label: string;
  color?: string;
}

export interface TreeMetaEntry {
  label: string;
  value: string;
  preview: boolean;
  alternateValue?: string | null;
}

export interface TreeNode {
  id: string;
  type: string;
  label: string;
  meta?: Record<string, TreeMetaEntry>;
  groupId?: string;
  groupLabel?: string;
}

export interface TreeEdge {
  id: string;
  source: string;
  target: string;
  kind?: string;
  label?: string;
  detail?: string;
  meta?: Record<string, TreeMetaEntry>;
}

export interface TreeGroup {
  id: string;
  label: string;
  memberIds: string[];
  accentKind: string;
}

export interface TreeLayout {
  positions: Record<string, TreeNodePosition>;
  bounds: TreeBounds;
}

export interface TreeViewerDocument {
  title?: string;
  icon?: string;
  nodeStyles?: TreeNodeStyle[];
  edgeStyles?: TreeEdgeStyle[];
  legend?: TreeLegendItem[];
  nodes: TreeNode[];
  edges: TreeEdge[];
  groups: TreeGroup[];
}
