import React, { useMemo, useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import ChatInput, { type ChatInputHandle } from '../../../components/ui/chat-input';
import { useChatInput } from '../hooks/useChatInput';
import { useIsMobile } from '@/hooks/useIsMobile';
import { MobileFabChat } from '@/components/ui/mobile-fab-chat';

// FORK: 추천 prompt 를 i18n 키로 derive — locale 별 종목/시장 컨텍스트 분기
// id 는 React key 충돌 방지용 안정 식별자 (label 이 locale 따라 바뀌고 중복될 수 있음).
const SUGGESTION_KEYS = [
  { id: 'earnings', key: 'dashboard.suggestionEarnings' },
  { id: 'compare', key: 'dashboard.suggestionCompare' },
  { id: 'volatility', key: 'dashboard.suggestionVolatility' },
  { id: 'portfolio', key: 'dashboard.suggestionPortfolio' },
] as const;

/**
 * Floating chat input wrapper for dashboard.
 * Renders as a fixed pill at the bottom of the viewport.
 * On mobile: collapses to a floating logo FAB by default.
 */
function ChatInputCard() {
  const { t } = useTranslation();
  const suggestionChips = useMemo(
    () => SUGGESTION_KEYS.map(({ id, key }) => ({ id, label: t(key) })),
    [t],
  );
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
  const chatInputRef = useRef<ChatInputHandle>(null);
  const isMobile = useIsMobile();
  const [chatExpanded, setChatExpanded] = useState(false);

  const handleMobileSend = (...args: Parameters<typeof handleSend>) => {
    handleSend(...args);
    setChatExpanded(false);
  };

  if (isMobile) {
    return (
      <MobileFabChat
        expanded={chatExpanded}
        onExpand={() => setChatExpanded(true)}
        onCollapse={() => setChatExpanded(false)}
        className="fixed left-0 right-0 z-40 px-3"
        style={{ bottom: 'calc(var(--bottom-tab-height, 0px) + 8px)' }}
      >
        <div className="dashboard-floating-chat">
          <ChatInput
            ref={chatInputRef}
            onSend={handleMobileSend}
            disabled={isLoading}
            mode={mode}
            onModeChange={setMode}
            workspaces={workspaces}
            selectedWorkspaceId={selectedWorkspaceId}
            onWorkspaceChange={setSelectedWorkspaceId}
            placeholder={t('dashboard.chatPlaceholder')}
          />
        </div>
      </MobileFabChat>
    );
  }

  return (
    <div className="dashboard-floating-chat-wrapper fixed bottom-8 left-0 right-0 z-40 flex justify-center pointer-events-none">
      <div className="pointer-events-auto w-full max-w-2xl px-4">
        {/* Suggestion bubbles — above the input, outside focus container.
            Only mount when focused so they stay out of the DOM, a11y tree,
            and tab order otherwise (upstream #174). FORK: chips 는 i18n
            suggestionChips (id/label) 사용. */}
        {focused && (
          <div className="dashboard-suggestion-bubbles visible">
            {suggestionChips.map(({ id, label }, i) => (
              <button
                key={id}
                type="button"
                data-testid="dashboard-suggestion-bubble"
                className="dashboard-suggestion-bubble"
                style={{ animationDelay: `${i * 60}ms` }}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => chatInputRef.current?.setValue(label)}
              >
                {label}
              </button>
            ))}
          </div>
        )}

        <div
          data-testid="dashboard-chat-input"
          className="dashboard-floating-chat"
          onFocus={() => setFocused(true)}
          onBlur={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget)) setFocused(false);
          }}
        >
          <ChatInput
            ref={chatInputRef}
            onSend={handleSend}
            disabled={isLoading}
            mode={mode}
            onModeChange={setMode}
            workspaces={workspaces}
            selectedWorkspaceId={selectedWorkspaceId}
            onWorkspaceChange={setSelectedWorkspaceId}
            placeholder={t('dashboard.chatPlaceholderFull')}
          />
        </div>
      </div>
    </div>
  );
}

export default ChatInputCard;
