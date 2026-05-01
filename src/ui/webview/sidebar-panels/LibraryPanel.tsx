import { useState, useCallback, useEffect, useMemo } from "react";
import { ChevronRight, Library } from "lucide-react";
import { useWaitFlag } from "../common/hooks/useWaitFlag";
import {
  EmptyState,
  CenteredSpinner,
  PanelSearchBox,
  CopyableCodeBlock,
  ResizableSectionStack,
  typeIcon,
} from "../common/components";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import type { StdLibChild, StdLibItem } from "../../protocol/generated-types";
import "./ComponentEntry.css";
import "./LibraryPanel.css";

const TYPE_ORDER = ["component", "module", "interface", "trait", "parameter"] as const;
type TypeKey = (typeof TYPE_ORDER)[number];

const TYPE_LABELS: Record<string, string> = {
  interface: "Interfaces",
  module: "Modules",
  component: "Components",
  trait: "Traits",
  parameter: "Parameters",
};

const LIBRARY_GROUP_HEADER_HEIGHT = 26;

function ChildRow({ child, depth }: { child: StdLibChild; depth: number }) {
  const [expanded, setExpanded] = useState(false);
  const hasChildren = child.children.length > 0 && depth < 3;

  return (
    <div className="library-child-node">
      <div
        className={`library-child-row${hasChildren ? " library-child-expandable" : ""}`}
        style={{ paddingLeft: `${depth * 16}px` }}
        onClick={hasChildren ? () => setExpanded(!expanded) : undefined}
        role={hasChildren ? "button" : undefined}
        tabIndex={hasChildren ? 0 : undefined}
        onKeyDown={hasChildren ? (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setExpanded(!expanded);
          }
        } : undefined}
      >
        {hasChildren ? (
          <span className={`library-child-chevron${expanded ? " expanded" : ""}`}>
            <ChevronRight size={10} />
          </span>
        ) : (
          <span className="library-child-chevron-spacer" />
        )}
        <span className={`type-icon type-${child.itemType}`}>
          {typeIcon(child.itemType)}
        </span>
        <span className="library-child-name">{child.name}</span>
        <span className="library-child-type">{child.type}</span>
      </div>
      {expanded && hasChildren && (
        <ChildTree children={child.children} depth={depth + 1} />
      )}
    </div>
  );
}

function ChildTree({ children, depth = 0 }: { children: StdLibChild[]; depth?: number }) {
  if (!children.length) return null;
  return (
    <div className="library-children">
      {children.map((child) => (
        <ChildRow key={child.name} child={child} depth={depth} />
      ))}
    </div>
  );
}

function LibraryItem({ item }: { item: StdLibItem }) {
  const [expanded, setExpanded] = useState(false);
  const childCount = item.children.length;

  return (
    <div className="library-item">
      <button
        type="button"
        className="component-entry-row component-entry-row-expandable library-item-header"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        <span className="component-entry-mark">
          <span className={`type-icon type-${item.type}`}>
            {typeIcon(item.type)}
          </span>
        </span>
        <span className="component-entry-body">
          <span className="component-entry-line">
            <span className="component-entry-title">{item.name}</span>
            <span className="component-entry-detail">{TYPE_LABELS[item.type] ?? item.type}</span>
          </span>
          <span className="component-entry-line">
            <span className="component-entry-description">
              {item.description || "Standard library item"}
            </span>
            <span className="component-entry-detail">
              {childCount > 0 ? `${childCount} member${childCount === 1 ? "" : "s"}` : "Open"}
            </span>
          </span>
        </span>
        <span className={`component-entry-chevron${expanded ? " expanded" : ""}`}>
          <ChevronRight size={14} />
        </span>
      </button>
      {expanded && (
        <div className="library-item-detail">
          {item.usage && (
            <CopyableCodeBlock code={item.usage} label="Usage" highlightAto />
          )}
          {item.children.length > 0 && <ChildTree children={item.children} />}
        </div>
      )}
    </div>
  );
}

function LibraryGroupContent({ items }: { items: StdLibItem[] }) {
  return (
    <div className="library-group-scroll">
      <div className="component-entry-list library-group-items">
        {items.map((item) => (
          <LibraryItem key={item.id} item={item} />
        ))}
      </div>
    </div>
  );
}

const DEFAULT_EXPANDED: Record<TypeKey, boolean> = {
  component: true,
  module: true,
  interface: true,
  trait: true,
  parameter: true,
};

const DEFAULT_HEIGHTS: Record<TypeKey, number> = {
  component: 240,
  module: 240,
  interface: 240,
  trait: 240,
  parameter: 240,
};

export function LibraryPanel() {
  const stdlibData = WebviewRpcClient.useSubscribe("stdlibData");
  const [search, setSearch] = useState("");
  const [loading, raiseLoading] = useWaitFlag([stdlibData.items], 3000);
  const [expanded, setExpanded] = useState(DEFAULT_EXPANDED);
  const [targetHeights, setTargetHeights] = useState(DEFAULT_HEIGHTS);

  const toggleSection = useCallback((key: TypeKey) => {
    setExpanded((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  useEffect(() => {
    rpcClient?.sendAction("getStdlib", {});
    raiseLoading();
  }, [raiseLoading]);

  const filtered = useMemo(() => {
    if (!search) return stdlibData.items;
    const q = search.toLowerCase();
    return stdlibData.items.filter(
      (item) =>
        item.name.toLowerCase().includes(q) ||
        item.description.toLowerCase().includes(q),
    );
  }, [stdlibData.items, search]);

  const grouped = useMemo(() => {
    const groups = new Map<string, StdLibItem[]>();
    for (const item of filtered) {
      const key = item.type;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(item);
    }
    return groups;
  }, [filtered]);

  const activeTypes = useMemo(
    () => TYPE_ORDER.filter((t) => grouped.has(t)),
    [grouped],
  );

  const sections = useMemo(
    () =>
      activeTypes.map((type) => ({
        key: type,
        label: TYPE_LABELS[type] ?? type,
        icon: typeIcon(type),
        expanded: expanded[type],
        content: <LibraryGroupContent items={grouped.get(type)!} />,
      })),
    [activeTypes, expanded, grouped],
  );

  if (loading && stdlibData.items.length === 0) {
    return <CenteredSpinner />;
  }

  if (stdlibData.items.length === 0) {
    return (
      <EmptyState
        icon={<Library size={24} />}
        title="No library items"
        description="Standard library could not be loaded"
      />
    );
  }

  return (
    <div className="sidebar-panel library-panel-root">
      <PanelSearchBox value={search} onChange={setSearch} placeholder="Search library..." />
      {filtered.length === 0 ? (
        <EmptyState title="No matches" description={`No items match "${search}"`} />
      ) : (
        <ResizableSectionStack
          sections={sections}
          onToggleSection={toggleSection}
          targetHeights={targetHeights}
          onTargetHeightsChange={setTargetHeights}
          headerHeight={LIBRARY_GROUP_HEADER_HEIGHT}
          panelClassName="library-stack-shell"
          stackClassName="library-stack"
          sectionClassName="library-stack-section"
          headerClassName="library-group-toggle"
          chevronClassName="library-group-chevron"
          copyClassName="library-group-copy"
          iconClassName="library-group-icon"
          headerVariant="icon-rail"
        />
      )}
    </div>
  );
}
