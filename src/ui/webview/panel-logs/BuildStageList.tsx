/**
 * Build-first log view:
 *   Build card (top level, double-height summary)
 *     └ Stage rows (collapsible sections)
 *         └ Log rows (one-line with expand)
 */

import type React from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, ChevronDown, MessageSquareText, Square } from "lucide-react";
import { Spinner } from "../common/components";
import type { Build, BuildStage, UiLogEntry } from "../../protocol/generated-types";
import type { SearchOptions } from "../common/utils/searchUtils";
import { createSearchMatcher } from "../common/utils/searchUtils";
import type { SourceMode, TimeMode } from "../../protocol/types";
import { STATUS_ICONS, formatDuration, getCurrentStage } from "../common/utils";
import {
  computeRowDisplay,
  isSeparatorLine,
  ansiConverter,
  tryParseStructuredTraceback,
} from "./logUtils";
import { StackInspector } from "./StackInspector";
import { AtoTraceback, parseAtoTraceback } from "./AtoTraceback";
import { rpcClient, WebviewRpcClient } from "../common/webviewRpcClient";

/* ---- Helpers ---- */

function buildStatusIcon(status: Build["status"], warnings = 0) {
  if (status === "building") return <Spinner size={16} className="lv-si-running" />;
  const displayStatus = status === "success" && warnings > 0 ? "warning" : status;
  const Icon = STATUS_ICONS[displayStatus];
  return Icon ? <Icon size={16} className={`lv-si-${displayStatus}`} /> : null;
}

function stageStatusIcon(status: BuildStage["status"], warnings = 0) {
  if (status === "running") return <Spinner size={12} />;
  const displayStatus = status === "success" && warnings > 0 ? "warning" : status;
  const Icon = STATUS_ICONS[displayStatus] ?? AlertCircle;
  return <Icon size={12} className={`lv-si-${displayStatus}`} />;
}

function groupLogsByStage(logs: UiLogEntry[]): Map<string, UiLogEntry[]> {
  const groups = new Map<string, UiLogEntry[]>();
  for (const entry of logs) {
    const key = entry.stage || "";
    const group = groups.get(key);
    if (group) group.push(entry);
    else groups.set(key, [entry]);
  }
  return groups;
}

function TracebacksExpanded({ entry }: { entry: UiLogEntry }) {
  const structuredTb = tryParseStructuredTraceback(entry.pythonTraceback);
  const atoTb = parseAtoTraceback(entry.atoTraceback);
  const showPython = !!entry.pythonTraceback;

  return (
    <div className="lv-tracebacks-expanded" onClick={(e) => e.stopPropagation()}>
      {atoTb && (
        <div className="lv-tb-section">
          <AtoTraceback traceback={atoTb} />
        </div>
      )}
      {showPython && structuredTb && structuredTb.frames.length > 0 ? (
        <details className="lv-tb-section">
          <summary className="lv-tb-label lv-tb-label-toggle">python traceback</summary>
          <StackInspector traceback={structuredTb} />
        </details>
      ) : showPython ? (
        <details className="lv-tb-section">
          <summary className="lv-tb-label lv-tb-label-toggle">python traceback</summary>
          <pre
            className="lv-tb-content lv-tb-python"
            dangerouslySetInnerHTML={{ __html: ansiConverter.toHtml(entry.pythonTraceback!) }}
          />
        </details>
      ) : null}
    </div>
  );
}

/* ---- Log row ---- */

function askAgent(entry: UiLogEntry, projectRoot: string | null) {
  if (!projectRoot) return;

  const parts: string[] = [
    `I got a build ${entry.level === "WARNING" ? "warning" : "error"}. Can you help me understand and fix it?`,
    "",
    `**${entry.level}**: ${entry.message}`,
  ];
  if (entry.stage) parts.push(`**Stage**: ${entry.stage}`);
  if (entry.sourceFile) parts.push(`**Source**: ${entry.sourceFile}${entry.sourceLine ? `:${entry.sourceLine}` : ""}`);

  const message = parts.join("\n");

  rpcClient?.sendAction("agent.createSession", {
    projectRoot,
    initialMessage: message,
    errorContext: {
      level: entry.level,
      message: entry.message,
      stage: entry.stage,
      sourceFile: entry.sourceFile,
      sourceLine: entry.sourceLine,
      atoTraceback: entry.atoTraceback,
      pythonTraceback: entry.pythonTraceback,
      buildId: null,
    },
  });
  void rpcClient?.requestAction("vscode.revealAgent");
}

function summarizeMessage(message: string): string {
  const lines = message.split(/\r?\n/);
  if (lines.length === 1) return message;
  return lines.map((line) => line.trim()).filter(Boolean).join(" ");
}

function SectionLogRow({
  entry,
  search,
  searchOptions,
  timeMode,
  sourceMode,
  firstTimestamp,
  projectRoot,
  isAuthenticated,
}: {
  entry: UiLogEntry;
  search: string;
  searchOptions: SearchOptions;
  timeMode: TimeMode;
  sourceMode: SourceMode;
  firstTimestamp: number;
  projectRoot: string | null;
  isAuthenticated: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasTracebacks = !!(entry.atoTraceback || entry.pythonTraceback);
  const messageText = entry.message;
  const isErrorOrWarning = entry.level === "ERROR" || entry.level === "WARNING" || entry.level === "ALERT";

  const display = computeRowDisplay(
    entry, messageText, search, searchOptions, timeMode, sourceMode, firstTimestamp,
  );
  const summaryMessage = summarizeMessage(messageText);
  const sepInfo = isSeparatorLine(messageText);
  const hasExtraMessage = summaryMessage !== messageText;
  const expandable = hasExtraMessage || hasTracebacks || isErrorOrWarning;
  const showExpandedMessage = expanded && hasExtraMessage;

  function hasTextSelectionIn(target: HTMLElement) {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) return false;
    return [selection.anchorNode, selection.focusNode].some(
      (node) => !!node && target.contains(node),
    );
  }

  function handleToggle(event: React.MouseEvent<HTMLDivElement>) {
    if (!expandable) return;
    if (hasTextSelectionIn(event.currentTarget)) return;
    setExpanded((value) => !value);
  }

  return (
    <div className={`lv-srow-wrapper${expanded ? " lv-srow-expanded" : ""}`}>
      <div
        className={`lv-srow ${entry.level.toLowerCase()}`}
        onClick={expandable ? handleToggle : undefined}
        style={expandable ? { cursor: "pointer" } : undefined}
      >
        <span className="lv-srow-time">{display.ts}</span>
        <span className={`lv-srow-level ${entry.level.toLowerCase()}`}>
          {entry.level}
        </span>
        <span
          className={`lv-srow-source${entry.sourceFile ? " lv-srow-source-link" : ""}`}
          title={display.sourceTooltip}
          style={display.sourceStyle}
          onClick={entry.sourceFile ? (e) => {
            e.stopPropagation();
            void rpcClient?.requestAction("vscode.openFile", {
              path: entry.sourceFile,
              line: entry.sourceLine ?? undefined,
            });
          } : undefined}
        >
          {display.sourceDisplayValue}
        </span>
        <div className="lv-srow-msg">
          {sepInfo.isSeparator ? (
            <div className={`separator-line separator-${sepInfo.char === "=" ? "double" : "single"}`}>
              <span className="separator-line-bar" />
              {sepInfo.label && <span className="separator-line-label">{sepInfo.label}</span>}
              {sepInfo.label && <span className="separator-line-bar" />}
            </div>
          ) : (
            <div className="lv-message lv-message-clamp">
              {summaryMessage}
            </div>
          )}
        </div>
        {expandable && (
          <ChevronDown size={11} className={`lv-srow-chevron${expanded ? " open" : ""}`} />
        )}
      </div>
      {expanded && (
        <div className="lv-srow-expanded-content">
          {showExpandedMessage && (
            <pre className="lv-message lv-srow-detail-message" dangerouslySetInnerHTML={{ __html: display.html }} />
          )}
          {isErrorOrWarning && (
            <button
              type="button"
              className="lv-ask-agent-btn"
              disabled={!isAuthenticated}
              title={isAuthenticated ? "Send this error to the AI agent" : "Sign in to use the AI agent"}
              onClick={(e) => {
                e.stopPropagation();
                askAgent(entry, projectRoot);
              }}
            >
              <MessageSquareText size={12} />
              <span>Ask Agent</span>
            </button>
          )}
          {hasTracebacks && (
            <div className="lv-srow-traceback">
              <TracebacksExpanded entry={entry} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ---- Build card (top-level summary) ---- */

function BuildCard({
  build,
  isExpanded,
  onToggle,
  progress,
  hasFailedStage,
  logWarnings,
  logErrors,
}: {
  build: Build;
  isExpanded: boolean;
  onToggle: () => void;
  progress: number;
  hasFailedStage: boolean;
  logWarnings: number;
  logErrors: number;
}) {
  const currentStage = getCurrentStage(build);
  const isBuilding = build.status === "building";
  const isComplete = build.status === "success" || build.status === "failed"
    || build.status === "warning" || build.status === "cancelled";
  const completedCount = build.stages.filter(
    (s) => s.status === "success" || s.status === "warning",
  ).length;

  // Detect build completion for pulse animation
  const [justCompleted, setJustCompleted] = useState(false);
  const prevStatusRef = useRef(build.status);
  useEffect(() => {
    const wasBuilding = prevStatusRef.current === "building" || prevStatusRef.current === "queued";
    if (wasBuilding && isComplete) {
      setJustCompleted(true);
      const timer = setTimeout(() => setJustCompleted(false), 1200);
      return () => clearTimeout(timer);
    }
    prevStatusRef.current = build.status;
  }, [build.status, isComplete]);

  const title = isBuilding && currentStage
    ? currentStage.name
    : isComplete
      ? build.status === "success" || build.status === "warning"
        ? "Build complete"
        : build.status === "failed"
          ? "Build failed"
          : "Build cancelled"
      : build.status === "queued"
        ? "Build queued"
        : "Build";

  // Background fill sweeps from left — uses CSS variable for smooth transition
  const progressStyle = isBuilding && !hasFailedStage
    ? { "--lv-build-fill": `${progress}%` } as React.CSSProperties
    : undefined;

  return (
    <button
      type="button"
      className={`lv-build-card ${build.status}${isExpanded ? " expanded" : ""}${justCompleted ? ` just-completed ${logErrors > 0 ? "outcome-error" : logWarnings > 0 ? "outcome-warning" : "outcome-success"}` : ""}`}
      onClick={onToggle}
      style={progressStyle}
    >
      <div className="lv-build-card-top">
        <span className="lv-build-card-icon">
          {buildStatusIcon(hasFailedStage && isBuilding ? "failed" : build.status, logWarnings)}
        </span>
        <span className="lv-build-card-title">{title}</span>
        <span className="lv-build-card-meta">
          {isBuilding && currentStage && (
            <span className="lv-build-card-stage">
              {completedCount}/{build.stages.length}
            </span>
          )}
          {build.elapsedSeconds > 0 && (
            <span className="lv-build-card-time">
              {formatDuration(build.elapsedSeconds)}
            </span>
          )}
        </span>
        {isBuilding && build.buildId ? (
          <span
            className="lv-build-card-stop"
            title="Cancel build"
            onClick={(e) => {
              e.stopPropagation();
              rpcClient?.sendAction("cancelBuild", { buildId: build.buildId });
            }}
          >
            <Square size={12} />
          </span>
        ) : (
          <ChevronDown size={14} className={`lv-build-card-chevron${isExpanded ? " open" : ""}`} />
        )}
      </div>
    </button>
  );
}

/* ---- Main component ---- */

export interface BuildStageListProps {
  build: Build;
  logs: UiLogEntry[];
  search: string;
  searchOptions: SearchOptions;
}

export function BuildStageList({
  build,
  logs,
  search,
  searchOptions,
}: BuildStageListProps) {
  const authState = WebviewRpcClient.useSubscribe("authState");
  const [stagesExpanded, setStagesExpanded] = useState(
    () => localStorage.getItem("lv-build-expanded") === "true",
  );
  const [expandedStages, setExpandedStages] = useState<Set<string>>(new Set());
  // Snapshot of manually-expanded stages before search overrides them
  const preSearchExpandedRef = useRef<Set<string> | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  const totalWarnings = logs.filter((e) => e.level === "WARNING").length;
  const totalErrors = logs.filter((e) => e.level === "ERROR" || e.level === "ALERT").length;
  const hasErrors = build.status === "failed" || totalErrors > 0;

  // Persist expand state
  useEffect(() => {
    localStorage.setItem("lv-build-expanded", String(stagesExpanded));
  }, [stagesExpanded]);

  // Auto-expand the build card when there are errors
  useEffect(() => {
    if (hasErrors) {
      setStagesExpanded(true);
    }
  }, [hasErrors]);

  // Auto-expand error stages
  useEffect(() => {
    const errorStages = build.stages
      .filter((s) => s.status === "failed" || s.errors > 0)
      .map((s) => s.stageId || s.name);
    if (errorStages.length > 0) {
      setExpandedStages((prev) => {
        const next = new Set(prev);
        for (const key of errorStages) next.add(key);
        return next;
      });
    }
  }, [build.stages]);

  const logsByStage = groupLogsByStage(logs);

  // Compute which stages have search matches
  const isSearching = search.trim().length > 0;
  const stagesWithMatches = useMemo(() => {
    if (!isSearching) return null;
    const matcher = createSearchMatcher(search, searchOptions);
    const matched = new Set<string>();
    for (const [stageKey, entries] of logsByStage) {
      if (entries.some((e) => matcher(e.message).matches)) {
        matched.add(stageKey);
      }
    }
    return matched;
  }, [isSearching, search, searchOptions, logsByStage]);

  // When search activates, save current state and expand matching stages.
  // When search clears, restore the saved state.
  useEffect(() => {
    if (isSearching && stagesWithMatches) {
      if (preSearchExpandedRef.current === null) {
        preSearchExpandedRef.current = new Set(expandedStages);
      }
      setExpandedStages(stagesWithMatches);
      if (stagesWithMatches.size > 0) setStagesExpanded(true);
    } else if (preSearchExpandedRef.current !== null) {
      setExpandedStages(preSearchExpandedRef.current);
      preSearchExpandedRef.current = null;
    }
  }, [isSearching, stagesWithMatches]);
  const stageMetaMap = new Map(build.stages.map((s) => [s.stageId || s.name, s]));
  const stageKeys = build.stages
    .filter((s) => {
      const key = s.stageId || s.name;
      const hasLogs = (logsByStage.get(key)?.length ?? 0) > 0;
      const hasDuration = (s.elapsedSeconds ?? 0) > 0;
      const isActive = s.status === "running" || s.status === "failed";
      return hasLogs || hasDuration || isActive;
    })
    .map((s) => s.stageId || s.name);

  const orphanLogs: UiLogEntry[] = [];
  for (const [key, entries] of logsByStage) {
    if (!key || !stageMetaMap.has(key)) {
      orphanLogs.push(...entries);
    }
  }

  const hasFailedStage = build.stages.some((s) => s.status === "failed");
  const progress = (() => {
    if (!build.stages.length) return 0;
    const done = build.stages.filter((s) => s.status === "success" || s.status === "warning").length;
    const total = build.totalStages || Math.max(done + 1, 10);
    return Math.round((done / total) * 100);
  })();
  const firstTimestamp = logs.length > 0 ? new Date(logs[0].timestamp).getTime() : 0;

  function toggleStage(key: string) {
    setExpandedStages((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <div className="lv-build-overview">
      <BuildCard
        build={build}
        isExpanded={stagesExpanded}
        onToggle={() => setStagesExpanded((v) => !v)}
        progress={progress}
        hasFailedStage={hasFailedStage}
        logWarnings={totalWarnings}
        logErrors={totalErrors}
      />
      {stagesExpanded && (
        <div className="lv-build-stream" ref={contentRef}>
          {stageKeys.length === 0 && logs.length === 0 && (
            <div className="lv-stage-list-empty">
              {build.status === "queued" ? "Build queued..." : build.status === "building" ? "Starting build..." : "No logs"}
            </div>
          )}
          {/* Setup section for orphan logs */}
          {orphanLogs.length > 0 && !(isSearching && stagesWithMatches && !stagesWithMatches.has("")) && (() => {
            const setupExpanded = expandedStages.has("__setup__") || (isSearching && stagesWithMatches?.has(""));
            return (
              <div className="lv-build-section">
                <button
                  type="button"
                  className={`lv-stage-hdr${setupExpanded ? " expanded" : ""}`}
                  onClick={() => toggleStage("__setup__")}
                >
                  <span className="lv-stage-hdr-icon">
                    {stageStatusIcon(
                      orphanLogs.some((e) => e.level === "ERROR") ? "failed" : "success",
                      orphanLogs.filter((e) => e.level === "WARNING").length,
                    )}
                  </span>
                  <span className="lv-stage-hdr-name">Setup</span>
                  <span className="lv-stage-hdr-badges" />
                  <span className="lv-stage-hdr-time" />
                  <span className="lv-stage-hdr-count">{orphanLogs.length}</span>
                  <ChevronDown size={12} className={`lv-stage-hdr-chevron${setupExpanded ? " open" : ""}`} />
                </button>
                {setupExpanded && (
                  <div className="lv-build-section-logs">
                    {orphanLogs.map((entry, idx) => (
                      <SectionLogRow
                        key={`setup-${idx}`}
                        entry={entry}
                        search={search}
                        searchOptions={searchOptions}
                        timeMode="delta"
                        sourceMode="source"
                        firstTimestamp={firstTimestamp}
                        projectRoot={build.projectRoot}
                        isAuthenticated={authState.isAuthenticated}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          })()}
          {/* Stage sections */}
          {stageKeys.map((stageKey) => {
            // Hide stages with no search matches
            if (isSearching && stagesWithMatches && !stagesWithMatches.has(stageKey)) return null;

            const meta = stageMetaMap.get(stageKey);
            const stageLogs = logsByStage.get(stageKey) || [];
            const isExpanded = expandedStages.has(stageKey);
            const displayName = meta?.name || stageKey;
            const logCount = stageLogs.length;
            const stageWarnings = stageLogs.filter((e) => e.level === "WARNING").length;

            return (
              <div key={stageKey} className="lv-build-section">
                <button
                  type="button"
                  className={`lv-stage-hdr ${meta?.status || "pending"}${isExpanded ? " expanded" : ""}`}
                  onClick={() => toggleStage(stageKey)}
                >
                  <span className="lv-stage-hdr-icon">
                    {meta ? stageStatusIcon(meta.status, stageWarnings) : null}
                  </span>
                  <span className="lv-stage-hdr-name">{displayName}</span>
                  <span className="lv-stage-hdr-badges" />
                  <span className="lv-stage-hdr-time">
                    {meta && meta.status !== "pending"
                      ? formatDuration(meta.elapsedSeconds)
                      : ""}
                  </span>
                  <span className="lv-stage-hdr-count">
                    {logCount > 0 ? logCount : ""}
                  </span>
                  <ChevronDown size={12} className={`lv-stage-hdr-chevron${isExpanded ? " open" : ""}`} />
                </button>
                {isExpanded && stageLogs.length > 0 && (
                  <div className="lv-build-section-logs">
                    {stageLogs.map((entry, idx) => (
                      <SectionLogRow
                        key={idx}
                        entry={entry}
                        search={search}
                        searchOptions={searchOptions}
                        timeMode="delta"
                        sourceMode="source"
                        firstTimestamp={firstTimestamp}
                        projectRoot={build.projectRoot}
                        isAuthenticated={authState.isAuthenticated}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
