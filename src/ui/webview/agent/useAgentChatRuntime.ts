import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FileNode, ModuleDefinition, UiAgentMessageData } from '../../protocol/generated-types';
import { AGENT_MODEL_OPTIONS, agentApi } from './api';
import { collectChangedFilesSummary, type AgentChangedFile } from './components/viewHelpers';
import { type AgentError, sanitizeAgentError } from './runtime/errors';
import { useAgentComposerState } from './runtime/useAgentComposerState';
import { flattenFileNodes } from './runtime/shared';
import { useAgentRunState } from './runtime/useAgentRunState';
import { useAgentSessionState, isSessionSending } from './runtime/useAgentSessionState';
import { WebviewRpcClient, rpcClient } from '../common/webviewRpcClient';

const TRANSIENT_ERROR_DISMISS_MS = 6000;

export function useAgentChatRuntime(
  projectRoot: string | null,
  selectedTargets: string[],
  projectModules: ModuleDefinition[],
  projectFileNodes: FileNode[],
) {
  const [transientError, setTransientError] = useState<AgentError | null>(null);

  const reportError = useCallback((err: unknown | null) => {
    if (err === null || err === undefined) {
      setTransientError(null);
      return;
    }
    // Keep the raw error in the console so we can still debug sanitized reports.
    console.error('[agent] error:', err);
    setTransientError(sanitizeAgentError(err));
  }, []);
  const [contextWindow, setContextWindow] = useState<{ usedTokens: number; limitTokens: number } | null>(null);
  const [modelName, setModelName] = useState<string | null>(null);
  const [compactionNotice, setCompactionNotice] = useState<{ nonce: number; status: string; detail: string | null } | null>(null);
  const compactionNoticeTimerRef = useRef<number | null>(null);
  const agentData = WebviewRpcClient.useSubscribe('agentData');

  const projectFiles = useMemo(() => flattenFileNodes(projectFileNodes), [projectFileNodes]);
  const composerState = useAgentComposerState(projectModules, projectFiles);
  const { setMentionToken, setMentionIndex } = composerState;

  const resetChatUiState = useCallback(() => {
    setMentionToken(null);
    setMentionIndex(0);
    setContextWindow(null);
    setCompactionNotice(null);
    if (compactionNoticeTimerRef.current !== null) {
      window.clearTimeout(compactionNoticeTimerRef.current);
      compactionNoticeTimerRef.current = null;
    }
  }, [setMentionIndex, setMentionToken]);

  const sessionState = useAgentSessionState({
    agentData,
    projectRoot,
  });

  const activeSession = sessionState.activeSession;
  const sessionId = activeSession?.sessionId ?? null;
  const messages = activeSession?.messages ?? [];
  const checklist = activeSession?.checklist ?? null;
  const sending = activeSession ? isSessionSending(activeSession) : false;
  const isStopping = activeSession?.activeRunStopRequested ?? false;
  // Clear stale transient errors when switching sessions
  useEffect(() => { setTransientError(null); }, [sessionId]);

  // Auto-dismiss non-persistent errors so a failed send doesn't stick forever.
  useEffect(() => {
    if (!transientError || transientError.persistent) return;
    const id = window.setTimeout(() => setTransientError(null), TRANSIENT_ERROR_DISMISS_MS);
    return () => window.clearTimeout(id);
  }, [transientError]);

  const error: AgentError | null = useMemo(() => {
    if (activeSession?.error) return sanitizeAgentError(activeSession.error);
    return transientError;
  }, [activeSession?.error, transientError]);

  const runState = useAgentRunState({
    projectRoot,
    selectedTargets,
    input: composerState.input,
    setInput: composerState.setInput,
    sessionId,
    activeSession,
    compactionNoticeTimerRef,
    setCompactionNotice,
    setContextWindow,
    setModelName,
    setMentionToken: composerState.setMentionToken,
    setMentionIndex: composerState.setMentionIndex,
    reportError,
  });

  const changedFilesSummary = useMemo(() => collectChangedFilesSummary(messages), [messages]);
  const contextUsage = useMemo(() => {
    if (!contextWindow || contextWindow.limitTokens <= 0) return null;
    const used = Math.min(contextWindow.usedTokens, contextWindow.limitTokens);
    const usedPercent = Math.round((used / contextWindow.limitTokens) * 100);
    return { usedTokens: used, limitTokens: contextWindow.limitTokens, usedPercent, leftPercent: 100 - usedPercent };
  }, [contextWindow]);
  const openFileDiff = useCallback((file: AgentChangedFile) => {
    void rpcClient?.requestAction("vscode.openDiff", {
      path: file.payload.path,
      beforeContent: file.payload.before_content,
      afterContent: file.payload.after_content,
      title: `Agent edit diff: ${file.payload.path}`,
    });
  }, []);

  // Track elapsed time for current turn
  const turnStartRef = useRef<number | null>(null);
  const [turnElapsed, setTurnElapsed] = useState<number>(0);

  useEffect(() => {
    if (sending) {
      if (turnStartRef.current === null) {
        turnStartRef.current = Date.now();
      }
      const tick = () => {
        if (turnStartRef.current !== null) {
          setTurnElapsed(Math.floor((Date.now() - turnStartRef.current) / 1000));
        }
      };
      tick();
      const interval = window.setInterval(tick, 1000);
      return () => window.clearInterval(interval);
    }
    turnStartRef.current = null;
    setTurnElapsed(0);
  }, [sending]);

const statusClass = sending || isStopping ? 'working' : sessionState.isReady ? 'ready' : 'idle';
  const statusText = (sending || isStopping) ? 'Working' : sessionState.isReady ? 'Ready' : 'Idle';

  // Keep modelName in sync with the session's persisted model. modelName is
  // the source of truth for the dropdown; this effect only fires when the
  // session's model actually changes (session switch, external store update,
  // or post-RPC confirmation) — never because we just updated modelName
  // locally, which would revert the optimistic click.
  // Keep modelName in sync with backend-owned state. Prefer the active
  // session's model; otherwise fall back to the Agent-wide default so the
  // dropdown shows the right value before any session has been created.
  const backendModel = activeSession?.model || agentData.defaultModel || null;
  useEffect(() => {
    if (backendModel) {
      setModelName(backendModel);
    }
  }, [backendModel]);

  const setModel = useCallback(
    (modelId: string) => {
      setModelName(modelId);
      if (sessionId) {
        agentApi.setModel(sessionId, modelId);
      }
    },
    [sessionId],
  );

  const setInput = useCallback((value: string) => {
    setTransientError(null);
    composerState.setInput(value);
  }, [composerState]);

  const startNewChat = useCallback(() => {
    if (!projectRoot) return;
    setTransientError(null);
    resetChatUiState();
    sessionState.createAndActivateChat(projectRoot);
  }, [projectRoot, resetChatUiState, sessionState]);

  const dismissError = useCallback(() => setTransientError(null), []);

  return {
    activeChatId: sessionState.activeChatId,
    sessionId,
    messages,
    checklist,
    input: composerState.input,
    isSending: sending,
    isStopping,
    error,
    dismissError,
    compactionNotice,
    mentionToken: composerState.mentionToken,
    mentionItems: composerState.mentionItems,
    mentionIndex: composerState.mentionIndex,
    composerInputRef: composerState.composerInputRef,
    projectChats: sessionState.projectChatsForHistory,
    isReady: sessionState.isReady,
    headerTitle: sessionState.headerTitle,
    activeChatTitle: sessionState.activeChatTitle,
    changedFilesSummary,
    contextUsage,
    modelName,
    modelOptions: AGENT_MODEL_OPTIONS,
    setModel,
    canChangeModel: !sending && !isStopping,
    statusClass,
    statusText,
    turnElapsed,
    setInput,
    setMentionToken: composerState.setMentionToken,
    setMentionIndex: composerState.setMentionIndex,
    startNewChat,
    refreshMentionFromInput: composerState.refreshMentionFromInput,
    insertMention: composerState.insertMention,
    stopRun: runState.stopRun,
    sendSteeringMessage: runState.sendSteeringMessage,
    sendMessage: runState.sendMessage,
    activateChat: sessionState.activateChat,
    openFileDiff,
  };
}
