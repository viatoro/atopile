import type { ToolTraceResponse as AgentToolTrace } from '../api';
import type { LogErrorContext } from '../../../protocol/types';

export type MessageRole = 'user' | 'assistant' | 'system';

export type AgentProgressPhase =
  | 'thinking'
  | 'text_delta'
  | 'reasoning_delta'
  | 'tool_start'
  | 'tool_end'
  | 'done'
  | 'stopped'
  | 'error'
  | 'compacting'
  | 'design_questions';

export interface AgentChecklistItem {
  id: string;
  description: string;
  status: 'pending' | 'in_progress' | 'done' | 'blocked';
}

export interface AgentChecklist {
  items: AgentChecklistItem[];
}

export interface DesignQuestion {
  id: string;
  question: string;
  options?: string[];
  default?: string;
}

export interface DesignQuestionsData {
  context: string;
  questions: DesignQuestion[];
}

export interface AgentProgressPayload {
  session_id?: unknown;
  run_id?: unknown;
  phase?: unknown;
  call_id?: unknown;
  name?: unknown;
  args?: unknown;
  trace?: unknown;
  status_text?: unknown;
  detail_text?: unknown;
  delta?: unknown;
  loop?: unknown;
  tool_index?: unknown;
  tool_count?: unknown;
  input_tokens?: unknown;
  output_tokens?: unknown;
  total_tokens?: unknown;
  context_limit_tokens?: unknown;
  contextLimitTokens?: unknown;
  usage?: unknown;
  checklist?: unknown;
  context?: unknown;
  questions?: unknown;
  response?: unknown;
  error?: unknown;
}

export interface AgentTraceView extends AgentToolTrace {
  callId?: string;
  running?: boolean;
}

export interface AgentEditDiffUiPayload {
  path: string;
  before_content: string;
  after_content: string;
}

export interface AgentMessage {
  id: string;
  role: MessageRole;
  content: string;
  pending?: boolean;
  reasoning?: string;
  toolTraces?: AgentTraceView[];
  checklist?: AgentChecklist | null;
  designQuestions?: DesignQuestionsData | null;
  errorContext?: LogErrorContext | null;
}
