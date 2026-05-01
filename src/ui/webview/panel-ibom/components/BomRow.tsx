import { memo } from "react";
import { AlertTriangle } from "lucide-react";
import { getTypeLabel } from "../../common/utils/format";
import { TYPE_COLORS, DEFAULT_COLOR } from "../utils/colors";
import type { BomGroup } from "../utils/types";

interface BomRowProps {
  group: BomGroup;
  selected: boolean;
  hovered: boolean;
  onClick: () => void;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
}

/** Truncate designator list: "C1, C2 ... C100" */
function truncateDesignators(members: BomGroup["members"], maxLen = 28): string {
  if (members.length === 0) return "";
  if (members.length === 1) return members[0]!.designator;

  const first = members[0]!.designator;
  const last = members[members.length - 1]!.designator;
  const full = members.map((m) => m.designator).join(", ");
  if (full.length <= maxLen) return full;
  return `${first} \u2026 ${last}`;
}

export const BomRow = memo(function BomRow({ group, selected, hovered, onClick, onMouseEnter, onMouseLeave }: BomRowProps) {
  const bom = group.bomComponent;
  const typeLabel = getTypeLabel(bom?.type);
  const isOutOfStock = bom?.stock != null && bom.stock <= 0;
  const displayValue = group.value || bom?.mpn || group.footprintName || "-";
  const [r, g, b] = TYPE_COLORS[bom?.type ?? ""] ?? DEFAULT_COLOR;
  const badgeBg = `rgba(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)}, 0.2)`;
  const badgeColor = `rgb(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)})`;

  return (
    <div
      className={`ibom-row${selected ? " selected" : ""}${hovered ? " hovered" : ""}`}
      onClick={onClick}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
    >
      <span className="ibom-row-qty">{group.quantity}</span>
      {typeLabel ? (
        <span className="ibom-type-badge" style={{ background: badgeBg, color: badgeColor }}>
          {typeLabel}
        </span>
      ) : (
        <span className="ibom-type-badge-spacer" />
      )}
      <span className="ibom-row-designators" title={group.members.map((m) => m.designator).join(", ")}>
        {truncateDesignators(group.members)}
      </span>
      <span className="ibom-row-value" title={displayValue}>
        {displayValue}
      </span>
      {isOutOfStock && (
        <span className="ibom-row-stock-warning" title="Out of stock">
          <AlertTriangle size={12} />
        </span>
      )}
    </div>
  );
});
