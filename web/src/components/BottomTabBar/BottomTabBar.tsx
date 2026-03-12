import { ChartCandlestick, LayoutDashboard, MessageSquareText, Timer, Settings } from 'lucide-react';
import { useLocation, useNavigate } from 'react-router-dom';
import { getChatSession } from '../../pages/ChatAgent/hooks/utils/chatSessionRestore';
import './BottomTabBar.css';

const menuItems = [
  { key: '/dashboard', icon: LayoutDashboard, labelKey: 'sidebar.dashboard' },
  { key: '/chat', icon: MessageSquareText, labelKey: 'sidebar.chatAgent' },
  { key: '/market', icon: ChartCandlestick, labelKey: 'sidebar.marketView' },
  { key: '/automations', icon: Timer, labelKey: 'sidebar.automations' },
  { key: '/settings', icon: Settings, labelKey: 'sidebar.settings' },
];

export default function BottomTabBar() {
  const navigate = useNavigate();
  const location = useLocation();
  const handleItemClick = (path: string) => {
    if (path === '/chat') {
      const session = getChatSession();
      if (session) {
        if (session.threadId) {
          navigate(`/chat/t/${session.threadId}`, {
            state: { workspaceId: session.workspaceId },
          });
        } else {
          navigate(`/chat/${session.workspaceId}`);
        }
        return;
      }
    }
    navigate(path);
  };

  return (
    <div className="bottom-tab-bar">
      <div className="bottom-tab-bar-pill">
        {menuItems.map((item) => {
          const Icon = item.icon;
          const isActive = item.key === '/chat'
            ? location.pathname.startsWith('/chat')
            : location.pathname === item.key || location.pathname.startsWith(item.key + '/');

          return (
            <button
              key={item.key}
              className={`bottom-tab-item ${isActive ? 'active' : ''}`}
              onClick={() => handleItemClick(item.key)}
              aria-label={item.key.slice(1)}
            >
              <Icon className="bottom-tab-item-icon" />
            </button>
          );
        })}
      </div>
    </div>
  );
}
