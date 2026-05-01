import { useEffect, useRef, useState } from "react";
import { AlertCircle, Loader2, Plus, MessageSquareText } from "lucide-react";
import { render } from "../common/render";
import { WebviewRpcClient } from "../common/webviewRpcClient";
import { AgentComposer, formatElapsed } from "../agent/components/AgentComposer";
import { AgentHistoryDrawer } from "../agent/components/AgentHistoryDrawer";
import { AgentMessagesView } from "../agent/components/AgentMessagesView";
import { AgentChecklistBar, AgentMetadataStrip } from "../agent/components/AgentMetadataStrip";
import { useAgentChatRuntime } from "../agent/useAgentChatRuntime";
import { Alert, AlertTitle, AlertDescription } from "../common/components/Alert";
import type { FileNode, ModuleDefinition } from "../../protocol/generated-types";
import "../sidebar-panels/AgentPanel.css";
import "./main.css";

function AgentPanelApp() {
  const projectState = WebviewRpcClient.useSubscribe("projectState");
  const authState = WebviewRpcClient.useSubscribe("authState");
  const structureData = WebviewRpcClient.useSubscribe("structureData");
  const projectFiles = WebviewRpcClient.useSubscribe("projectFiles");

  const projectRoot = projectState.selectedProjectRoot;
  const selectedTargets = projectState.selectedTarget ? [projectState.selectedTarget.name] : [];
  const projectModules: ModuleDefinition[] = structureData.modules;
  const projectFileNodes: FileNode[] = projectFiles.files;
  const isAuthenticated = authState.isAuthenticated;

  const runtime = useAgentChatRuntime(
    projectRoot,
    selectedTargets,
    projectModules,
    projectFileNodes,
  );

  const [isChatsPanelOpen, setIsChatsPanelOpen] = useState(false);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const chatsPanelRef = useRef<HTMLDivElement | null>(null);
  const chatsPanelToggleRef = useRef<HTMLButtonElement | null>(null);
  const lastEscapeRef = useRef<number>(0);

  useEffect(() => {
    const element = messagesRef.current;
    if (!element) return;
    element.scrollTop = element.scrollHeight;
  }, [runtime.messages]);

  useEffect(() => {
    if (!isChatsPanelOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (chatsPanelRef.current?.contains(target)) return;
      if (chatsPanelToggleRef.current?.contains(target)) return;
      setIsChatsPanelOpen(false);
    };
    window.addEventListener("mousedown", onPointerDown);
    return () => window.removeEventListener("mousedown", onPointerDown);
  }, [isChatsPanelOpen]);

  // errorContext is now attached to messages by the backend (via agent.createSession)
  const messagesWithErrorContext = runtime.messages;

  const latestChecklist = runtime.checklist;

  return (
    <div className="agent-panel-standalone agent-chat-dock">
      <div className="agent-panel-header">
        <div className="agent-panel-header-left">
          <span className="agent-panel-title">{runtime.headerTitle}</span>
          <span className="agent-chat-thread-row">
            <span className="agent-chat-thread-title" title={runtime.activeChatTitle}>
              {runtime.activeChatTitle}
            </span>
          </span>
        </div>
        <div className="agent-panel-header-actions">
          <span
            className={`agent-chat-thread-status ${runtime.statusClass}`}
            aria-label={`Status: ${runtime.statusText}`}
            title={`Status: ${runtime.statusText}`}
          >
            <span className="agent-chat-thread-dot" />
          </span>
          <button
            ref={chatsPanelToggleRef}
            type="button"
            className={`agent-chat-action ${isChatsPanelOpen ? "active" : ""}`}
            onClick={() => setIsChatsPanelOpen((current) => !current)}
            disabled={!projectRoot}
            title="Show chat history for this project"
            aria-label="Toggle chat history panel"
          >
            <MessageSquareText size={13} />
          </button>
          <button
            type="button"
            className="agent-chat-action"
            onClick={() => {
              runtime.startNewChat();
              setIsChatsPanelOpen(false);
            }}
            disabled={!projectRoot}
            title="Start a new chat session"
            aria-label="Start a new chat session"
          >
            <Plus size={12} />
          </button>
        </div>
      </div>

      {latestChecklist && (
        <AgentChecklistBar checklist={latestChecklist} />
      )}

      <div className="agent-panel-body">
        <AgentHistoryDrawer
          projectRoot={projectRoot}
          projectChats={runtime.projectChats}
          activeChatId={runtime.activeChatId}
          isChatsPanelOpen={isChatsPanelOpen}
          chatsPanelRef={chatsPanelRef}
          onClose={() => setIsChatsPanelOpen(false)}
          onActivateChat={(chatId) => {
            runtime.activateChat(chatId);
            setIsChatsPanelOpen(false);
          }}
        />
        <div className="agent-chat-main">
          {!isAuthenticated && (
            <Alert variant="warning">
              <AlertTitle>Sign in required</AlertTitle>
              <AlertDescription>
                Sign in from the sidebar to use the AI agent.
              </AlertDescription>
            </Alert>
          )}
          {runtime.error?.kind === "out_of_credit" && (
            <Alert variant="warning">
              <AlertTitle>Out of credit</AlertTitle>
              <AlertDescription>{runtime.error.message}</AlertDescription>
            </Alert>
          )}
          {runtime.error?.kind === "sign_in" && (
            <Alert variant="warning">
              <AlertTitle>Sign in again</AlertTitle>
              <AlertDescription>{runtime.error.message}</AlertDescription>
            </Alert>
          )}
          <AgentMetadataStrip
            messages={messagesWithErrorContext}
            changedFilesSummary={runtime.changedFilesSummary}
            onOpenFileDiff={runtime.openFileDiff}
          />
          <AgentMessagesView
            messagesRef={messagesRef}
            messages={messagesWithErrorContext}
            projectRoot={projectRoot}
            onSubmitDesignQuestions={(answers) => void runtime.sendMessage({ directMessage: answers, hideUserMessage: true })}
          />

          {(runtime.isSending || runtime.compactionNotice) && (
            <div className="agent-status-line" role="status" aria-live="polite">
              <div className="agent-status-left">
                {runtime.isSending && (
                  <>
                    <Loader2 size={10} className="agent-tool-spin agent-status-spinner" />
                    <span className="agent-status-activity">Working</span>
                  </>
                )}
                {runtime.compactionNotice && !runtime.isSending && (
                  <>
                    <AlertCircle size={10} />
                    <span className="agent-status-activity">{runtime.compactionNotice.status}</span>
                  </>
                )}
              </div>
              {runtime.isSending && (
                <span className="agent-status-elapsed">{formatElapsed(runtime.turnElapsed)}</span>
              )}
            </div>
          )}

          <AgentComposer
            composerInputRef={runtime.composerInputRef}
            input={runtime.input}
            mentionToken={runtime.mentionToken}
            mentionItems={runtime.mentionItems}
            mentionIndex={runtime.mentionIndex}
            isAuthenticated={isAuthenticated}
            projectRoot={projectRoot}
            isReady={runtime.isReady}
            isSending={runtime.isSending}
            isStopping={runtime.isStopping}
            contextUsage={runtime.contextUsage}
            modelName={runtime.modelName}
            modelOptions={runtime.modelOptions}
            canChangeModel={runtime.canChangeModel}
            onModelChange={runtime.setModel}
            onInputChange={(nextValue, textarea) => {
              runtime.setInput(nextValue);
              runtime.refreshMentionFromInput(
                nextValue,
                textarea.selectionStart ?? nextValue.length,
              );
              textarea.style.height = "auto";
              textarea.style.height = `${textarea.scrollHeight}px`;
            }}
            onInputClick={runtime.refreshMentionFromInput}
            onInputKeyUp={(key, value, caret) => {
              if (
                runtime.mentionToken
                && runtime.mentionItems.length > 0
                && (
                  key === "ArrowDown"
                  || key === "ArrowUp"
                  || key === "Enter"
                  || key === "Tab"
                  || key === "Escape"
                )
              ) {
                return;
              }
              runtime.refreshMentionFromInput(value, caret);
            }}
            onKeyDown={(event) => {
              if (runtime.mentionToken && runtime.mentionItems.length > 0) {
                if (event.key === "ArrowDown") {
                  event.preventDefault();
                  runtime.setMentionIndex((current) => (current + 1) % runtime.mentionItems.length);
                  return;
                }
                if (event.key === "ArrowUp") {
                  event.preventDefault();
                  runtime.setMentionIndex((current) => (
                    (current - 1 + runtime.mentionItems.length) % runtime.mentionItems.length
                  ));
                  return;
                }
                if (event.key === "Enter" || event.key === "Tab") {
                  event.preventDefault();
                  runtime.insertMention(runtime.mentionItems[runtime.mentionIndex]);
                  return;
                }
                if (event.key === "Escape") {
                  event.preventDefault();
                  runtime.setMentionToken(null);
                  runtime.setMentionIndex(0);
                  return;
                }
              }
              if (event.key === "Escape" && runtime.isSending && !runtime.isStopping) {
                const now = Date.now();
                if (now - lastEscapeRef.current < 500) {
                  event.preventDefault();
                  void runtime.stopRun();
                  lastEscapeRef.current = 0;
                } else {
                  lastEscapeRef.current = now;
                }
                return;
              }
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                if (runtime.isSending) {
                  void runtime.sendSteeringMessage();
                } else {
                  void runtime.sendMessage();
                }
              }
            }}
            onInsertMention={runtime.insertMention}
            onStop={() => void runtime.stopRun()}
            onSend={() => {
              if (runtime.isSending) {
                void runtime.sendSteeringMessage();
              } else {
                void runtime.sendMessage();
              }
            }}
          />

          {runtime.error && !runtime.error.persistent && (
            <div
              className="agent-chat-error"
              role="status"
              onClick={runtime.dismissError}
              title="Dismiss"
            >
              {runtime.error.message}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

render(AgentPanelApp);
