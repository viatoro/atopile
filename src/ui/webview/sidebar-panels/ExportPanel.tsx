import { useMemo, type CSSProperties } from "react";
import {
  AlertCircle,
  Factory,
  Sparkles,
} from "lucide-react";

import { Badge, Spinner, KicadIcon, AltiumIcon, CadenceIcon, XpeditionIcon } from "../common/components";
import { SECTION_HEADER_HEIGHT } from "../common/components";
import "./ViewRow.css";

interface ExportPanelProps {
  disabled: boolean;
  kicadOpening: boolean;
  kicadError?: string | null;
  onOpenKicad: () => void;
  onOpenManufacture: () => void;
}

interface ExportItem {
  id: string;
  label: string;
  description: string;
  icon: React.ReactNode;
  onClick: () => void;
  pro?: boolean;
  muted?: boolean;
}

export function ExportPanel({
  disabled,
  kicadOpening,
  kicadError,
  onOpenKicad,
  onOpenManufacture,
}: ExportPanelProps) {
  const panelStyle = useMemo(() => ({
    ["--sidebar-section-header-height" as "--sidebar-section-header-height"]:
      `${SECTION_HEADER_HEIGHT}px`,
  }) as CSSProperties, []);

  const items = useMemo<ExportItem[]>(
    () => [
      {
        id: "manufacture",
        label: "Export for Manufacturing",
        description: "Review build outputs and manufacturing files.",
        icon: <Factory size={20} />,
        onClick: onOpenManufacture,
      },
      {
        id: "kicad",
        label: kicadError ? "Open in KiCad Failed" : "Open in KiCad",
        description: kicadError
          ? kicadError
          : kicadOpening
            ? "Opening KiCad..."
            : "Opens PCB in local KiCad.",
        icon: kicadError ? (
          <AlertCircle size={20} />
        ) : kicadOpening ? (
          <Spinner size={20} />
        ) : (
          <KicadIcon size={20} />
        ),
        onClick: onOpenKicad,
      },
      {
        id: "altium",
        label: "Open in Altium",
        description: "Opens PCB in local Altium.",
        icon: <AltiumIcon size={20} />,
        onClick: () => {},
        pro: true,
        muted: true,
      },
      {
        id: "cadence",
        label: "Open in Cadence",
        description: "Opens PCB in local Cadence.",
        icon: <CadenceIcon size={20} />,
        onClick: () => {},
        pro: true,
        muted: true,
      },
      {
        id: "xpedition",
        label: "Open in Xpedition",
        description: "Opens PCB in local Xpedition.",
        icon: <XpeditionIcon size={20} />,
        onClick: () => {},
        pro: true,
        muted: true,
      },
    ],
    [kicadError, kicadOpening, onOpenKicad, onOpenManufacture],
  );

  return (
    <div className="sidebar-panel sidebar-panel-shell" style={panelStyle}>
      <div className="sidebar-panel-scroll sidebar-panel-shell-scroll">
        <div className="views-list">
          {items.map((item) => (
            <ExportRow
              key={item.id}
              item={item}
              disabled={disabled}
              busy={item.id === "kicad" && kicadOpening}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function ExportRow({
  item,
  disabled,
  busy,
}: {
  item: ExportItem;
  disabled: boolean;
  busy: boolean;
}) {
  const isDisabled = disabled || busy || item.muted;

  return (
    <button
      className={`view-row${item.muted ? " view-row-muted" : ""}`}
      onClick={item.onClick}
      disabled={isDisabled}
      title={item.description}
    >
      <span className={`view-row-mark${item.pro ? " view-row-mark-brand" : ""}`}>
        <span className="view-row-icon">{item.icon}</span>
      </span>
      <span className="view-row-copy">
        <span className="view-row-heading">
          <span className="card-row-name">{item.label}</span>
          {item.pro ? (
            <Badge variant="outline" className="view-row-pro-badge">
              <Sparkles size={12} />
              Pro
            </Badge>
          ) : null}
        </span>
        <span className="card-row-description">{item.description}</span>
      </span>
    </button>
  );
}
