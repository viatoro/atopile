import { useRef, useState, useEffect } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  XCircle,
} from 'lucide-react';
import { FileIcon } from '../../common/utils/fileIcons';
import type { AgentMessage, AgentChecklist, AgentChecklistItem } from '../state/types';
import type { AgentChangedFile, AgentChangedFilesSummary } from './viewHelpers';
import {
  formatCount,
  renderLineDelta,
  summarizeToolTrace,
  summarizeToolTraceGroup,
  summarizeTraceDetails,
} from './viewHelpers';
import { readTraceDiff } from '../state/progress';
import type { AgentTraceView } from '../state/types';

/* ── Aggregate helpers ──────────────────────────────────── */

function aggregateToolTraces(messages: AgentMessage[]): { traces: AgentTraceView[]; running: number } {
  const traces: AgentTraceView[] = [];
  for (const msg of messages) {
    if (msg.toolTraces) {
      for (const t of msg.toolTraces) traces.push(t);
    }
  }
  return {
    traces,
    running: traces.filter((t) => t.running).length,
  };
}

/* ── Checklist (pinned to header area) ─────────────────── */

function ChecklistItemRow({ item, index }: { item: AgentChecklistItem; index: number }) {
  const [itemExpanded, setItemExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const descRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const el = descRef.current;
    if (el) setOverflows(el.scrollWidth > el.clientWidth);
  }, [item.description]);

  const isExpandable = overflows || itemExpanded;

  return (
    <button
      type="button"
      className={[
        'agent-meta-checklist-item',
        `agent-meta-checklist-item--${item.status}`,
        itemExpanded ? 'item-expanded' : '',
        isExpandable ? 'expandable' : '',
      ].filter(Boolean).join(' ')}
      onClick={isExpandable ? () => setItemExpanded((v) => !v) : undefined}
      style={isExpandable ? undefined : { cursor: 'default' }}
    >
      <span className="agent-meta-checklist-icon">
        {item.status === 'done' && <CheckCircle2 size={11} className="meta-status-ok" />}
        {item.status === 'in_progress' && <Loader2 size={11} className="agent-tool-spin meta-status-running" />}
        {item.status === 'blocked' && <AlertCircle size={11} className="meta-status-blocked" />}
        {item.status === 'pending' && <span className="agent-meta-checklist-circle" />}
      </span>
      <span className="agent-meta-checklist-id">{index}</span>
      <span
        ref={descRef}
        className={`agent-meta-checklist-desc ${itemExpanded ? 'expanded' : ''}`}
      >
        {item.description}
      </span>
      {isExpandable && (
        <ChevronRight
          size={10}
          className={`agent-meta-checklist-expand-icon ${itemExpanded ? 'open' : ''}`}
        />
      )}
    </button>
  );
}


interface AgentChecklistBarProps {
  checklist: AgentChecklist;
}

export function AgentChecklistBar({ checklist }: AgentChecklistBarProps) {
  const [expanded, setExpanded] = useState(false);
  const completedCount = checklist.items.filter((i) => i.status === 'done').length;
  const checklistCount = checklist.items.length;

  return (
    <div className={`agent-checklist-bar ${expanded ? 'expanded' : ''}`}>
      <button
        type="button"
        className="agent-meta-summary-row"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <span className="agent-meta-col-label">Checklist</span>
        <span className="agent-meta-col-info" />
        <span className="agent-meta-col-right">
          <span className="agent-meta-count">{completedCount}/{checklistCount}</span>
        </span>
        <ChevronDown
          size={11}
          className={`agent-meta-col-chevron ${expanded ? 'open' : ''}`}
        />
      </button>
      {expanded && (
        <div className="agent-checklist-bar-detail">
          {checklist.items.map((item, index) => (
            <ChecklistItemRow key={item.id} item={item} index={index + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Expandable tool trace row ──────────────────────────── */

function ToolTraceRow({ trace }: { trace: AgentTraceView }) {
  const [expanded, setExpanded] = useState(false);
  const details = summarizeTraceDetails(trace);
  const traceDiff = readTraceDiff(trace);
  const hasDetails = !!(details.input.text || details.output.text || traceDiff);

  return (
    <div className={`agent-meta-tool-row ${trace.running ? 'running' : trace.ok ? 'ok' : 'error'} ${expanded ? 'expanded' : ''}`}>
      <button
        type="button"
        className={`agent-meta-tool-toggle ${hasDetails ? 'expandable' : ''}`}
        onClick={hasDetails ? () => setExpanded((v) => !v) : undefined}
        style={hasDetails ? undefined : { cursor: 'default' }}
      >
        <span className="agent-meta-tool-status">
          {trace.running
            ? <Loader2 size={10} className="agent-tool-spin meta-status-running" />
            : trace.ok
              ? <CheckCircle2 size={10} className="meta-status-ok" />
              : <XCircle size={10} className="meta-status-error" />}
        </span>
        <span className="agent-meta-tool-name">{trace.name}</span>
        <span className="agent-meta-tool-summary">{summarizeToolTrace(trace)}</span>
        {traceDiff && renderLineDelta(traceDiff.added, traceDiff.removed, 'agent-line-delta-compact')}
        {hasDetails && (
          <ChevronRight
            size={10}
            className={`agent-meta-tool-expand ${expanded ? 'open' : ''}`}
          />
        )}
      </button>
      {expanded && (
        <div className="agent-meta-tool-details">
          {details.input.text && (
            <div className="agent-meta-tool-detail-row">
              <span className="agent-meta-tool-detail-label">input</span>
              <span className="agent-meta-tool-detail-value">{details.input.text}</span>
            </div>
          )}
          {traceDiff && (
            <div className="agent-meta-tool-detail-row">
              <span className="agent-meta-tool-detail-label">lines</span>
              <span className="agent-meta-tool-detail-value">
                {renderLineDelta(traceDiff.added, traceDiff.removed)}
              </span>
            </div>
          )}
          {details.output.text && (
            <div className="agent-meta-tool-detail-row">
              <span className="agent-meta-tool-detail-label">
                {trace.ok || trace.running ? 'output' : 'error'}
              </span>
              <span className={`agent-meta-tool-detail-value ${!trace.ok && !trace.running ? 'error' : ''}`}>
                {details.output.text}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Metadata Strip (tools + files) ────────────────────── */

interface AgentMetadataStripProps {
  messages: AgentMessage[];
  changedFilesSummary: AgentChangedFilesSummary | null;
  onOpenFileDiff: (file: AgentChangedFile) => void;
}

export function AgentMetadataStrip({
  messages,
  changedFilesSummary,
  onOpenFileDiff,
}: AgentMetadataStripProps) {
  const [toolsExpanded, setToolsExpanded] = useState(false);
  const [filesExpanded, setFilesExpanded] = useState(false);

  const toolStats = aggregateToolTraces(messages);

  const hasTools = toolStats.traces.length > 0;
  const hasFiles = changedFilesSummary && changedFilesSummary.files.length > 0;

  if (!hasTools && !hasFiles) return null;

  return (
    <div className="agent-metadata-strip">
      {/* ── Tool use row ── */}
      {hasTools && (
        <div className={`agent-meta-section ${toolsExpanded ? 'expanded' : ''}`}>
          <button
            type="button"
            className="agent-meta-summary-row"
            onClick={() => setToolsExpanded((v) => !v)}
            aria-expanded={toolsExpanded}
          >
            <span className="agent-meta-col-label">Tools</span>
            <span className="agent-meta-col-info" />
            <span className="agent-meta-col-right">
              <span className="agent-meta-count">{toolStats.traces.length}</span>
            </span>
            <ChevronDown
              size={11}
              className={`agent-meta-col-chevron ${toolsExpanded ? 'open' : ''}`}
            />
          </button>
          {toolsExpanded && (
            <div className="agent-meta-detail agent-meta-tool-list">
              {toolStats.traces.map((trace, idx) => (
                <ToolTraceRow
                  key={trace.callId ?? `${trace.name}-${idx}`}
                  trace={trace}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Changed files row ── */}
      {hasFiles && (
        <div className={`agent-meta-section ${filesExpanded ? 'expanded' : ''}`}>
          <button
            type="button"
            className="agent-meta-summary-row"
            onClick={() => setFilesExpanded((v) => !v)}
            aria-expanded={filesExpanded}
          >
            <span className="agent-meta-col-label">Files</span>
            <span className="agent-meta-col-info" />
            <span className="agent-meta-col-right">
              {renderLineDelta(
                changedFilesSummary!.totalAdded,
                changedFilesSummary!.totalRemoved,
                'agent-line-delta-compact',
              )}
            </span>
            <ChevronDown
              size={11}
              className={`agent-meta-col-chevron ${filesExpanded ? 'open' : ''}`}
            />
          </button>
          {filesExpanded && (
            <div className="agent-meta-detail">
              {changedFilesSummary!.files.map((file) => (
                <button
                  key={file.path}
                  type="button"
                  className="agent-meta-file-row"
                  onClick={() => onOpenFileDiff(file)}
                  title={file.path}
                >
                  <FileIcon name={file.path} size={12} />
                  <span className="agent-meta-file-path">{file.path}</span>
                  {renderLineDelta(file.added, file.removed, 'agent-line-delta-compact')}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
