import type { DiffElementStatus } from "../../common/diff/types";
import { STATUS_CSS_COLORS } from "../../common/diff/types";

interface DiffItemRowProps {
    item: DiffElementStatus;
    selected: boolean;
    onClick: () => void;
    netNames?: Record<number, string>;
}

function shortUuid(uuid: string): string {
    return uuid.split("-")[0] ?? uuid;
}

function itemLabel(item: DiffElementStatus, netNames?: Record<number, string>): string {
    if (item.reference) {
        return item.value ? `${item.reference} (${item.value})` : item.reference;
    }
    if (item.name) return item.name;
    if (item.element_type === "track" || item.element_type === "via") {
        const uid = shortUuid(item.uuid_a || item.uuid_b || "");
        const netName = item.net_name ?? (item.net != null ? netNames?.[item.net] : null) ?? null;
        return netName ? `${netName} (${uid})` : uid || item.element_type;
    }
    if (item.net_name) return item.net_name;
    return item.uuid_a || item.uuid_b || item.element_type;
}

export function DiffItemRow({ item, selected, onClick, netNames }: DiffItemRowProps) {
    return (
        <div
            className={`pcb-diff-item-row${selected ? " selected" : ""}`}
            onClick={onClick}
        >
            <span
                className="pcb-diff-item-dot"
                style={{ background: STATUS_CSS_COLORS[item.status] }}
            />
            <span className="pcb-diff-item-label">{itemLabel(item, netNames)}</span>
            <span className="pcb-diff-item-type">{item.element_type}</span>
        </div>
    );
}
