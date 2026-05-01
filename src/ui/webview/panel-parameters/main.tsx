import { useState, useEffect, useMemo, useCallback } from "react";
import {
  SlidersHorizontal,
  AlertCircle,
  CheckCircle2,
  HelpCircle,
} from "lucide-react";
import {
  EmptyState,
  NoDataMessage,
  PanelSearchBox,
  TreeRowHeader,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "../common/components";
import { render } from "../common/render";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import type {
  UiVariable,
  UiVariableNode,
} from "../../protocol/generated-types";
import "./ParametersPanel.css";

function StatusIcon({ meetsSpec }: { meetsSpec: boolean | null }) {
  switch (meetsSpec) {
    case true:
      return <CheckCircle2 size={12} className="param-status-ok" />;
    case false:
      return <AlertCircle size={12} className="param-status-error" />;
    default:
      return <HelpCircle size={12} className="param-status-unknown" />;
  }
}

function VariableTable({ variables }: { variables: UiVariable[] }) {
  if (variables.length === 0) return null;
  return (
    <Table className="parameters-table">
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Spec</TableHead>
          <TableHead>Actual</TableHead>
          <TableHead></TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {variables.map((v) => (
          <TableRow key={v.name}>
            <TableCell className="param-name" title={v.name}>{v.name}</TableCell>
            <TableCell className="param-spec" title={v.spec ?? "-"}>{v.spec ?? "-"}</TableCell>
            <TableCell className="param-actual" title={v.actual ?? "-"}>{v.actual ?? "-"}</TableCell>
            <TableCell><StatusIcon meetsSpec={v.meetsSpec} /></TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function VariableNodeTree({
  node,
  expandedKeys,
  onToggle,
  depth,
  search,
}: {
  node: UiVariableNode;
  expandedKeys: Set<string>;
  onToggle: (key: string) => void;
  depth: number;
  search: string;
}) {
  const key = `${depth}-${node.name}`;
  const isExpanded = expandedKeys.has(key) || search.length > 0;
  const totalVars = node.variables.length + node.children.reduce(
    (sum, c) => sum + c.variables.length, 0,
  );

  return (
    <div className="parameters-node" style={{ paddingLeft: depth > 0 ? 'var(--spacing-md)' : undefined }}>
      <TreeRowHeader
        isExpandable
        isExpanded={isExpanded}
        onClick={() => onToggle(key)}
        label={node.name}
        count={totalVars}
      />
      {isExpanded && (
        <div className="parameters-children">
          <VariableTable variables={node.variables} />
          {node.children.map((child) => (
            <VariableNodeTree
              key={child.name}
              node={child}
              expandedKeys={expandedKeys}
              onToggle={onToggle}
              depth={depth + 1}
              search={search}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function filterNodes(nodes: UiVariableNode[], search: string): UiVariableNode[] {
  if (!search) return nodes;
  const q = search.toLowerCase();
  const matchesValue = (value: string | null): boolean =>
    value?.toLowerCase().includes(q) === true;
  return nodes
    .map((node) => {
      const matchingVars = node.variables.filter(
        (v) =>
          v.name.toLowerCase().includes(q) ||
          matchesValue(v.spec) ||
          matchesValue(v.actual),
      );
      const matchingChildren = filterNodes(node.children, search);
      if (matchingVars.length === 0 && matchingChildren.length === 0) return null;
      return { ...node, variables: matchingVars, children: matchingChildren };
    })
    .filter(Boolean) as UiVariableNode[];
}

function ParametersPanel() {
  const projectState = WebviewRpcClient.useSubscribe("projectState");
  const variablesData = WebviewRpcClient.useSubscribe("variablesData");
  const currentBuilds = WebviewRpcClient.useSubscribe("currentBuilds");
  const selectedBuildInProgress = WebviewRpcClient.useSubscribe("selectedBuildInProgress");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (projectState.selectedProjectRoot) {
      setLoading(true);
      rpcClient?.sendAction("getVariables", {
        projectRoot: projectState.selectedProjectRoot,
        target: projectState.selectedTarget,
      });
    }
  }, [projectState.selectedProjectRoot, projectState.selectedTarget]);

  // Refresh after build completes
  useEffect(() => {
    if (
      projectState.selectedProjectRoot &&
      currentBuilds.every((b) => b.status !== "building" && b.status !== "queued")
    ) {
      rpcClient?.sendAction("getVariables", {
        projectRoot: projectState.selectedProjectRoot,
        target: projectState.selectedTarget,
      });
    }
  }, [currentBuilds, projectState.selectedProjectRoot, projectState.selectedTarget]);

  useEffect(() => {
    setLoading(false);
  }, [variablesData.nodes]);

  const toggleKey = useCallback((key: string) => {
    setExpandedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const filtered = useMemo(
    () => filterNodes(variablesData.nodes, search),
    [variablesData.nodes, search],
  );

  return (
    <NoDataMessage
      icon={<SlidersHorizontal size={24} />}
      noun="parameter"
      hasSelection={Boolean(projectState.selectedProjectRoot)}
      isLoading={loading}
      buildInProgress={selectedBuildInProgress}
      error={null}
      hasData={variablesData.nodes.length > 0}
    >
      <div className="sidebar-panel parameters-panel">
        <PanelSearchBox value={search} onChange={setSearch} placeholder="Search parameters..." />
        <div className="sidebar-panel-scroll">
          {filtered.length === 0 ? (
            <EmptyState title="No matches" description={`No parameters match "${search}"`} />
          ) : (
            filtered.map((node) => (
              <VariableNodeTree
                key={node.name}
                node={node}
                expandedKeys={expandedKeys}
                onToggle={toggleKey}
                depth={0}
                search={search}
              />
            ))
          )}
        </div>
      </div>
    </NoDataMessage>
  );
}

function App() {
  return <ParametersPanel />;
}

render(App);
