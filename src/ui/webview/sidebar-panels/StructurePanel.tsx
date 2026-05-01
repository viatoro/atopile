import { useState, useMemo, useCallback } from "react";
import {
  GitBranch,
  ChevronRight,
  RefreshCw,
} from "lucide-react";
import {
  Alert,
  AlertDescription,
  AlertTitle,
  EmptyState,
  CenteredSpinner,
  PanelSearchBox,
  TreeRowHeader,
  typeIcon,
} from "../common/components";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import { useToggleSet } from "./hooks";
import { joinPath, parseSrcLoc } from "../../protocol/paths";
import type {
  ModuleChild,
  ModuleDefinition,
} from "../../protocol/generated-types";
import "./StructurePanel.css";

function ChildNode({
  child,
  depth,
  expandedKeys,
  onToggle,
  onGoToSource,
}: {
  child: ModuleChild;
  depth: number;
  expandedKeys: Set<string>;
  onToggle: (key: string) => void;
  onGoToSource: (srcLoc: string | null) => void;
}) {
  const key = `${depth}-${child.name}`;
  const hasChildren = child.children.length > 0;
  const isExpanded = expandedKeys.has(key);

  const handleChevronClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onToggle(key);
  };

  return (
    <div>
      <div
        className="structure-child-row"
        onClick={() => onGoToSource(child.srcLoc)}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
      >
        {hasChildren ? (
          <span className={`structure-chevron${isExpanded ? " expanded" : ""}`} onClick={handleChevronClick}>
            <ChevronRight size={10} />
          </span>
        ) : (
          <span style={{ width: 10 }} />
        )}
        <span className={`type-icon type-${child.itemType}`}>
          {typeIcon(child.itemType)}
        </span>
        <span className="structure-child-name">{child.name}</span>
        {child.spec && <span className="structure-child-spec">{child.spec}</span>}
        <span className="structure-child-type">{child.typeName}</span>
      </div>
      {hasChildren && isExpanded && (
        <div>
          {child.children.map((c) => (
            <ChildNode
              key={c.name}
              child={c}
              depth={depth + 1}
              expandedKeys={expandedKeys}
              onToggle={onToggle}
              onGoToSource={onGoToSource}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ModuleNode({
  module,
  expandedKeys,
  onToggle,
  onGoToSource,
}: {
  module: ModuleDefinition;
  expandedKeys: Set<string>;
  onToggle: (key: string) => void;
  onGoToSource: (srcLoc: string | null) => void;
}) {
  const key = `module-${module.entry}`;
  const isExpanded = expandedKeys.has(key);

  const handleHeaderClick = () => {
    onToggle(key);
    onGoToSource(`${module.file}:${module.line ?? 1}:0`);
  };

  return (
    <div className="structure-module">
      <TreeRowHeader
        isExpandable
        isExpanded={isExpanded}
        onClick={handleHeaderClick}
        icon={(
          <span className={`type-icon type-${module.type}`}>
            {typeIcon(module.type)}
          </span>
        )}
        label={module.name}
        secondaryLabel={module.superType ?? undefined}
        className="structure-module-header"
      />
      {isExpanded && module.children.length > 0 && (
        <div className="structure-children">
          {module.children.map((child) => (
            <ChildNode
              key={child.name}
              child={child}
              depth={0}
              expandedKeys={expandedKeys}
              onToggle={onToggle}
              onGoToSource={onGoToSource}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function StructurePanel({ hideHeader = false }: { hideHeader?: boolean }) {
  const { selectedProjectRoot: projectRoot } = WebviewRpcClient.useSubscribe("projectState");
  const structureData = WebviewRpcClient.useSubscribe("structureData");
  const [search, setSearch] = useState("");
  const [expandedKeys, toggleKey] = useToggleSet();

  const handleRefresh = useCallback(() => {
    if (projectRoot) {
      rpcClient?.sendAction("getStructure", { projectRoot });
    }
  }, [projectRoot]);

  const handleGoToSource = useCallback((srcLoc: string | null) => {
    if (!srcLoc || !projectRoot) return;
    const { file, line, column } = parseSrcLoc(srcLoc);
    void rpcClient?.requestAction("vscode.openFile", {
      path: joinPath(projectRoot, file),
      line,
      column,
    });
  }, [projectRoot]);

  const filtered = useMemo(() => {
    if (!search) return structureData.modules;
    const q = search.toLowerCase();
    return structureData.modules.filter(
      (m) =>
        m.name.toLowerCase().includes(q) ||
        m.type.toLowerCase().includes(q),
    );
  }, [structureData.modules, search]);

  if (!projectRoot) {
    return (
      <EmptyState
        icon={<GitBranch size={24} />}
        title="No project selected"
        description="Select a project to view its structure"
      />
    );
  }

  return (
    <div className="sidebar-panel">
      {!hideHeader ? (
        <div className="sidebar-panel-header">
          <span className="structure-file-path">Symbolic</span>
        </div>
      ) : null}
      <div className="panel-search-toolbar">
        <PanelSearchBox value={search} onChange={setSearch} placeholder="Search modules..." />
        <button className="panel-search-clear" onClick={handleRefresh} title="Refresh structure">
          <RefreshCw size={14} />
        </button>
      </div>
      <div className="sidebar-panel-scroll">
        {structureData.loading ? (
          <CenteredSpinner />
        ) : structureData.error ? (
          <Alert variant="destructive">
            <AlertTitle>Error loading structure</AlertTitle>
            <AlertDescription>{structureData.error}</AlertDescription>
          </Alert>
        ) : filtered.length === 0 ? (
          <EmptyState
            title={search ? "No matches" : "No modules found"}
            description={search ? `No modules match "${search}"` : "No module definitions in this project"}
          />
        ) : (
          filtered.map((mod) => (
            <ModuleNode
              key={mod.entry}
              module={mod}
              expandedKeys={expandedKeys}
              onToggle={toggleKey}
              onGoToSource={handleGoToSource}
            />
          ))
        )}
      </div>
    </div>
  );
}
