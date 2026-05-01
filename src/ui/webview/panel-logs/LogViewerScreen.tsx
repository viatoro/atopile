import type { CSSProperties, ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../common/components/Select";
import { SearchBar } from "../common/components/SearchBar";
import { Button } from "../common/components/Button";
import { AlertCircle, Settings, Trash2 } from "lucide-react";
import { Spinner } from "../common/components";
import type { Build } from "../../protocol/generated-types";
import { STATUS_ICONS, formatDuration } from "../common/utils";
import { BuildStageList } from "./BuildStageList";
import type { SourceMode, TimeMode, TreeNode } from "../../protocol/types";
import { LEVEL_SHORT } from "../../protocol/types";
import type {
  UiAudience,
  UiLogEntry,
  UiLogLevel,
} from "../../protocol/generated-types";
import type { SearchOptions } from "../common/utils/searchUtils";
import {
  ansiConverter,
  computeRowDisplay,
  countDescendants,
  filterByLoggers,
  filterLogs,
  getUniqueTopLevelLoggers,
  groupLogsIntoTrees,
  initLogSettings,
  isSeparatorLine,
  loadEnabledLoggers,
  saveEnabledLoggers,
  tryParseStructuredTraceback,
} from "./logUtils";
import { StackInspector } from "./StackInspector";
import { AtoTraceback, parseAtoTraceback } from "./AtoTraceback";
import {
  createLogRequest,
  LogRpcClient,
  useLogState,
  type LogTarget,
} from "./logRpcClient";
import { rpcClient } from "../common/webviewRpcClient";
import "./LogViewer.css";


initLogSettings();

function DeleteLogsButton() {
  const [confirming, setConfirming] = useState(false);
  useEffect(() => {
    if (!confirming) return;
    const timer = window.setTimeout(() => setConfirming(false), 3000);
    return () => window.clearTimeout(timer);
  }, [confirming]);

  if (confirming) {
    return (
      <div className="lv-clear-logs-confirm-wrap">
        <span className="lv-clear-logs-warning">This will delete all build logs and chat history. This cannot be undone.</span>
        <button
          className="lv-clear-logs-btn lv-clear-logs-confirm"
          onClick={() => {
            rpcClient?.sendAction("clearLogs");
            setConfirming(false);
          }}
        >
          <Trash2 size={12} />
          <span>Confirm delete</span>
        </button>
      </div>
    );
  }

  return (
    <button
      className="lv-clear-logs-btn"
      onClick={() => setConfirming(true)}
    >
      <Trash2 size={12} />
      <span>Delete logs</span>
    </button>
  );
}

function getScopeLabel(mode: LogTarget["mode"]): string {
  return mode === "test" ? "Test" : "Stage";
}

function getScopeValue(entry: UiLogEntry, mode: LogTarget["mode"]): string | null {
  return mode === "test" ? entry.testName : entry.stage;
}

function LogRowCells({
  entry,
  display,
  scopeMode,
  levelFull,
  toggleHandlers,
}: {
  entry: UiLogEntry;
  display: ReturnType<typeof computeRowDisplay>;
  scopeMode: LogTarget["mode"];
  levelFull: boolean;
  toggleHandlers: {
    toggleTimeMode: () => void;
    toggleLevelWidth: () => void;
    toggleSourceMode: () => void;
  };
}) {
  const scopeValue = getScopeValue(entry, scopeMode);

  return (
    <>
      <span
        className="lv-ts"
        onClick={toggleHandlers.toggleTimeMode}
        title="Click: toggle format"
      >
        {display.ts}
      </span>
      <span
        className={`lv-level-badge ${entry.level.toLowerCase()} ${
          levelFull ? "" : "short"
        }`}
        onClick={toggleHandlers.toggleLevelWidth}
        title="Click: toggle short/full"
      >
        {levelFull ? entry.level : LEVEL_SHORT[entry.level]}
      </span>
      <span
        className="lv-source-badge"
        title={display.sourceTooltip}
        onClick={toggleHandlers.toggleSourceMode}
        style={display.sourceStyle}
      >
        {display.sourceDisplayValue}
      </span>
      <span className="lv-stage-badge" title={scopeValue || ""}>
        {scopeValue || "\u2014"}
      </span>
    </>
  );
}

function TraceDetails({
  label,
  content,
  className,
  defaultOpen = false,
}: {
  label: string;
  content: string;
  className: string;
  defaultOpen?: boolean;
}) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  return (
    <div className={`lv-trace ${className}`}>
      <button className="lv-trace-summary" onClick={() => setIsOpen(!isOpen)}>
        <span className={`lv-trace-arrow ${isOpen ? "open" : ""}`}>
          &#x25B8;
        </span>
        {label}
      </button>
      {isOpen && (
        <pre
          className="lv-trace-content"
          dangerouslySetInnerHTML={{ __html: ansiConverter.toHtml(content) }}
        />
      )}
    </div>
  );
}

function CollapsibleStackTrace({
  traceback,
}: {
  traceback: Parameters<typeof StackInspector>[0]["traceback"];
}) {
  const [isOpen, setIsOpen] = useState(false);
  return (
    <div className="lv-trace lv-trace-python">
      <button className="lv-trace-summary" onClick={() => setIsOpen(!isOpen)}>
        <span className={`lv-trace-arrow ${isOpen ? "open" : ""}`}>
          &#x25B8;
        </span>
        python traceback
      </button>
      {isOpen && <StackInspector traceback={traceback} />}
    </div>
  );
}

function TracebacksInline({ entry }: { entry: UiLogEntry }) {
  if (!entry.atoTraceback && !entry.pythonTraceback) return null;

  const atoTb = parseAtoTraceback(entry.atoTraceback);
  const structuredTb = tryParseStructuredTraceback(entry.pythonTraceback);

  return (
    <div className="lv-tracebacks lv-tracebacks-inline">
      {atoTb && <AtoTraceback traceback={atoTb} />}
      {structuredTb && structuredTb.frames.length > 0 ? (
        <CollapsibleStackTrace traceback={structuredTb} />
      ) : entry.pythonTraceback ? (
        <TraceDetails
          label="python traceback"
          content={entry.pythonTraceback}
          className="lv-trace-python"
        />
      ) : null}
    </div>
  );
}

function TreeNodeRow({
  node,
  search,
  searchOptions,
  levelFull,
  timeMode,
  sourceMode,
  firstTimestamp,
  indentLevel,
  defaultExpanded,
  scopeMode,
  toggleHandlers,
}: {
  node: TreeNode;
  search: string;
  searchOptions: SearchOptions;
  levelFull: boolean;
  timeMode: TimeMode;
  sourceMode: SourceMode;
  firstTimestamp: number;
  indentLevel: number;
  defaultExpanded: boolean;
  scopeMode: LogTarget["mode"];
  toggleHandlers: {
    toggleTimeMode: () => void;
    toggleLevelWidth: () => void;
    toggleSourceMode: () => void;
  };
}) {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);
  const hasChildren = node.children.length > 0;
  const { entry, content } = node;
  const display = computeRowDisplay(
    entry,
    content,
    search,
    searchOptions,
    timeMode,
    sourceMode,
    firstTimestamp,
  );
  const descendantCount = countDescendants(node);

  return (
    <>
      <div
        className={`lv-tree-row ${entry.level.toLowerCase()} ${
          indentLevel === 0 ? "lv-tree-root" : "lv-tree-child"
        }`}
      >
        <LogRowCells
          entry={entry}
          display={display}
          scopeMode={scopeMode}
          levelFull={levelFull}
          toggleHandlers={toggleHandlers}
        />
        <div className="lv-tree-message-cell">
          <div className="lv-tree-message-main">
            {indentLevel > 0 && (
              <span
                className="lv-tree-indent"
                style={{ width: `${indentLevel * 1.2}em` }}
              />
            )}
            {hasChildren && (
              <button
                className={`lv-tree-toggle ${
                  isExpanded ? "expanded" : "collapsed"
                }`}
                onClick={() => setIsExpanded(!isExpanded)}
                title={isExpanded ? "Collapse" : "Expand"}
              >
                <span className="lv-tree-toggle-icon">&#x25B8;</span>
                {!isExpanded && (
                  <span className="lv-tree-child-count">{descendantCount}</span>
                )}
              </button>
            )}
            <pre
              className="lv-message"
              dangerouslySetInnerHTML={{ __html: display.html }}
            />
          </div>
          <TracebacksInline entry={entry} />
        </div>
      </div>
      {hasChildren &&
        isExpanded &&
        node.children.map((child, idx) => (
          <TreeNodeRow
            key={idx}
            node={child}
            search={search}
            searchOptions={searchOptions}
            levelFull={levelFull}
            timeMode={timeMode}
            sourceMode={sourceMode}
            firstTimestamp={firstTimestamp}
            indentLevel={indentLevel + 1}
            defaultExpanded={defaultExpanded}
            scopeMode={scopeMode}
            toggleHandlers={toggleHandlers}
          />
        ))}
    </>
  );
}

function StandaloneLogRow({
  entry,
  content,
  search,
  searchOptions,
  levelFull,
  timeMode,
  sourceMode,
  firstTimestamp,
  scopeMode,
  toggleHandlers,
}: {
  entry: UiLogEntry;
  content: string;
  search: string;
  searchOptions: SearchOptions;
  levelFull: boolean;
  timeMode: TimeMode;
  sourceMode: SourceMode;
  firstTimestamp: number;
  scopeMode: LogTarget["mode"];
  toggleHandlers: {
    toggleTimeMode: () => void;
    toggleLevelWidth: () => void;
    toggleSourceMode: () => void;
  };
}) {
  const display = computeRowDisplay(
    entry,
    content,
    search,
    searchOptions,
    timeMode,
    sourceMode,
    firstTimestamp,
  );
  const sepInfo = isSeparatorLine(entry.message);

  return (
    <div className={`lv-entry-row lv-entry-standalone ${entry.level.toLowerCase()}`}>
      <LogRowCells
        entry={entry}
        display={display}
        scopeMode={scopeMode}
        levelFull={levelFull}
        toggleHandlers={toggleHandlers}
      />
      <div className="lv-message-cell">
        {sepInfo.isSeparator ? (
          <div
            className={`separator-line separator-${
              sepInfo.char === "=" ? "double" : "single"
            }`}
          >
            <span className="separator-line-bar" />
            {sepInfo.label && (
              <span className="separator-line-label">{sepInfo.label}</span>
            )}
            {sepInfo.label && <span className="separator-line-bar" />}
          </div>
        ) : (
          <pre
            className="lv-message"
            dangerouslySetInnerHTML={{ __html: display.html }}
          />
        )}
        <TracebacksInline entry={entry} />
      </div>
    </div>
  );
}

function LoggerCheckboxes({
  logs,
  enabledLoggers,
  onEnabledLoggersChange,
}: {
  logs: UiLogEntry[];
  enabledLoggers: Set<string> | null;
  onEnabledLoggersChange: (enabled: Set<string> | null) => void;
}) {
  const availableLoggers = getUniqueTopLevelLoggers(logs);
  const currentEnabled = enabledLoggers ?? new Set(availableLoggers);

  function toggleLogger(logger: string) {
    const next = new Set(currentEnabled);
    if (next.has(logger)) next.delete(logger);
    else next.add(logger);

    const allEnabled =
      next.size === availableLoggers.length &&
      availableLoggers.every((name) => next.has(name));
    const value = allEnabled ? null : next;
    onEnabledLoggersChange(value);
    saveEnabledLoggers(value);
  }

  if (availableLoggers.length === 0) {
    return <span className="lv-settings-empty">No loggers</span>;
  }

  return (
    <>
      {availableLoggers.map((logger) => (
        <label key={logger} className="lv-settings-check">
          <input
            type="checkbox"
            checked={currentEnabled.has(logger)}
            onChange={() => toggleLogger(logger)}
          />
          <span>{logger}</span>
        </label>
      ))}
    </>
  );
}

function SettingsPopover({
  children,
}: {
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  return (
    <div className="lv-settings" ref={ref}>
      <button
        className={`lv-settings-btn ${open ? "active" : ""}`}
        onClick={() => setOpen(!open)}
        title="Log settings"
      >
        <Settings size={14} />
      </button>
      {open && (
        <div className="lv-settings-panel">
          {children}
        </div>
      )}
    </div>
  );
}

function SettingsSection({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="lv-settings-section">
      <div className="lv-settings-section-label">{label}</div>
      {children}
    </div>
  );
}

function buildStatusIcon(status: Build["status"]) {
  if (status === "building") return <Spinner size={14} className="lv-build-status-icon building" />;
  const Icon = STATUS_ICONS[status];
  return Icon ? <Icon size={14} className={`lv-build-status-icon ${status}`} /> : null;
}

function BuildStatusSummary({ build }: { build: Build }) {
  const isBuilding = build.status === "building";
  const hasFailedStage = build.stages.some((s) => s.status === "failed");

  return (
    <div className="lv-build-summary">
      {buildStatusIcon(hasFailedStage && isBuilding ? "failed" : build.status)}
      {build.elapsedSeconds > 0 && (
        <span className="lv-build-summary-time">
          {formatDuration(build.elapsedSeconds)}
        </span>
      )}
      {build.warnings > 0 && (
        <span className="lv-build-summary-badge warning">{build.warnings}</span>
      )}
      {build.errors > 0 && (
        <span className="lv-build-summary-badge error">{build.errors}</span>
      )}
    </div>
  );
}

export interface LogViewerScreenProps {
  client: LogRpcClient;
  target: LogTarget | null;
  build?: Build | null;
  scopeValue?: string;
  onScopeChange?: (value: string) => void;
  targetControl?: ReactNode;
}

export function LogViewerScreen({
  client,
  target,
  build,
  scopeValue = "",
  onScopeChange,
  targetControl,
}: LogViewerScreenProps) {
  const [logLevels, setLogLevels] = useState<UiLogLevel[]>(() => {
    try {
      const parsed = JSON.parse(localStorage.getItem("lv-logLevels") || '["WARNING","ERROR"]');
      return Array.isArray(parsed) ? (parsed as UiLogLevel[]) : [];
    } catch {
      return [];
    }
  });
  const audience: UiAudience = "developer";
  const [search, setSearch] = useState("");
  const [enabledLoggers, setEnabledLoggers] = useState<Set<string> | null>(() =>
    loadEnabledLoggers(),
  );
  const [levelFull, setLevelFull] = useState(
    () => localStorage.getItem("lv-levelFull") === "true",
  );
  const [timeMode, setTimeMode] = useState<TimeMode>(
    () => localStorage.getItem("lv-timeMode") as TimeMode,
  );
  const [sourceMode, setSourceMode] = useState<SourceMode>(
    () => localStorage.getItem("lv-sourceMode") as SourceMode,
  );
  const [allExpanded, setAllExpanded] = useState(false);
  const [expandKey, setExpandKey] = useState(0);

  const contentRef = useRef<HTMLDivElement>(null);

  const { connectionState, error, logs } = useLogState(client);

  useEffect(() => {
    localStorage.setItem("lv-levelFull", String(levelFull));
  }, [levelFull]);

  useEffect(() => {
    localStorage.setItem("lv-timeMode", timeMode);
  }, [timeMode]);

  useEffect(() => {
    localStorage.setItem("lv-sourceMode", sourceMode);
  }, [sourceMode]);

  useEffect(() => {
    localStorage.setItem("lv-logLevels", JSON.stringify(logLevels));
  }, [logLevels]);

  useEffect(() => {
    if (!target?.mode) {
      client.stopStream();
      return;
    }

    if (target.mode === "test") {
      client.startStream(
        createLogRequest(
          {
            mode: "test",
            testRunId: target.testRunId,
            testName: target.testName,
          },
          {
            audience,
            logLevels,
          },
        ),
      );
    } else {
      client.startStream(
        createLogRequest(
          {
            mode: "build",
            buildId: target.buildId,
            stage: target.stage,
          },
          {
            audience,
            logLevels,
          },
        ),
      );
    }
  }, [
    audience,
    client,
    logLevels,
    target?.mode,
    target?.mode === "build" ? target.buildId : null,
    target?.mode === "build" ? target.stage : null,
    target?.mode === "test" ? target.testRunId : null,
    target?.mode === "test" ? target.testName : null,
  ]);

  const searchOptions: SearchOptions = { isRegex: false };
  const filteredLogs = filterByLoggers(
    filterLogs(logs, search, "", searchOptions),
    enabledLoggers,
  );

  const firstTimestamp =
    filteredLogs.length > 0
      ? new Date(filteredLogs[0].timestamp).getTime()
      : 0;
  const groups = groupLogsIntoTrees(filteredLogs);
  const foldableCount = groups.filter(
    (group) => group.type === "tree" && group.root.children.length > 0,
  ).length;
  const toggleHandlers = {
    toggleTimeMode: () =>
      setTimeMode((value) => (value === "delta" ? "wall" : "delta")),
    toggleLevelWidth: () => setLevelFull((value) => !value),
    toggleSourceMode: () =>
      setSourceMode((value) => (value === "source" ? "logger" : "source")),
  };

  const toggleLevel = (level: UiLogLevel) => {
    setLogLevels((current) =>
      current.includes(level)
        ? current.filter((candidate) => candidate !== level)
        : [...current, level],
    );
  };

  const gridTemplateColumns = [
    timeMode === "delta" ? "60px" : "72px",
    levelFull ? "max-content" : "3ch",
    "96px",
    "96px",
    "minmax(0, 1fr)",
  ].join(" ");

  const scopeMode = target?.mode ?? "build";
  const scopeLabel = getScopeLabel(scopeMode);
  const emptyState = !target
    ? "No log target selected"
    : "No logs available";

  return (
    <div
      className="lv-container"
      style={{ "--lv-grid-template-columns": gridTemplateColumns } as CSSProperties}
    >
      <div className="lv-toolbar">
        <div className="lv-controls">
          <div className="lv-controls-left">
            {targetControl}
          </div>

          <div className="lv-controls-right">
            <SearchBar
              value={search}
              onChange={setSearch}
              placeholder="Search..."
              className="lv-toolbar-search"
            />
            <SettingsPopover>
              <SettingsSection label="Log Levels">
                {(["ERROR", "WARNING", "INFO", "DEBUG"] as const).map(
                  (level) => (
                    <label key={level} className="lv-settings-check lv-level-check">
                      <input
                        type="checkbox"
                        checked={logLevels.includes(level)}
                        onChange={() => toggleLevel(level)}
                      />
                      <span className={`lv-level-label ${level.toLowerCase()}`}>{level}</span>
                    </label>
                  ),
                )}
              </SettingsSection>
              <SettingsSection label="Loggers">
                <LoggerCheckboxes
                  logs={logs}
                  enabledLoggers={enabledLoggers}
                  onEnabledLoggersChange={setEnabledLoggers}
                />
              </SettingsSection>
              <DeleteLogsButton />
            </SettingsPopover>
          </div>
        </div>

        {error && <div className="inline-error">{error}</div>}
      </div>

      {build && build.stages.length > 0 ? (
        <BuildStageList
          build={build}
          logs={filteredLogs}
          search={search}
          searchOptions={searchOptions}
        />
      ) : (
        <div className="lv-log-grid">
          <div className="lv-display-container">
            {foldableCount > 0 && (
              <div className="lv-expand-toolbar">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setAllExpanded(true);
                    setExpandKey((value) => value + 1);
                  }}
                  disabled={allExpanded}
                  title="Expand all"
                  className="lv-expand-btn"
                >
                  &#x229E;
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setAllExpanded(false);
                    setExpandKey((value) => value + 1);
                  }}
                  disabled={!allExpanded}
                  title="Collapse all"
                  className="lv-expand-btn"
                >
                  &#x229F;
                </Button>
              </div>
            )}
            <div className="lv-content" ref={contentRef}>
              {filteredLogs.length === 0 ? (
                <div className="empty-state">{emptyState}</div>
              ) : (
                groups.map((group, groupIdx) => {
                  if (group.type === "tree" && group.root.children.length > 0) {
                    return (
                      <TreeNodeRow
                        key={`${expandKey}-${groupIdx}`}
                        node={group.root}
                        search={search}
                        searchOptions={searchOptions}
                        levelFull={levelFull}
                        timeMode={timeMode}
                        sourceMode={sourceMode}
                        firstTimestamp={firstTimestamp}
                        indentLevel={0}
                        defaultExpanded={allExpanded}
                        scopeMode={scopeMode}
                        toggleHandlers={toggleHandlers}
                      />
                    );
                  }

                  return (
                    <StandaloneLogRow
                      key={groupIdx}
                      entry={group.root.entry}
                      content={group.root.content}
                      search={search}
                      searchOptions={searchOptions}
                      levelFull={levelFull}
                      timeMode={timeMode}
                      sourceMode={sourceMode}
                      firstTimestamp={firstTimestamp}
                      scopeMode={scopeMode}
                      toggleHandlers={toggleHandlers}
                    />
                  );
                })
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
