import { useCallback, useEffect, type MutableRefObject } from 'react';
import type { UiAgentSessionData } from '../../../protocol/generated-types';
import { addAgentProgressListener, agentApi } from '../api';
import { readProgressPayload } from '../state/progress';
import { isSessionSending } from './useAgentSessionState';

interface RunStateDeps {
  projectRoot: string | null;
  selectedTargets: string[];
  input: string;
  setInput: (value: string) => void;
  sessionId: string | null;
  activeSession: UiAgentSessionData | null;
  compactionNoticeTimerRef: MutableRefObject<number | null>;
  setCompactionNotice: (value: { nonce: number; status: string; detail: string | null } | null | ((current: { nonce: number; status: string; detail: string | null } | null) => { nonce: number; status: string; detail: string | null } | null)) => void;
  setContextWindow: (value: { usedTokens: number; limitTokens: number } | null) => void;
  setModelName: (value: string | null) => void;
  setMentionToken: (value: null) => void;
  setMentionIndex: (value: number) => void;
  reportError: (err: unknown | null) => void;
}

export function useAgentRunState({
  projectRoot,
  selectedTargets,
  input,
  setInput,
  sessionId,
  activeSession,
  compactionNoticeTimerRef,
  setCompactionNotice,
  setContextWindow,
  setModelName,
  setMentionToken,
  setMentionIndex,
  reportError,
}: RunStateDeps) {
  useEffect(() => {
    return () => {
      if (compactionNoticeTimerRef.current !== null) {
        window.clearTimeout(compactionNoticeTimerRef.current);
        compactionNoticeTimerRef.current = null;
      }
    };
  }, [compactionNoticeTimerRef]);

  // Listen for progress events matching the active session.
  // Re-registers when sessionId changes — no stale-closure refs needed.
  useEffect(() => {
    if (!sessionId) return;
    return addAgentProgressListener((payload) => {
      const parsed = readProgressPayload(payload);
      if (parsed.sessionId !== sessionId) return;

      const usedTokens = parsed.inputTokens ?? parsed.totalTokens;
      const limitTokens = parsed.contextLimitTokens;
      if (typeof usedTokens === 'number' && typeof limitTokens === 'number' && limitTokens > 0) {
        setContextWindow({ usedTokens: Math.min(usedTokens, limitTokens), limitTokens });
      }

      if (parsed.response?.model) {
        setModelName(parsed.response.model);
      }

      if (parsed.phase === 'compacting') {
        const status = parsed.statusText || 'Compacting context';
        const nonce = Date.now();
        setCompactionNotice({ nonce, status, detail: parsed.detailText });
        window.clearTimeout(compactionNoticeTimerRef.current!);
        compactionNoticeTimerRef.current = window.setTimeout(() => {
          setCompactionNotice((current) => (current && current.nonce === nonce ? null : current));
          compactionNoticeTimerRef.current = null;
        }, 8000);
      }
    });
  }, [sessionId, compactionNoticeTimerRef, setCompactionNotice, setContextWindow, setModelName]);

  const sending = activeSession ? isSessionSending(activeSession) : false;

  // Keep contextWindow visible after turn ends — only clear on session change

  const stopRun = useCallback(() => {
    if (!sessionId || !activeSession?.activeRunId || !sending) return;
    reportError(null);
    try {
      agentApi.cancelRun(sessionId, activeSession.activeRunId);
    } catch (stopError: unknown) {
      reportError(stopError);
    }
  }, [activeSession, sending, sessionId, reportError]);

  const sendSteeringMessage = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || !sessionId || !activeSession?.activeRunId || !sending) return;
    reportError(null);
    setInput('');
    setMentionToken(null);
    setMentionIndex(0);
    try {
      agentApi.steerRun(sessionId, activeSession.activeRunId, { message: trimmed });
    } catch (steerError: unknown) {
      reportError(steerError);
    }
  }, [activeSession, sending, input, sessionId, setInput, setMentionIndex, setMentionToken, reportError]);

  const sendMessage = useCallback((options?: string | { directMessage?: string; hideUserMessage?: boolean }) => {
    const directMessage = typeof options === 'string' ? options : options?.directMessage;
    const trimmed = (directMessage ?? input).trim();
    if (!trimmed || !projectRoot || sending) return;
    reportError(null);
    setInput('');
    setMentionToken(null);
    setMentionIndex(0);
    try {
      if (!sessionId) {
        // No session yet — create one with the initial message
        agentApi.createSession(projectRoot, trimmed);
      } else {
        agentApi.createRun(sessionId, { message: trimmed, projectRoot, selectedTargets });
      }
    } catch (sendError: unknown) {
      reportError(sendError);
    }
  }, [activeSession, sending, input, projectRoot, selectedTargets, sessionId, setInput, setMentionIndex, setMentionToken, reportError]);

  return {
    stopRun,
    sendSteeringMessage,
    sendMessage,
  };
}
