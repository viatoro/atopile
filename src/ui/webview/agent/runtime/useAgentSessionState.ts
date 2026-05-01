import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { UiAgentData, UiAgentSessionData } from '../../../protocol/generated-types';
import {
  DEFAULT_CHAT_TITLE,
  deriveChatTitle,
  shortProjectName,
} from '../AgentChatPanel.helpers';
import { pathKey, samePath } from '../../../protocol/paths';
import { rpcClient } from '../../common/webviewRpcClient';

/** Cache of generated titles per session — persists across re-renders */
const generatedTitles = new Map<string, string>();
/** Sessions we've already requested a title for */
const titleRequested = new Set<string>();

interface SessionDeps {
  agentData: UiAgentData;
  projectRoot: string | null;
}

/** Is this session currently running an agent turn? */
export function isSessionSending(session: UiAgentSessionData): boolean {
  return session.activeRunId != null && session.activeRunStatus === 'running';
}

export function sessionTitle(session: UiAgentSessionData): string {
  return generatedTitles.get(session.sessionId) || deriveChatTitle(session.messages) || DEFAULT_CHAT_TITLE;
}

export function useAgentSessionState({
  agentData,
  projectRoot,
}: SessionDeps) {
  // The only local state: which session is active, tracked per-project
  const [activeChatByProject, setActiveChatByProject] = useState<Record<string, string | null>>({});

  const hasUserMessage = (s: UiAgentSessionData) =>
    s.messages.some((m) => m.role === "user");

  const projectSessions = useMemo(
    () => agentData.sessions
      .filter((s) => projectRoot !== null && samePath(s.projectRoot, projectRoot))
      .sort((a, b) => b.updatedAt - a.updatedAt),
    [agentData.sessions, projectRoot],
  );

  // For the history drawer — only show sessions with user messages
  const projectChatsForHistory = useMemo(
    () => projectSessions.filter(hasUserMessage),
    [projectSessions],
  );

  const key = projectRoot ? pathKey(projectRoot) : null;
  const explicitChoice = key !== null && key in activeChatByProject;
  const activeChatId = !key ? null
    : explicitChoice ? activeChatByProject[key]   // null = new chat, string = specific session
    : projectSessions[0]?.sessionId ?? null;       // no choice yet — default to newest

  const activeSession = useMemo(
    () => (activeChatId ? projectSessions.find((s) => s.sessionId === activeChatId) ?? null : null),
    [activeChatId, projectSessions],
  );

  const isReady = Boolean(projectRoot);
  const headerTitle = useMemo(() => shortProjectName(projectRoot), [projectRoot]);
  const [titleVersion, setTitleVersion] = useState(0);
  const activeChatTitle = activeSession ? sessionTitle(activeSession) : DEFAULT_CHAT_TITLE;

  // Generate a title via LLM after the first assistant response
  useEffect(() => {
    if (!activeSession) return;
    const sid = activeSession.sessionId;
    if (generatedTitles.has(sid) || titleRequested.has(sid)) return;
    const firstUser = activeSession.messages.find((m) => m.role === 'user');
    const hasAssistant = activeSession.messages.some((m) => m.role === 'assistant' && m.content.trim().length > 0);
    if (!firstUser || !hasAssistant) return;

    titleRequested.add(sid);
    rpcClient?.requestAction<{ title: string }>('generateChatTitle', {
      message: firstUser.content,
    }).then(
      (result) => {
        if (result?.title) {
          generatedTitles.set(sid, result.title);
          setTitleVersion((v) => v + 1); // trigger re-render
        }
      },
      () => {}, // silently fail — keep the fallback title
    );
  }, [activeSession?.sessionId, activeSession?.messages.length]);

  const activateChat = useCallback((sessionId: string) => {
    const session = agentData.sessions.find((s) => s.sessionId === sessionId);
    if (!session) return;
    setActiveChatByProject((prev) => ({ ...prev, [pathKey(session.projectRoot)]: sessionId }));
  }, [agentData.sessions]);

  const startNewChat = useCallback((root: string) => {
    setActiveChatByProject((prev) => ({ ...prev, [pathKey(root)]: null }));
  }, []);

  // Auto-select the newest session when a new one appears for the current project
  const prevSessionIdsRef = useRef(new Set<string>());
  useEffect(() => {
    const currentIds = new Set(projectSessions.map((s) => s.sessionId));
    const newSession = projectRoot
      ? projectSessions.find((s) => !prevSessionIdsRef.current.has(s.sessionId))
      : null;
    prevSessionIdsRef.current = currentIds;

    if (newSession && projectRoot) {
      setActiveChatByProject((prev) => ({ ...prev, [pathKey(projectRoot)]: newSession.sessionId }));
    }
  }, [projectSessions, projectRoot]);

  return {
    activeChatId,
    activeSession,
    projectSessions,
    projectChatsForHistory,
    isReady,
    headerTitle,
    activeChatTitle,
    activateChat,
    createAndActivateChat: startNewChat,
  };
}
