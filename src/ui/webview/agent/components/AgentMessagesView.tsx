import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from 'react';
import { AlertCircle, CheckCircle2, ChevronDown, ChevronRight, Loader2, MessageCircleMore, XCircle } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { AgentMessage, AgentTraceView } from '../state/types';
import type { LogErrorContext } from '../../../protocol/types';
import { AtoTraceback, parseAtoTraceback } from '../../panel-logs/AtoTraceback';
import { DesignQuestionsCard } from './DesignQuestionsCard';
import { BuildRef, ModuleRef, PackageRef, PanelRef, PartRef } from './RefBadges';
import { InlineFileRef } from '../../common/components/InlineFileRef';
import { highlightAtoCode } from '../../common/utils/codeHighlight';

/**
 * Build custom ReactMarkdown component overrides.
 */
function ClampedRow({ children }: { children: ReactNode }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <tr
      className={`agent-table-row${expanded ? ' expanded' : ''}`}
      onClick={(e) => {
        // Let clicks on interactive elements (links, buttons) pass through
        const target = e.target as HTMLElement;
        if (target.closest('a, button')) return;
        setExpanded((v) => !v);
      }}
    >
      {children}
    </tr>
  );
}

function CollapsibleTable({ title, children }: { title: string; children: ReactNode }) {
  const [expanded, setExpanded] = useState(true);
  return (
    <div className={`agent-table-wrap${expanded ? ' expanded' : ' collapsed'}`}>
      <button
        type="button"
        className="agent-table-header"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="agent-table-title">{title}</span>
        <ChevronDown size={12} className={`agent-table-chevron${expanded ? ' open' : ''}`} />
      </button>
      <div className="agent-table-body">
        {children}
        <button
          type="button"
          className="agent-table-expand-btn"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? 'Collapse' : 'Show all rows'}
        </button>
      </div>
    </div>
  );
}

function ExpandableTable({ children }: { children: ReactNode }) {
  const [expanded, setExpanded] = useState(true);
  return (
    <div className={`agent-table-expandable${expanded ? ' expanded' : ' collapsed'}`}>
      <table>{children}</table>
      <button
        type="button"
        className="agent-table-expand-btn"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? 'Collapse' : 'Show all rows'}
      </button>
    </div>
  );
}

function useMarkdownComponents(projectRoot: string | null, tableTitles: string[]) {
  return useMemo(() => {
    if (!projectRoot) return {};

    const root = projectRoot;
    let tableIndex = 0;

    return {
      code({ children, className }: { children?: ReactNode; className?: string }) {
        if (className === 'language-ato') {
          const text = typeof children === 'string' ? children : '';
          return <code className={className}>{text ? highlightAtoCode(text) : children}</code>;
        }
        return <code className={className}>{children}</code>;
      },
      pre({ children }: { children?: ReactNode }) {
        return <pre>{children}</pre>;
      },
      // Paragraphs, list items, inline emphasis, and headings: parse
      // [[type:value|label]] refs. Emphasis wrappers matter because the
      // model likes formatting refs as `**[[package:…]]**` — without
      // linkifying inside `<strong>`/`<em>`/headings, they'd stay as
      // raw bracket markup.
      p({ children }: { children?: ReactNode }) {
        return <p>{linkifyChildren(children, root)}</p>;
      },
      li({ children }: { children?: ReactNode }) {
        return <li>{linkifyChildren(children, root)}</li>;
      },
      strong({ children }: { children?: ReactNode }) {
        return <strong>{linkifyChildren(children, root)}</strong>;
      },
      em({ children }: { children?: ReactNode }) {
        return <em>{linkifyChildren(children, root)}</em>;
      },
      h1({ children }: { children?: ReactNode }) {
        return <h1>{linkifyChildren(children, root)}</h1>;
      },
      h2({ children }: { children?: ReactNode }) {
        return <h2>{linkifyChildren(children, root)}</h2>;
      },
      h3({ children }: { children?: ReactNode }) {
        return <h3>{linkifyChildren(children, root)}</h3>;
      },
      h4({ children }: { children?: ReactNode }) {
        return <h4>{linkifyChildren(children, root)}</h4>;
      },
      blockquote({ children }: { children?: ReactNode }) {
        return <blockquote>{linkifyChildren(children, root)}</blockquote>;
      },
      // Table rows: clamp cells to 2 lines, click row to expand
      tr({ children }: { children?: ReactNode }) {
        return <ClampedRow>{children}</ClampedRow>;
      },
      td({ children }: { children?: ReactNode }) {
        return <td><div className="agent-table-cell-clamp">{linkifyChildren(children, root)}</div></td>;
      },
      th({ children }: { children?: ReactNode }) {
        return <th>{linkifyChildren(children, root)}</th>;
      },
      // Tables: wrap with collapsible header if a [[table:Title]] preceded it,
      // otherwise wrap in expandable container (1 row preview by default)
      table({ children }: { children?: ReactNode }) {
        const title = tableTitles[tableIndex++];
        if (title) {
          return (
            <CollapsibleTable title={title}>
              <table>{children}</table>
            </CollapsibleTable>
          );
        }
        return <ExpandableTable>{children}</ExpandableTable>;
      },
    };
  }, [projectRoot, tableTitles]);
}

/* ── [[type:value|label]] structured reference parsing ── */

// Matches [[type:value]] or [[type:value|display label]]
// The label separator can be | or \x1F (unit separator).
// escapeRefsForMarkdown() converts | to \x1F inside refs before markdown parsing
// so pipes don't break table cells. Both separators are accepted here.
const REF_RE = /\[\[(\w+):([^|\x1F\]]+?)(?:[|\x1F]([^\]]+?))?\]\]/g;

/**
 * Preprocess message content before markdown parsing:
 * 1. Replace `|` inside `[[...]]` refs with \x1F so markdown table pipes don't split them
 * 2. Strip [[table:Title]] and [[/table]] markers, returning extracted titles
 */
function preprocessMarkdown(text: string): { content: string; tableTitles: string[] } {
  // Escape pipes inside refs
  let content = text.replace(/\[\[\w+:[^\]]+\]\]/g, (match) => match.replace(/\|/g, '\x1F'));

  // Extract [[table:Title]] markers and strip [[/table]]
  const tableTitles: string[] = [];
  content = content.replace(/^\[\[table:([^\]]+)\]\]\s*$/gm, (_match, title) => {
    tableTitles.push(title);
    return '';
  });
  content = content.replace(/^\[\[\/table\]\]\s*$/gm, '');

  return { content, tableTitles };
}

// Matches @mentions in user messages (e.g. @src/file.ato or @ModuleName)
const AT_MENTION_RE = /(?:^|\s)(@[\w./-]+\w)/g;

function renderRef(
  type: string,
  value: string,
  label: string | undefined,
  projectRoot: string,
  key: string | number,
): ReactNode {
  switch (type) {
    case 'file':
      return (
        <InlineFileRef
          key={key}
          path={value}
          projectRoot={projectRoot}
          label={label}
        />
      );
    case 'package':
      return (
        <PackageRef key={key} packageId={value} label={label} projectRoot={projectRoot} />
      );
    case 'part':
      return (
        <PartRef key={key} lcsc={value} label={label} projectRoot={projectRoot} />
      );
    case 'module':
      return (
        <ModuleRef key={key} name={value} label={label} projectRoot={projectRoot} />
      );
    case 'build':
      return <BuildRef key={key} buildId={value} label={label} />;
    case 'panel':
      return <PanelRef key={key} panelKey={value} label={label} />;
    case 'ato':
      return (
        <code key={key} className="ato-highlighted">
          {highlightAtoCode(value)}
        </code>
      );
    default:
      // Unknown type — render as plain text
      return <span key={key}>{label || value}</span>;
  }
}

/**
 * Scan text for [[type:value|label]] refs, file paths, and package references.
 */
function linkifyText(
  text: string,
  projectRoot: string,
): Array<string | ReactNode> {
  const matches: Array<{ start: number; end: number; node: ReactNode }> = [];

  // Structured [[type:value|label]] references only
  REF_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = REF_RE.exec(text)) !== null) {
    matches.push({
      start: m.index,
      end: m.index + m[0].length,
      node: renderRef(m[1], m[2].trim(), m[3]?.trim(), projectRoot, `r${m.index}`),
    });
  }

  // @mentions — infer type from token shape (contains / or . → file, else module)
  AT_MENTION_RE.lastIndex = 0;
  while ((m = AT_MENTION_RE.exec(text)) !== null) {
    const full = m[1]; // e.g. "@src/file.ato"
    const token = full.slice(1); // strip leading @
    const mentionStart = m.index + (m[0].length - full.length);
    const isFile = token.includes('/') || token.includes('.');
    const type = isFile ? 'file' : 'module';
    matches.push({
      start: mentionStart,
      end: mentionStart + full.length,
      node: renderRef(type, token, undefined, projectRoot, `at${m.index}`),
    });
  }

  if (matches.length === 0) return [text];

  matches.sort((a, b) => a.start - b.start);
  const segments: Array<string | ReactNode> = [];
  let lastIndex = 0;
  for (const match of matches) {
    if (match.start < lastIndex) continue; // skip overlapping matches
    if (match.start > lastIndex) segments.push(text.slice(lastIndex, match.start));
    segments.push(match.node);
    lastIndex = match.end;
  }
  if (lastIndex < text.length) segments.push(text.slice(lastIndex));
  return segments;
}

/** Recursively process children, linkifying string nodes */
function linkifyChildren(
  children: ReactNode,
  projectRoot: string,
): ReactNode {
  if (typeof children === 'string') {
    const result = linkifyText(children, projectRoot);
    return result.length === 1 && typeof result[0] === 'string' ? result[0] : <>{result}</>;
  }
  if (Array.isArray(children)) {
    return children.map((child, i) => {
      if (typeof child === 'string') {
        const result = linkifyText(child, projectRoot);
        if (result.length === 1 && typeof result[0] === 'string') return child;
        return <span key={i}>{result}</span>;
      }
      return child;
    });
  }
  return children;
}

/* ── Error context card ────────────────────────────────── */

function ErrorContextCard({ ctx }: { ctx: LogErrorContext }) {
  const [expanded, setExpanded] = useState(false);
  const atoTb = parseAtoTraceback(ctx.atoTraceback);
  const hasDetails = !!(atoTb || ctx.pythonTraceback || ctx.message.includes('\n'));
  const levelClass = ctx.level === 'WARNING' ? 'warning' : 'error';
  const firstLine = ctx.message.split('\n')[0];

  return (
    <div className={`agent-error-card ${levelClass}${expanded ? ' expanded' : ''}`}>
      <button
        type="button"
        className="agent-error-card-header"
        onClick={() => hasDetails && setExpanded((v) => !v)}
        style={hasDetails ? undefined : { cursor: 'default' }}
      >
        {ctx.level === 'WARNING'
          ? <AlertCircle size={12} className="agent-error-card-icon warning" />
          : <XCircle size={12} className="agent-error-card-icon error" />}
        <span className={`agent-error-card-level ${levelClass}`}>{ctx.level}</span>
        {ctx.stage && <span className="agent-error-card-stage">{ctx.stage}</span>}
        {ctx.sourceFile && (
          <span className="agent-error-card-source">
            {ctx.sourceFile}{ctx.sourceLine ? `:${ctx.sourceLine}` : ''}
          </span>
        )}
        {hasDetails && (
          <ChevronDown size={11} className={`agent-error-card-chevron ${expanded ? 'open' : ''}`} />
        )}
      </button>
      <div className="agent-error-card-message">
        <pre className="agent-error-card-msg-text">
          {expanded ? ctx.message : firstLine}
        </pre>
      </div>
      {expanded && (
        <div className="agent-error-card-details">
          {atoTb && (
            <div className="agent-error-tb-section">
              <AtoTraceback traceback={{
                ...atoTb,
                frames: atoTb.frames.length > 0 ? [atoTb.frames[atoTb.frames.length - 1]] : [],
              }} />
            </div>
          )}
          {ctx.pythonTraceback && (
            <details className="agent-error-tb-section">
              <summary className="agent-error-tb-label">python traceback</summary>
              <pre className="agent-error-tb-pre">{ctx.pythonTraceback}</pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Messages view ─────────────────────────────────────── */

interface AgentMessagesViewProps {
  messagesRef: RefObject<HTMLDivElement | null>;
  messages: AgentMessage[];
  projectRoot: string | null;
  onSubmitDesignQuestions: (answers: string) => void;
}

function MessageBubble({ content, projectRoot, streaming }: { content: string; projectRoot: string | null; streaming?: boolean }) {
  // During streaming, split so completed blocks are rendered as stable
  // memoized markdown and only the in-progress tail updates as plain text.
  // Special case: if the tail looks like a table row (starts with |),
  // keep the whole table in the stable section so it renders progressively.
  const { stable: stableContent, tail: tailContent } = useMemo(() => {
    if (!streaming) return { stable: content, tail: '' };

    const splitIdx = content.lastIndexOf('\n\n');
    if (splitIdx <= 0) return { stable: '', tail: content };

    const candidateTail = content.slice(splitIdx + 2);
    // If the tail looks like a table row or we're mid-table,
    // include it in stable markdown so the table renders progressively
    if (candidateTail.trimStart().startsWith('|')) {
      return { stable: content, tail: '' };
    }

    return { stable: content.slice(0, splitIdx), tail: candidateTail };
  }, [content, streaming]);

  const { content: processed, tableTitles } = useMemo(() => preprocessMarkdown(stableContent), [stableContent]);
  const components = useMarkdownComponents(projectRoot, tableTitles);
  return (
    <div className="agent-message-bubble">
      <div className="agent-message-content agent-markdown">
        {stableContent && (
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
            {processed}
          </ReactMarkdown>
        )}
        {tailContent && <p className="agent-streaming-tail">{tailContent}</p>}
      </div>
    </div>
  );
}

/** Does this tool operate on a file path? */
function toolFilePath(trace: AgentTraceView): string | null {
  const args = trace.args as Record<string, unknown>;
  if (typeof args.path === 'string') return args.path as string;
  return null;
}

/** Extract non-file context for display. */
function toolContextText(trace: AgentTraceView): string | null {
  const args = trace.args as Record<string, unknown>;
  if (typeof args.query === 'string') return `"${(args.query as string).slice(0, 60)}"`;
  if (typeof args.target === 'string') return args.target as string;
  if (typeof args.targets === 'string') return args.targets as string;
  if (typeof args.name === 'string') return args.name as string;
  if (typeof args.entry === 'string') return args.entry as string;
  if (typeof args.build_id === 'string') return args.build_id as string;
  if (typeof args.package_id === 'string') return args.package_id as string;
  // For search results, show count from result
  const result = trace.result as Record<string, unknown>;
  if (typeof result.total === 'number') return `${result.total} results`;
  return null;
}

/**
 * Group tool traces into "bursts" separated by text output.
 * A burst is a consecutive run of tool calls. Once the model produces
 * text (a gap in tool calls), the burst ends.
 * We detect burst boundaries by looking at the message content:
 * completed bursts are all traces before the last active/running set.
 */
interface ToolBurst {
  traces: AgentTraceView[];
  active: boolean; // true = current burst (still running or latest)
}

function groupToolBursts(traces: AgentTraceView[]): ToolBurst[] {
  if (traces.length === 0) return [];

  // Find the last running trace — everything from there to the end is "active"
  let activeStart = traces.length;
  for (let i = traces.length - 1; i >= 0; i--) {
    activeStart = i;
    if (traces[i].running) break;
    // If we hit the start without finding a running trace,
    // the last burst of completed traces is still "active" (just finished)
  }

  // Simple split: everything before activeStart = completed bursts, rest = active
  const bursts: ToolBurst[] = [];
  if (activeStart > 0) {
    bursts.push({ traces: traces.slice(0, activeStart), active: false });
  }
  if (activeStart < traces.length) {
    bursts.push({ traces: traces.slice(activeStart), active: true });
  }

  return bursts;
}

/** A single tool row — minimal, one line. */
function ToolRow({ trace, projectRoot }: { trace: AgentTraceView; projectRoot: string | null }) {
  const filePath = toolFilePath(trace);
  const ctx = toolContextText(trace);

  return (
    <div className={`agent-tool-row ${trace.running ? 'running' : ''} ${!trace.running && !trace.ok ? 'failed' : ''}`}>
      {trace.running ? (
        <Loader2 size={10} className="agent-tool-spin meta-status-running" />
      ) : trace.ok ? (
        <CheckCircle2 size={10} className="meta-status-ok" />
      ) : (
        <XCircle size={10} className="meta-status-error" />
      )}
      <span className="agent-tool-row-label">{trace.label}</span>
      {filePath && (
        <InlineFileRef path={filePath} projectRoot={projectRoot ?? undefined} size={10} />
      )}
      {!filePath && ctx && <span className="agent-tool-row-ctx">{ctx}</span>}
    </div>
  );
}

/** A collapsible summary of a completed tool burst. */
function CollapsedToolBurst({ burst, projectRoot }: { burst: ToolBurst; projectRoot: string | null }) {
  const [expanded, setExpanded] = useState(false);
  const count = burst.traces.length;
  const failCount = burst.traces.filter(t => !t.ok).length;

  return (
    <div className="agent-tool-burst">
      <button
        type="button"
        className="agent-tool-burst-toggle"
        onClick={() => setExpanded(v => !v)}
      >
        {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
        <span>
          {count} {count === 1 ? 'action' : 'actions'}
          {failCount > 0 && <span className="agent-tool-burst-fail"> · {failCount} failed</span>}
        </span>
      </button>
      {expanded && (
        <div className="agent-tool-list">
          {burst.traces.map((t, i) => (
            <ToolRow key={i} trace={t} projectRoot={projectRoot} />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Reasoning block — renders model "thinking" above tool activity.
 *
 * Live-streams while the message is pending, auto-collapses once the
 * final assistant text arrives. Header shows a shimmering Sparkles
 * icon and a terse label; body is the full thinking text, scrolled to
 * keep the latest content in view.
 */
function ReasoningBlock({ reasoning, pending, hasFinalText, projectRoot }: {
  reasoning: string;
  pending: boolean;
  hasFinalText: boolean;
  projectRoot: string | null;
}) {
  const streaming = pending && !hasFinalText;
  const [userToggled, setUserToggled] = useState<boolean | null>(null);
  const autoExpanded = streaming;
  const expanded = userToggled ?? autoExpanded;

  const bodyRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (expanded && streaming && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [reasoning, expanded, streaming]);

  const trimmed = reasoning.trim();
  // Preserve hook order across renders — bail after all hooks have run.
  const { content: processed, tableTitles } = useMemo(
    () => preprocessMarkdown(trimmed),
    [trimmed],
  );
  const components = useMarkdownComponents(projectRoot, tableTitles);
  const preview = useMemo(() => {
    const last = trimmed.split(/\n+/).filter(l => l.trim().length > 0).pop() ?? '';
    // Replace [[type:value|label]] refs with their label (or value) so the
    // collapsed preview reads like prose instead of raw markup.
    const deref = last.replace(
      /\[\[\w+:([^|\x1F\]]+?)(?:[|\x1F]([^\]]+?))?\]\]/g,
      (_m, value, label) => (label || value).trim(),
    );
    return deref.replace(/[#*_`>]/g, '').slice(0, 96);
  }, [trimmed]);

  if (!trimmed) return null;

  const label = streaming ? 'Thinking' : 'Thought';

  return (
    <div className={`agent-reasoning-block${streaming ? ' streaming' : ''}${expanded ? ' expanded' : ''}`}>
      <button
        type="button"
        className="agent-reasoning-header"
        onClick={() => setUserToggled(v => !(v ?? autoExpanded))}
      >
        <MessageCircleMore size={11} className="agent-reasoning-icon" />
        <span className="agent-reasoning-label">{label}</span>
        {!expanded && preview && (
          <span className="agent-reasoning-preview">{preview}</span>
        )}
        <ChevronDown size={11} className={`agent-reasoning-chevron${expanded ? ' open' : ''}`} />
      </button>
      {expanded && (
        <div className="agent-reasoning-body" ref={bodyRef}>
          <div className="agent-reasoning-content agent-markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
              {processed}
            </ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}

/** Inline tool activity shown on assistant messages. */
function InlineToolActivity({ message, projectRoot }: { message: AgentMessage; projectRoot: string | null }) {
  const traces = message.toolTraces;
  if (!traces || traces.length === 0) return null;

  const isPending = !!message.pending;
  const bursts = groupToolBursts(traces);

  return (
    <>
      {bursts.map((burst, i) =>
        burst.active && isPending ? (
          <div key={i} className="agent-tool-list active">
            {burst.traces.map((t, j) => (
              <ToolRow key={j} trace={t} projectRoot={projectRoot} />
            ))}
          </div>
        ) : (
          <CollapsedToolBurst key={i} burst={burst} projectRoot={projectRoot} />
        )
      )}
    </>
  );
}

export function AgentMessagesView({
  messagesRef,
  messages,
  projectRoot,
  onSubmitDesignQuestions,
}: AgentMessagesViewProps) {
  return (
    <>
      <div className="agent-chat-messages" ref={messagesRef}>
        {messages.map((message) => {
          const isStreaming = message.pending && message.role === 'assistant' && message.content.trim().length > 0;
          return (
            <div key={message.id} className={`agent-message-row ${message.role} ${message.pending ? 'pending' : ''} ${isStreaming ? 'streaming' : ''}`}>
              {message.errorContext && (
                <ErrorContextCard ctx={message.errorContext} />
              )}
              {message.role === 'assistant' && message.toolTraces && message.toolTraces.length > 0 && (
                <InlineToolActivity message={message} projectRoot={projectRoot} />
              )}
              {message.role === 'assistant' && message.reasoning && message.reasoning.trim().length > 0 && (
                <ReasoningBlock
                  reasoning={message.reasoning}
                  pending={!!message.pending}
                  hasFinalText={message.content.trim().length > 0}
                  projectRoot={projectRoot}
                />
              )}
              {message.content.trim().length > 0 && !message.errorContext && (!message.pending || isStreaming) && (
                <MessageBubble content={message.content} projectRoot={projectRoot} streaming={isStreaming} />
              )}
              {message.designQuestions && !message.pending && (
                <DesignQuestionsCard
                  data={message.designQuestions}
                  onSubmit={onSubmitDesignQuestions}
                />
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}
