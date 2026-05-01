import type { RefObject } from 'react';
import type { UiAgentSessionData } from '../../../protocol/generated-types';
import {
  formatChatTimestamp,
  summarizeChatPreview,
} from '../AgentChatPanel.helpers';
import { isSessionSending, sessionTitle } from '../runtime/useAgentSessionState';

interface AgentHistoryDrawerProps {
  projectRoot: string | null;
  projectChats: UiAgentSessionData[];
  activeChatId: string | null;
  isChatsPanelOpen: boolean;
  chatsPanelRef: RefObject<HTMLDivElement | null>;
  onClose: () => void;
  onActivateChat: (chatId: string) => void;
}

export function AgentHistoryDrawer({
  projectRoot,
  projectChats,
  activeChatId,
  isChatsPanelOpen,
  chatsPanelRef,
  onClose,
  onActivateChat,
}: AgentHistoryDrawerProps) {
  return (
    <>
      <aside className={`agent-chat-history-drawer ${isChatsPanelOpen ? 'open' : ''}`} ref={chatsPanelRef}>
        <div className="agent-chat-history-list">
          {projectChats.map((chat) => (
            <button
              key={`history-${chat.sessionId}`}
              type="button"
              className={`agent-chat-history-item ${chat.sessionId === activeChatId ? 'active' : ''} ${isSessionSending(chat) ? 'working' : ''}`}
              onClick={() => onActivateChat(chat.sessionId)}
            >
              <span className="agent-chat-history-item-title">{sessionTitle(chat)}</span>
              <span className="agent-chat-history-item-preview">{summarizeChatPreview(chat.messages)}</span>
              <span className="agent-chat-history-item-time">{formatChatTimestamp(chat.updatedAt)}</span>
            </button>
          ))}
        </div>
      </aside>
      <button
        type="button"
        className={`agent-chat-history-scrim ${isChatsPanelOpen ? 'open' : ''}`}
        onClick={onClose}
        aria-label="Close chat history panel"
        tabIndex={isChatsPanelOpen ? 0 : -1}
      />
    </>
  );
}
