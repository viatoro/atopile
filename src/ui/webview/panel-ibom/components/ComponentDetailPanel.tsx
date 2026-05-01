import { memo } from "react";
import { FileText } from "lucide-react";
import { Badge, Table, TableBody, TableRow, TableCell } from "../../common/components";
import { formatCurrency, formatStock } from "../../common/utils/format";
import { rpcClient } from "../../common/webviewRpcClient";
import type { BomGroup, BomGroupMember } from "../utils/types";

interface ComponentDetailPanelProps {
  group: BomGroup | null;
  activeDesignator: string | null;
  onDesignatorClick: (member: BomGroupMember) => void;
  onSelectAll: () => void;
}

function sourceVariant(source: string): "default" | "info" | "success" | "secondary" {
  switch (source) {
    case "picked": return "info";
    case "specified": return "success";
    default: return "secondary";
  }
}

const DASH = <span className="ibom-detail-dash">-</span>;

export const ComponentDetailPanel = memo(function ComponentDetailPanel({
  group,
  activeDesignator,
  onDesignatorClick,
  onSelectAll,
}: ComponentDetailPanelProps) {
  const bom = group?.bomComponent ?? null;

  const rows: { label: string; value: React.ReactNode }[] = [
    { label: "Value", value: group?.value || DASH },
    { label: "MPN", value: bom?.mpn ? <span className="mono">{bom.mpn}</span> : DASH },
    { label: "Manufacturer", value: bom?.manufacturer || DASH },
    { label: "Package", value: group?.package || DASH },
    { label: "LCSC", value: bom?.lcsc ? <span className="mono">{bom.lcsc}</span> : DASH },
    { label: "Unit cost", value: bom?.unitCost != null ? <span className="ibom-cost">{formatCurrency(bom.unitCost)}</span> : DASH },
    { label: "Stock", value: bom?.stock != null ? <span className={bom.stock > 0 ? "ibom-stock-ok" : "ibom-stock-warning"}>{formatStock(bom.stock)}</span> : DASH },
    { label: "Source", value: bom?.source ? <Badge variant={sourceVariant(bom.source)}>{bom.source}</Badge> : DASH },
    { label: "Datasheet", value: bom?.datasheet ? (
      <button
        className="ibom-datasheet-link"
        onClick={() => void rpcClient?.requestAction("vscode.openInPanel", { url: bom.datasheet })}
        title={bom.datasheet}
      >
        <FileText size={12} /> Open
      </button>
    ) : DASH },
  ];

  return (
    <div className="ibom-detail">
      <Table className="ibom-detail-table">
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.label}>
              <TableCell className="ibom-detail-label">{row.label}</TableCell>
              <TableCell>{row.value}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      {group && (
        <div className="ibom-detail-members">
          <div className="ibom-detail-members-list">
            {group.quantity > 1 && (
              <button
                className={`ibom-member-btn${activeDesignator === null ? " selected" : ""}`}
                onClick={onSelectAll}
                title="Highlight all on PCB"
              >
                All ({group.quantity})
              </button>
            )}
            {group.members.map((member) => (
              <button
                key={member.designator}
                className={`ibom-member-btn${activeDesignator === member.designator ? " selected" : ""}`}
                onClick={() => onDesignatorClick(member)}
                title={`Highlight ${member.designator} on PCB`}
              >
                {member.designator}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
});
