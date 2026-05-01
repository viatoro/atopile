import { useMemo, useRef, useEffect, type KeyboardEvent, type RefObject, type ReactNode } from 'react';
import { CornerDownLeft } from 'lucide-react';
import type { MentionItem, MentionToken } from '../runtime/shared';
import { FileIcon } from '../../common/utils/fileIcons';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '../../common/components/Select';
import { typeIcon } from '../../common/components/TypeIcon';

const AT_MENTION_INPUT_RE = /(?:^|\s)(@[\w./-]+\w)/g;

/** Render input text with @mentions highlighted as rich inline badges */
function renderInputHighlights(text: string): ReactNode {
  if (!text) return '\u00A0'; // nbsp keeps the overlay sized
  const segments: ReactNode[] = [];
  let lastIndex = 0;
  AT_MENTION_INPUT_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = AT_MENTION_INPUT_RE.exec(text)) !== null) {
    const full = m[1]; // "@token"
    const token = full.slice(1);
    const mentionStart = m.index + (m[0].length - full.length);
    if (mentionStart > lastIndex) {
      segments.push(text.slice(lastIndex, mentionStart));
    }
    const isFile = token.includes('/') || token.includes('.');
    segments.push(
      <span key={mentionStart} className={isFile ? 'composer-mention-file' : 'composer-mention-module'}>
        {isFile ? (
          <FileIcon name={token} size={11} />
        ) : (
          <span className="type-icon type-module">{typeIcon('module', 10)}</span>
        )}
        <span>{token}</span>
      </span>,
    );
    lastIndex = mentionStart + full.length;
  }
  if (lastIndex < text.length) {
    segments.push(text.slice(lastIndex));
  }
  // Trailing newline needs a visible character to maintain height
  if (text.endsWith('\n')) segments.push('\u00A0');
  return <>{segments}</>;
}

export function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export interface AgentModelOption {
  id: string;
  label: string;
}

interface AgentComposerProps {
  composerInputRef: RefObject<HTMLTextAreaElement | null>;
  input: string;
  mentionToken: MentionToken | null;
  mentionItems: MentionItem[];
  mentionIndex: number;
  isAuthenticated: boolean;
  projectRoot: string | null;
  isReady: boolean;
  isSending: boolean;
  isStopping: boolean;
  contextUsage: {
    usedTokens: number;
    limitTokens: number;
    usedPercent: number;
    leftPercent: number;
  } | null;
  modelName: string | null;
  modelOptions: readonly AgentModelOption[];
  canChangeModel: boolean;
  onModelChange: (modelId: string) => void;
  onInputChange: (nextValue: string, textarea: HTMLTextAreaElement) => void;
  onInputClick: (value: string, caret: number) => void;
  onInputKeyUp: (key: string, value: string, caret: number) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onInsertMention: (item: MentionItem) => void;
  onSend: () => void;
  onStop: () => void;
}

export function AgentComposer({
  composerInputRef,
  input,
  mentionToken,
  mentionItems,
  mentionIndex,
  isAuthenticated,
  projectRoot,
  isReady,
  isSending,
  isStopping,
  contextUsage,
  modelName,
  modelOptions,
  canChangeModel,
  onModelChange,
  onInputChange,
  onInputClick,
  onInputKeyUp,
  onInsertMention,
  onKeyDown,
  onSend,
  onStop,
}: AgentComposerProps) {
  const canCompose = isAuthenticated && isReady;
  const highlightRef = useRef<HTMLDivElement | null>(null);
  const hasMentions = input.includes('@');
  const highlights = useMemo(() => hasMentions ? renderInputHighlights(input) : null, [input, hasMentions]);

  // Build items for the headless Select. Include the current model as an
  // unknown entry if it isn't in the canonical list (e.g. old session with a
  // retired model id) so it still renders in the trigger.
  const selectItems = useMemo(() => {
    const items = modelOptions.map((o) => ({ label: o.label, value: o.id }));
    if (modelName && !modelOptions.some((o) => o.id === modelName)) {
      items.unshift({ label: modelName, value: modelName });
    }
    return items;
  }, [modelOptions, modelName]);

  // Sync scroll between textarea and highlight overlay
  useEffect(() => {
    const textarea = composerInputRef.current;
    const overlay = highlightRef.current;
    if (!textarea || !overlay) return;
    const syncScroll = () => {
      overlay.scrollTop = textarea.scrollTop;
      overlay.scrollLeft = textarea.scrollLeft;
    };
    textarea.addEventListener('scroll', syncScroll);
    return () => textarea.removeEventListener('scroll', syncScroll);
  }, [composerInputRef]);

  return (
    <div className="agent-chat-composer-wrap">
      {mentionToken && (
        <div className="agent-mention-menu" role="listbox" aria-label="Mention suggestions">
          {mentionItems.length > 0 ? (
            mentionItems.map((item, index) => (
              <button
                key={`${item.kind}:${item.token}`}
                type="button"
                className={`agent-mention-item ${index === mentionIndex ? 'active' : ''}`}
                onMouseDown={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                }}
                onClick={() => onInsertMention(item)}
              >
                <span className="agent-mention-icon">
                  {item.kind === 'file' ? (
                    <FileIcon name={item.label} size={13} />
                  ) : (
                    <span className={`type-icon type-${item.subtitle || 'module'}`}>
                      {typeIcon(item.subtitle || 'module', 13)}
                    </span>
                  )}
                </span>
                <span className="agent-mention-label">{item.label}</span>
                {item.subtitle && (
                  <span className="agent-mention-subtitle">{item.subtitle}</span>
                )}
              </button>
            ))
          ) : (
            <div className="agent-mention-empty" role="status">
              No matches
            </div>
          )}
        </div>
      )}

      <div className={`agent-chat-input-shell ${highlights ? 'has-mentions' : ''}`}>
        {highlights && (
          <div
            ref={highlightRef}
            className="agent-chat-input-highlights"
            aria-hidden="true"
          >
            {highlights}
          </div>
        )}
        <textarea
          ref={composerInputRef}
          className={`agent-chat-input ${highlights ? 'agent-chat-input-transparent' : ''}`}
          value={input}
          onChange={(event) => onInputChange(event.target.value, event.target)}
          onClick={(event) => {
            onInputClick(
              event.currentTarget.value,
              event.currentTarget.selectionStart ?? event.currentTarget.value.length,
            );
          }}
          onKeyUp={(event) => {
            onInputKeyUp(
              event.key,
              event.currentTarget.value,
              event.currentTarget.selectionStart ?? event.currentTarget.value.length,
            );
          }}
          placeholder={
            !isAuthenticated
              ? 'Sign in to use the agent'
              : projectRoot
              ? 'Ask the agent...'
              : 'Select a project first'
          }
          disabled={!canCompose}
          rows={1}
          onKeyDown={onKeyDown}
        />
        {isSending ? (
          <button
            type="button"
            className={`agent-composer-action-btn agent-composer-stop-btn ${isStopping ? 'stopping' : ''}`}
            onClick={onStop}
            disabled={isStopping}
            title={isStopping ? 'Stopping...' : 'Stop'}
            aria-label={isStopping ? 'Stopping agent' : 'Stop agent'}
          >
            <svg width="12" height="12" viewBox="0 0 14 14" fill="currentColor" aria-hidden="true">
              <rect x="2" y="2" width="10" height="10" rx="2" />
            </svg>
          </button>
        ) : input.trim().length > 0 && canCompose ? (
          <button
            type="button"
            className="agent-composer-action-btn agent-composer-send-btn"
            onClick={onSend}
            title="Send (Enter)"
            aria-label="Send message"
          >
            <CornerDownLeft size={14} aria-hidden="true" />
          </button>
        ) : null}
      </div>

      {/* Footer — [future settings on left] ... [context · model on right] */}
      <div className="agent-composer-footer">
        <div className="agent-composer-footer-left">
          {/* reserved for future settings / tool toggles */}
        </div>
        <div className="agent-composer-footer-right">
          {contextUsage && (
            <div className="agent-status-context">
              <span
                className={`agent-status-context-bar ${
                  contextUsage.usedPercent >= 90 ? 'danger' : contextUsage.usedPercent >= 70 ? 'warning' : ''
                }`}
                aria-hidden="true"
              >
                <span
                  className="agent-status-context-fill"
                  style={{ width: `${contextUsage.usedPercent}%` }}
                />
              </span>
              <span className="agent-status-context-pct">{contextUsage.usedPercent}%</span>
            </div>
          )}
          <Select
            className="agent-composer-model-select"
            items={selectItems}
            value={modelName}
            onValueChange={(v) => {
              if (v) onModelChange(v);
            }}
            disabled={!canChangeModel}
          >
            <SelectTrigger className="agent-composer-model-trigger" aria-label="Select model">
              <SelectValue placeholder="Select model" />
            </SelectTrigger>
            <SelectContent className="agent-composer-model-content">
              <SelectLabel>Models</SelectLabel>
              {selectItems.map((item) => (
                <SelectItem key={item.value ?? ''} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
    </div>
  );
}
