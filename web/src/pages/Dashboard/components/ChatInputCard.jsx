import React, { useState } from 'react';
import ChatInput from '../../../components/ui/chat-input';
import { useChatInput } from '../hooks/useChatInput';

const SUGGESTION_CHIPS = [
  "Summarize Apple's earnings",
  'Compare TSLA vs BYD',
  'Predict market volatility',
  'Analyze my portfolio risk',
];

/**
 * Floating chat input wrapper for dashboard.
 * Renders as a fixed pill at the bottom of the viewport.
 */
function ChatInputCard() {
  const {
    mode,
    setMode,
    isLoading,
    handleSend,
    workspaces,
    selectedWorkspaceId,
    setSelectedWorkspaceId,
  } = useChatInput();

  const [focused, setFocused] = useState(false);

  return (
    <div className="fixed bottom-8 left-0 right-0 z-40 flex justify-center pointer-events-none">
      <div className="pointer-events-auto w-full max-w-2xl px-4">
        <div
          className="dashboard-floating-chat"
          onFocus={() => setFocused(true)}
          onBlur={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget)) setFocused(false);
          }}
        >
          <ChatInput
            onSend={handleSend}
            disabled={isLoading}
            mode={mode}
            onModeChange={setMode}
            workspaces={workspaces}
            selectedWorkspaceId={selectedWorkspaceId}
            onWorkspaceChange={setSelectedWorkspaceId}
            placeholder="Ask AI about market trends, specific stocks, or portfolio analysis..."
          />
          {/* Suggestion chips */}
          {focused && (
            <div
              className="px-4 pb-3 pt-1 flex gap-2 overflow-x-auto border-t"
              style={{ borderColor: 'var(--color-border-muted)' }}
            >
              {SUGGESTION_CHIPS.map((label) => (
                <button
                  key={label}
                  type="button"
                  className="whitespace-nowrap px-3 py-1.5 rounded-full border text-xs transition-all flex-shrink-0"
                  style={{
                    backgroundColor: 'var(--color-bg-surface, var(--color-bg-card))',
                    borderColor: 'var(--color-border-muted)',
                    color: 'var(--color-text-secondary)',
                  }}
                  onClick={() => handleSend(label)}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.backgroundColor = 'var(--color-bg-hover)';
                    e.currentTarget.style.borderColor = 'var(--color-border-default)';
                    e.currentTarget.style.color = 'var(--color-text-primary)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.backgroundColor = 'var(--color-bg-surface, var(--color-bg-card))';
                    e.currentTarget.style.borderColor = 'var(--color-border-muted)';
                    e.currentTarget.style.color = 'var(--color-text-secondary)';
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default ChatInputCard;
