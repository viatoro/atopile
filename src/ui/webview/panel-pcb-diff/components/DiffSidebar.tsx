import { useState } from "react";
import { RefreshCw, Plus, Minus, ArrowRight, Pencil, Equal } from "lucide-react";
import { RegexSearchBar } from "../../common/components/SearchBar";
import { Button } from "../../common/components/Button";
import { Spinner } from "../../common/components/Spinner";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "../../common/components/Tooltip";
import { PanelTabs, type PanelTab } from "../../common/components/PanelTabs";
import { SidebarDockPanel, SidebarDockHeader } from "../../common/components/SidebarDockPanel";
import { filterBySearch } from "../../common/utils/searchUtils";
import type { DiffResult, DiffStatus, DiffFilterMode } from "../../common/diff/types";
import { STATUS_CSS_COLORS } from "../../common/diff/types";
import {
    groupElementsByStatus,
    filterElementsByMode,
    elementId,
} from "../../common/diff/diff_state";
import type { DiffElementStatus } from "../../common/diff/types";
import { DiffItemRow } from "./DiffItemRow";

const STATUS_ORDER: DiffStatus[] = ["added", "deleted", "moved", "modified", "unchanged"];

const STATUS_ICONS: Record<DiffStatus, React.ReactNode> = {
    added: <Plus size={10} />,
    deleted: <Minus size={10} />,
    moved: <ArrowRight size={10} />,
    modified: <Pencil size={10} />,
    unchanged: <Equal size={10} />,
};

const FILTER_TABS: PanelTab[] = [
    { key: "components", label: "Components" },
    { key: "traces", label: "Traces" },
    { key: "silkscreen", label: "Silkscreen" },
    { key: "outline", label: "Zones" },
];

interface DiffSidebarProps {
    result: DiffResult;
    filterMode: DiffFilterMode;
    onFilterModeChange: (mode: DiffFilterMode) => void;
    selectedId: string | null;
    onSelectItem: (id: string | null) => void;
    onReload: () => void;
    loading: boolean;
    versionSelector?: React.ReactNode;
}

function itemSortName(el: DiffElementStatus): string {
    return (el.reference || el.name || el.net_name || el.uuid_a || el.uuid_b || "").toLowerCase();
}

function itemSortKey(el: DiffElementStatus, netNames?: Record<number, string>): string {
    if (el.element_type === "track" || el.element_type === "via") {
        const name = el.net_name ?? (el.net != null ? netNames?.[el.net] : null) ?? "";
        return name.toLowerCase();
    }
    return itemSortName(el);
}

function itemSearchText(el: DiffElementStatus): string {
    return [el.reference, el.name, el.value, el.net_name].filter(Boolean).join(" ");
}

function resolveNetName(el: DiffElementStatus, netNames?: Record<number, string>): string {
    return el.net_name ?? (el.net != null ? netNames?.[el.net] : null) ?? "";
}

interface NetSubGroup {
    net: number;
    netName: string;
    elements: DiffElementStatus[];
}

function groupByNet(items: DiffElementStatus[], netNames: Record<number, string>): { netGroups: NetSubGroup[]; rest: DiffElementStatus[] } {
    const byNet = new Map<number, DiffElementStatus[]>();
    const rest: DiffElementStatus[] = [];
    for (const el of items) {
        if ((el.element_type === "track" || el.element_type === "via") && el.net != null) {
            let arr = byNet.get(el.net);
            if (!arr) { arr = []; byNet.set(el.net, arr); }
            arr.push(el);
        } else {
            rest.push(el);
        }
    }
    const netGroups: NetSubGroup[] = [];
    for (const [net, elements] of byNet) {
        const netName = resolveNetName(elements[0]!, netNames) || `Net ${net}`;
        netGroups.push({ net, netName, elements });
    }
    netGroups.sort((a, b) => a.netName.toLowerCase().localeCompare(b.netName.toLowerCase()));
    return { netGroups, rest };
}

export function DiffSidebar({
    result,
    filterMode,
    onFilterModeChange,
    selectedId,
    onSelectItem,
    onReload,
    loading,
    versionSelector,
}: DiffSidebarProps) {
    const [search, setSearch] = useState("");
    const [isRegex, setIsRegex] = useState(false);
    const [caseSensitive, setCaseSensitive] = useState(false);
    const [collapsedGroups, setCollapsedGroups] = useState<Set<DiffStatus>>(new Set(["unchanged"]));
    const [expandedNets, setExpandedNets] = useState<Set<string>>(new Set());

    const filtered = filterElementsByMode(result.elements, filterMode);
    const searched = search
        ? filterBySearch(filtered, search, itemSearchText, { isRegex, caseSensitive })
        : filtered;

    const isTraceMode = filterMode === "traces";
    const grouped = groupElementsByStatus(searched);

    const toggleCollapse = (status: DiffStatus) => {
        setCollapsedGroups((prev) => {
            const next = new Set(prev);
            if (next.has(status)) next.delete(status);
            else next.add(status);
            return next;
        });
    };

    return (
        <div className="pcb-diff-sidebar">
            <div className="pcb-diff-sidebar-header">
                {versionSelector}
                <div className="pcb-diff-header-row">
                    <PanelTabs
                        tabs={FILTER_TABS}
                        activeTab={filterMode}
                        onTabChange={(key) => onFilterModeChange(key as DiffFilterMode)}
                    />
                    <TooltipProvider>
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    onClick={onReload}
                                    disabled={loading}
                                    style={{ flexShrink: 0 }}
                                >
                                    {loading ? <Spinner size={12} /> : <RefreshCw size={12} />}
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent>Recalculate diff</TooltipContent>
                        </Tooltip>
                    </TooltipProvider>
                </div>
                <RegexSearchBar
                    value={search}
                    onChange={setSearch}
                    isRegex={isRegex}
                    onRegexChange={setIsRegex}
                    caseSensitive={caseSensitive}
                    onCaseSensitiveChange={setCaseSensitive}
                    placeholder="Filter items..."
                />
            </div>
            <div className="pcb-diff-sidebar-content">
                {STATUS_ORDER.map((status) => {
                    const items = [...grouped[status]].sort(
                        (a, b) => itemSortKey(a, result.net_names).localeCompare(itemSortKey(b, result.net_names)),
                    );
                    if (items.length === 0) return null;
                    const collapsed = collapsedGroups.has(status);

                    // For traces, sub-group by net
                    const { netGroups, rest } = isTraceMode
                        ? groupByNet(items, result.net_names)
                        : { netGroups: [], rest: items };

                    return (
                        <div key={status} className="pcb-diff-status-group">
                            <SidebarDockPanel collapsed={collapsed}>
                                <SidebarDockHeader
                                    title={status}
                                    badge={items.length}
                                    collapsed={collapsed}
                                    onToggleCollapsed={() => toggleCollapse(status)}
                                />
                                {!collapsed && (
                                    <>
                                        {netGroups.map((ng) => {
                                            const netId = `net:${ng.net}`;
                                            const netKey = `${status}:${ng.net}`;
                                            const netExpanded = expandedNets.has(netKey);
                                            const netSelected = selectedId === netId;
                                            return (
                                                <div key={netKey} className="pcb-diff-net-group">
                                                    <div
                                                        className={`pcb-diff-net-group-header${netSelected ? " selected" : ""}`}
                                                        onClick={() => onSelectItem(netSelected ? null : netId)}
                                                    >
                                                        <span
                                                            className="pcb-diff-net-chevron"
                                                            onClick={(e) => {
                                                                e.stopPropagation();
                                                                setExpandedNets((prev) => {
                                                                    const next = new Set(prev);
                                                                    if (next.has(netKey)) next.delete(netKey);
                                                                    else next.add(netKey);
                                                                    return next;
                                                                });
                                                            }}
                                                        >
                                                            {netExpanded ? "\u25BE" : "\u25B8"}
                                                        </span>
                                                        <span
                                                            className="pcb-diff-item-dot"
                                                            style={{ background: STATUS_CSS_COLORS[status] }}
                                                        />
                                                        <span className="pcb-diff-item-label">{ng.netName}</span>
                                                        <span className="pcb-diff-item-type">
                                                            {ng.elements.length}
                                                        </span>
                                                    </div>
                                                    {netExpanded && ng.elements.map((el) => {
                                                        const id = elementId(el);
                                                        return (
                                                            <DiffItemRow
                                                                key={id}
                                                                item={el}
                                                                selected={selectedId === id}
                                                                onClick={() =>
                                                                    onSelectItem(selectedId === id ? null : id)
                                                                }
                                                                netNames={result.net_names}
                                                            />
                                                        );
                                                    })}
                                                </div>
                                            );
                                        })}
                                        {rest.map((el) => {
                                            const id = elementId(el);
                                            return (
                                                <DiffItemRow
                                                    key={id}
                                                    item={el}
                                                    selected={selectedId === id}
                                                    onClick={() =>
                                                        onSelectItem(selectedId === id ? null : id)
                                                    }
                                                    netNames={result.net_names}
                                                />
                                            );
                                        })}
                                    </>
                                )}
                            </SidebarDockPanel>
                        </div>
                    );
                })}
            </div>
            <div className="pcb-diff-sidebar-footer">
                <ul className="pcb-diff-summary-list">
                    {STATUS_ORDER.map((status) => (
                        <li key={status} title={status}>
                            <span style={{ color: STATUS_CSS_COLORS[status], display: "flex", alignItems: "center" }}>
                                {STATUS_ICONS[status]}
                            </span>
                            <span style={{ color: STATUS_CSS_COLORS[status], fontWeight: 600, opacity: status === "unchanged" ? 0.7 : 1 }}>
                                {result.summary[status] ?? 0}
                            </span>
                        </li>
                    ))}
                </ul>
            </div>
        </div>
    );
}
