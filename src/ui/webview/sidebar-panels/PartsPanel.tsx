import { useEffect, useState } from "react";
import {
  AlertCircle,
  CircleCheck,
  Cpu,
  Download,
  Search,
  Trash2,
} from "lucide-react";
import {
  CenteredSpinner,
  EmptyState,
  PanelSearchBox,
  Spinner,
} from "../common/components";
import type { UiPartData } from "../../protocol/generated-types";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import "./ComponentEntry.css";
import "./PartsPanel.css";

function stockClass(stock: number | null): string {
  if (stock == null) return "";
  if (stock <= 0) return "part-stock-none";
  if (stock < 100) return "part-stock-low";
  return "part-stock-ok";
}

function compactNumber(value: number): string {
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(1).replace(/\.0$/, "")}k`;
  }
  return String(value);
}

function stockLabel(stock: number | null): string {
  return stock == null ? "-" : compactNumber(stock);
}

function openPartDetails(projectRoot: string, part: UiPartData): void {
  rpcClient?.sendAction("showPartDetails", {
    projectRoot,
    identifier: part.identifier,
    lcsc: part.lcsc,
    installed: part.installed,
  });
}

function PartRow({
  part,
  projectRoot,
}: {
  part: UiPartData;
  projectRoot: string;
}) {
  const [justClicked, setJustClicked] = useState(false);
  useEffect(() => {
    if (part.action !== "idle") {
      setJustClicked(false);
    }
  }, [part.action]);
  const isBusy = justClicked || part.action !== "idle";
  const secondarySummary =
    part.stock != null
      ? part.unitCost != null
        ? `${stockLabel(part.stock)} · $${part.unitCost.toFixed(4)}`
        : stockLabel(part.stock)
      : part.lcsc ?? part.identifier;

  return (
    <div
      className="component-entry-row"
      role="button"
      tabIndex={0}
      onClick={() => openPartDetails(projectRoot, part)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openPartDetails(projectRoot, part);
        }
      }}
    >
      <div className="component-entry-mark">
        <Cpu size={16} />
      </div>
      <div className="component-entry-body">
        <div className="component-entry-line">
          <span className="component-entry-title">
            {part.mpn || part.identifier || part.lcsc}
          </span>
          <span className="component-entry-detail">{part.manufacturer}</span>
        </div>
        <div className="component-entry-line">
          <span className="component-entry-description">{part.description}</span>
          <span className={`component-entry-detail ${stockClass(part.stock)}`}>
            {secondarySummary}
          </span>
        </div>
      </div>
      <div className="component-entry-actions">
        {isBusy ? (
          <span className="component-entry-icon-action">
            <Spinner size={14} />
          </span>
        ) : part.installed ? (
          <>
            {part.lcsc ? (
              <button
                type="button"
                className="component-entry-icon-action action-full"
                onClick={(event) => {
                  event.stopPropagation();
                  if (!part.lcsc) return;
                  setJustClicked(true);
                  rpcClient?.sendAction("uninstallPart", {
                    projectRoot,
                    lcsc: part.lcsc,
                  });
                }}
                aria-label="Remove part"
                title="Remove part"
              >
                <Trash2 size={14} />
              </button>
            ) : null}
            <span className="component-entry-installed-badge action-compact" title="Installed">
              <CircleCheck size={16} />
            </span>
          </>
        ) : part.lcsc ? (
          <button
            type="button"
            className="component-entry-icon-action install"
            onClick={(event) => {
              event.stopPropagation();
              if (!part.lcsc) return;
              setJustClicked(true);
              rpcClient?.sendAction("installPart", {
                projectRoot,
                lcsc: part.lcsc,
              });
            }}
            aria-label="Install part"
            title="Install part"
          >
            <Download size={14} />
          </button>
        ) : null}
      </div>
    </div>
  );
}

export function PartsPanel() {
  const { selectedProjectRoot: projectRoot } = WebviewRpcClient.useSubscribe("projectState");
  const partsSearch = WebviewRpcClient.useSubscribe("partsSearch");

  useEffect(() => {
    if (!projectRoot) {
      return;
    }
    rpcClient?.sendAction("searchParts", {
      projectRoot,
      query: partsSearch.query,
      installedOnly: partsSearch.installedOnly,
      limit: 50,
    });
    // The store owns query/filter state; this only hydrates the selected project.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectRoot]);

  if (!projectRoot) {
    return (
      <EmptyState
        icon={<Cpu size={24} />}
        title="No project selected"
        description="Select a project to search and manage parts"
      />
    );
  }

  const hasQuery = Boolean(partsSearch.query.trim());
  const hasResults = partsSearch.parts.length > 0;
  const listError = !partsSearch.loading && partsSearch.error && hasResults
    ? partsSearch.error
    : null;

  let emptyTitle = "No installed parts";
  let emptyDescription = hasQuery
    ? `No installed parts match "${partsSearch.query}"`
    : "Search and install parts to see them here";
  if (partsSearch.installedOnly) {
    emptyTitle = hasQuery ? "No matches" : "No installed parts";
    emptyDescription = hasQuery
      ? `No installed parts match "${partsSearch.query}"`
      : "Search and install parts to see them here";
  } else if (partsSearch.error) {
    emptyTitle = "Search error";
    emptyDescription = partsSearch.error;
  } else if (hasQuery) {
    emptyTitle = "No results";
    emptyDescription = `No parts found for "${partsSearch.query}"`;
  }

  return (
    <div className="sidebar-panel">
      <div className="panel-search-toolbar">
        <PanelSearchBox
          className="no-margin"
          value={partsSearch.query}
          onChange={(query) => {
            rpcClient?.sendAction("searchParts", {
              projectRoot,
              query,
              installedOnly: partsSearch.installedOnly,
              limit: 50,
            });
          }}
          placeholder="Search by MPN, LCSC ID, or description..."
        />
        <button
          type="button"
          className={`panel-search-filter${partsSearch.installedOnly ? " active" : ""}`}
          onClick={() => {
            rpcClient?.sendAction("searchParts", {
              projectRoot,
              query: partsSearch.query,
              installedOnly: !partsSearch.installedOnly,
              limit: 50,
            });
          }}
        >
          Installed only
        </button>
      </div>

      <div className="sidebar-panel-scroll">
        {partsSearch.actionError ? (
          <div className="parts-panel-error">
            <AlertCircle size={14} />
            <span>{partsSearch.actionError}</span>
          </div>
        ) : null}
        {listError ? (
          <div className="parts-panel-error">
            <AlertCircle size={14} />
            <span>{listError}</span>
          </div>
        ) : null}

        {partsSearch.loading ? (
          <CenteredSpinner />
        ) : hasResults ? (
          <div className="component-entry-list">
            {partsSearch.parts.map((part) => (
              <PartRow
                key={part.lcsc?.toUpperCase() ?? `identifier:${part.identifier}`}
                part={part}
                projectRoot={projectRoot}
              />
            ))}
          </div>
        ) : (
          <EmptyState
            icon={hasQuery || partsSearch.installedOnly ? undefined : <Search size={24} />}
            title={emptyTitle}
            description={emptyDescription}
          />
        )}
      </div>
    </div>
  );
}
