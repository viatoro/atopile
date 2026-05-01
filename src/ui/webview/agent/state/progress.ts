import type { SendMessageResponse, ToolTraceResponse as AgentToolTrace } from '../api';
import type {
  AgentChecklist,
  AgentChecklistItem,
  AgentEditDiffUiPayload,
  AgentMessage,
  AgentProgressPayload,
  AgentProgressPhase,
  AgentTraceView,
  DesignQuestion,
  DesignQuestionsData,
} from './types';

function toFiniteNumber(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return value;
}

function normalizeProgressPhase(value: unknown): AgentProgressPhase | null {
  if (
    value === 'thinking'
    || value === 'text_delta'
    || value === 'reasoning_delta'
    || value === 'tool_start'
    || value === 'tool_end'
    || value === 'done'
    || value === 'stopped'
    || value === 'error'
    || value === 'compacting'
    || value === 'design_questions'
  ) {
    return value;
  }
  return null;
}

function parseChecklist(raw: unknown): AgentChecklist | null {
  if (!raw || typeof raw !== 'object') return null;
  const obj = raw as Record<string, unknown>;
  const items = Array.isArray(obj.items) ? obj.items : [];
  const parsed: AgentChecklistItem[] = [];
  for (const item of items) {
    if (!item || typeof item !== 'object') continue;
    const it = item as Record<string, unknown>;
    if (typeof it.id !== 'string' || typeof it.description !== 'string') continue;
    parsed.push({
      id: it.id,
      description: it.description as string,
      status: (['pending', 'in_progress', 'done', 'blocked'].includes(it.status as string)
        ? it.status
        : 'pending') as AgentChecklistItem['status'],
    });
  }
  if (parsed.length === 0) return null;
  return { items: parsed };
}

function parseDesignQuestions(payload: AgentProgressPayload): DesignQuestionsData | null {
  if (!Array.isArray(payload.questions)) return null;
  const questions: DesignQuestion[] = [];
  for (const raw of payload.questions) {
    if (!raw || typeof raw !== 'object') continue;
    const q = raw as Record<string, unknown>;
    const id = typeof q.id === 'string' ? q.id : null;
    const question = typeof q.question === 'string' ? q.question : null;
    if (!id || !question) continue;
    const options = Array.isArray(q.options)
      ? (q.options as unknown[]).filter((o): o is string => typeof o === 'string')
      : undefined;
    const defaultOpt = typeof q.default === 'string' ? q.default : undefined;
    questions.push({ id, question, options: options?.length ? options : undefined, default: defaultOpt });
  }
  if (questions.length === 0) return null;
  const context = typeof payload.context === 'string' ? payload.context : '';
  return { context, questions };
}

function parseSendMessageResponse(raw: unknown): SendMessageResponse | null {
  if (!raw || typeof raw !== 'object') return null;
  const obj = raw as Record<string, unknown>;
  if (
    typeof obj.sessionId !== 'string'
    || typeof obj.assistantMessage !== 'string'
    || typeof obj.model !== 'string'
    || !Array.isArray(obj.toolTraces)
    || !Array.isArray(obj.toolSuggestions)
    || !Array.isArray(obj.toolMemory)
  ) {
    return null;
  }
  return obj as unknown as SendMessageResponse;
}

export function readProgressPayload(detail: unknown): {
  sessionId: string | null;
  runId: string | null;
  phase: AgentProgressPhase | null;
  callId: string | null;
  trace: AgentToolTrace | null;
  name: string | null;
  args: Record<string, unknown>;
  statusText: string | null;
  detailText: string | null;
  delta: string | null;
  loop: number | null;
  toolIndex: number | null;
  toolCount: number | null;
  inputTokens: number | null;
  totalTokens: number | null;
  contextLimitTokens: number | null;
  checklist: AgentChecklist | null;
  designQuestions: DesignQuestionsData | null;
  response: SendMessageResponse | null;
  error: string | null;
} {
  if (!detail || typeof detail !== 'object') {
    return {
      sessionId: null,
      runId: null,
      phase: null,
      callId: null,
      trace: null,
      name: null,
      args: {},
      statusText: null,
      detailText: null,
      delta: null,
      loop: null,
      toolIndex: null,
      toolCount: null,
      inputTokens: null,
      totalTokens: null,
      contextLimitTokens: null,
      checklist: null,
      designQuestions: null,
      response: null,
      error: null,
    };
  }

  const payload = detail as AgentProgressPayload;
  const phase = normalizeProgressPhase(payload.phase);
  const sessionId = typeof payload.session_id === 'string' ? payload.session_id : null;
  const runId = typeof payload.run_id === 'string' ? payload.run_id : null;
  const callId = typeof payload.call_id === 'string' ? payload.call_id : null;
  const name = typeof payload.name === 'string' ? payload.name : null;
  const args = payload.args && typeof payload.args === 'object'
    ? payload.args as Record<string, unknown>
    : {};
  const statusText = typeof payload.status_text === 'string' ? payload.status_text : null;
  const detailText = typeof payload.detail_text === 'string' ? payload.detail_text : null;
  const delta = typeof payload.delta === 'string' ? payload.delta : null;
  const loop = toFiniteNumber(payload.loop);
  const toolIndex = toFiniteNumber(payload.tool_index);
  const toolCount = toFiniteNumber(payload.tool_count);

  const usage = payload.usage && typeof payload.usage === 'object'
    ? payload.usage as Record<string, unknown>
    : null;
  const inputTokens = toFiniteNumber(payload.input_tokens)
    ?? (usage ? toFiniteNumber(usage.input_tokens) : null)
    ?? (usage ? toFiniteNumber(usage.inputTokens) : null);
  const totalTokens = toFiniteNumber(payload.total_tokens)
    ?? (usage ? toFiniteNumber(usage.total_tokens) : null)
    ?? (usage ? toFiniteNumber(usage.totalTokens) : null)
    ?? ((() => {
      const input = inputTokens;
      const output = toFiniteNumber(payload.output_tokens)
        ?? (usage ? toFiniteNumber(usage.output_tokens) : null)
        ?? (usage ? toFiniteNumber(usage.outputTokens) : null);
      if (input === null && output === null) return null;
      return (input ?? 0) + (output ?? 0);
    })());
  const contextLimitTokens = toFiniteNumber(payload.context_limit_tokens)
    ?? toFiniteNumber(payload.contextLimitTokens);

  const trace = payload.trace && typeof payload.trace === 'object'
    ? payload.trace as AgentToolTrace
    : null;

  const checklist = parseChecklist(payload.checklist);
  const designQuestions = phase === 'design_questions' ? parseDesignQuestions(payload) : null;
  const response = parseSendMessageResponse(payload.response);
  const error = typeof payload.error === 'string' ? payload.error : null;

  return {
    sessionId,
    runId,
    phase,
    callId,
    trace,
    name,
    args,
    statusText,
    detailText,
    delta,
    loop,
    toolIndex,
    toolCount,
    inputTokens,
    totalTokens,
    contextLimitTokens,
    checklist,
    designQuestions,
    response,
    error,
  };
}

export function readTraceDiff(trace: Pick<AgentTraceView, 'result'>): { added: number; removed: number } | null {
  const raw = trace.result.diff;
  if (!raw || typeof raw !== 'object') return null;
  const diff = raw as Record<string, unknown>;
  const added = typeof diff.added_lines === 'number' ? diff.added_lines : null;
  const removed = typeof diff.removed_lines === 'number' ? diff.removed_lines : null;
  if (added == null || removed == null) return null;
  return { added, removed };
}

export function readTraceEditDiffPayload(trace: Pick<AgentTraceView, 'result'>): AgentEditDiffUiPayload | null {
  const rawUi = trace.result._ui;
  if (!rawUi || typeof rawUi !== 'object') return null;
  const ui = rawUi as Record<string, unknown>;
  const rawEditDiff = ui.edit_diff;
  if (!rawEditDiff || typeof rawEditDiff !== 'object') return null;
  const editDiff = rawEditDiff as Record<string, unknown>;

  const path = typeof editDiff.path === 'string' ? editDiff.path : null;
  const before = typeof editDiff.before_content === 'string' ? editDiff.before_content : null;
  const after = typeof editDiff.after_content === 'string' ? editDiff.after_content : null;

  if (!path || before == null || after == null) return null;
  return {
    path,
    before_content: before,
    after_content: after,
  };
}
