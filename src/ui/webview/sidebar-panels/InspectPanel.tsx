import { useMemo, type CSSProperties } from "react";
import {
  Box,
  CircuitBoard,
  ClipboardList,
  GitBranch,
  GitCompareArrows,
  Grid2x2,
  Layers,
  SlidersHorizontal,
} from "lucide-react";
import { requestPanel } from "../sidebar/sidebarActions";
import { SECTION_HEADER_HEIGHT } from "../common/components";
import "./ViewRow.css";

interface InspectPanelProps {
  disabled: boolean;
}

interface InspectItem {
  id: string;
  label: string;
  description: string;
  icon: React.ReactNode;
  onClick: () => void;
}

export function InspectPanel({ disabled }: InspectPanelProps) {
  const panelStyle = useMemo(() => ({
    ["--sidebar-section-header-height" as "--sidebar-section-header-height"]:
      `${SECTION_HEADER_HEIGHT}px`,
  }) as CSSProperties, []);

  const items = useMemo<InspectItem[]>(
    () => [
      {
        id: "3d",
        label: "3D",
        description: "Open the 3D board viewer.",
        icon: <Box size={16} />,
        onClick: () => void requestPanel("panel-3d"),
      },
      {
        id: "layout",
        label: "Layout",
        description: "Inspect PCB geometry and board placement.",
        icon: <Grid2x2 size={16} />,
        onClick: () => void requestPanel("panel-layout"),
      },
      {
        id: "pinout",
        label: "Pinout",
        description: "Browse nets, interfaces, and pin assignments.",
        icon: <CircuitBoard size={16} />,
        onClick: () => void requestPanel("panel-pinout"),
      },
      {
        id: "parameters",
        label: "Parameters",
        description: "Inspect solved values, specs, and constraint status.",
        icon: <SlidersHorizontal size={16} />,
        onClick: () => void requestPanel("panel-parameters"),
      },
      {
        id: "trees",
        label: "Trees",
        description: "Inspect power and data interface trees.",
        icon: <GitBranch size={16} />,
        onClick: () => void requestPanel("panel-tree"),
      },
      {
        id: "stackup",
        label: "Stackup",
        description: "Inspect layer order, materials, and board thickness.",
        icon: <Layers size={16} />,
        onClick: () => void requestPanel("panel-stackup"),
      },
      {
        id: "diff",
        label: "Diff",
        description: "Compare PCB revisions side by side.",
        icon: <GitCompareArrows size={16} />,
        onClick: () => void requestPanel("panel-pcb-diff"),
      },
      {
        id: "ibom",
        label: "iBOM",
        description: "Open the interactive bill of materials.",
        icon: <ClipboardList size={16} />,
        onClick: () => void requestPanel("panel-ibom"),
      },
    ],
    [],
  );

  return (
    <div className="sidebar-panel sidebar-panel-shell" style={panelStyle}>
      <div className="sidebar-panel-scroll sidebar-panel-shell-scroll">
        <div className="views-list">
          {items.map((item) => (
            <InspectRow
              key={item.id}
              item={item}
              disabled={disabled}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function InspectRow({
  item,
  disabled,
}: {
  item: InspectItem;
  disabled: boolean;
}) {
  return (
    <button
      className="view-row"
      onClick={item.onClick}
      disabled={disabled}
      title={item.description}
    >
      <span className="view-row-mark">
        <span className="view-row-icon">{item.icon}</span>
      </span>
      <span className="view-row-copy">
        <span className="view-row-heading">
          <span className="card-row-name">{item.label}</span>
        </span>
        <span className="card-row-description">{item.description}</span>
      </span>
    </button>
  );
}
