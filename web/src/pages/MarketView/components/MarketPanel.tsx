import React, { useRef, useEffect } from 'react';
// eslint-disable-next-line @typescript-eslint/no-explicit-any
import MessageList from '../../ChatAgent/components/MessageList';
import LogoLoading from '../../../components/ui/logo-loading';
import './MarketPanel.css';

// TODO: type properly once ChatAgent message types are exported
interface ChatMessage {
  id?: string;
  role?: string;
  content?: string;
  isStreaming?: boolean;
  [key: string]: unknown;
}

interface MarketPanelProps {
  messages?: ChatMessage[];
  isLoading?: boolean;
  error?: string | null;
}

const MarketPanel = ({ messages = [], isLoading = false, error = null }: MarketPanelProps) => {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when messages change or when streaming
  useEffect(() => {
    const scrollToBottom = () => {
      if (messagesContainerRef.current) {
        messagesContainerRef.current.scrollTo({
          top: messagesContainerRef.current.scrollHeight,
          behavior: 'smooth',
        });
      }
    };

    // Scroll when messages change
    if (messages.length > 0) {
      // Use setTimeout to ensure DOM has updated
      const timeoutId = setTimeout(scrollToBottom, 100);
      return () => clearTimeout(timeoutId);
    }
  }, [messages]);

  // Also scroll when a message is streaming (content updates)
  useEffect(() => {
    const hasStreamingMessage = messages.some((msg) => msg.isStreaming);
    if (hasStreamingMessage && messagesContainerRef.current) {
      const timeoutId = setTimeout(() => {
        if (messagesContainerRef.current) {
          messagesContainerRef.current.scrollTo({
            top: messagesContainerRef.current.scrollHeight,
            behavior: 'smooth',
          });
        }
      }, 50);
      return () => clearTimeout(timeoutId);
    }
  }, [messages]);

  return (
    <div className="market-panel">
      <div 
        ref={messagesContainerRef}
        style={{ 
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          overflowX: 'hidden',
        }}
      >
        {messages.length === 0 ? (
          <div className="market-chat-empty-state" style={{ height: '100%' }}>
            <LogoLoading size={60} color="var(--color-accent-overlay)" />
            <p className="market-chat-empty-text" style={{ marginTop: 16 }}>
              Start a conversation by typing a message below
            </p>
            {error && (
              <div style={{ color: 'var(--color-loss)', padding: '12px', fontSize: '14px' }}>
                Error: {error}
              </div>
            )}
          </div>
        ) : (
          <div style={{ padding: '16px 24px', maxWidth: '100%' }}>
            {/* @ts-expect-error MessageList is still JSX — will be typed after ChatAgent migration */}
            <MessageList
              messages={messages}
              hideAvatar
              compactToolCalls
              onOpenSubagentTask={() => {}}
              onOpenFile={() => {}}
            />
            {error && (
              <div style={{
                margin: '8px 0',
                padding: '10px 14px',
                borderRadius: '8px',
                background: 'var(--color-loss-soft)',
                border: '1px solid var(--color-border-loss)',
                color: 'var(--color-loss)',
                fontSize: '13px',
                lineHeight: '1.5',
              }}>
                {error}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default React.memo(MarketPanel);
