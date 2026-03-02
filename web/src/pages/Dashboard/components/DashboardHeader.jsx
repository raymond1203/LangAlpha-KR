import { Search, HelpCircle, User, Mail, ChevronDown } from 'lucide-react';
import UserConfigPanel from './UserConfigPanel';
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { getCurrentUser, searchStocks } from '../utils/api';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../../contexts/AuthContext';
import './DashboardHeader.css';

const DashboardHeader = ({ onStockSearch, onModifyPreferences, onStartOnboarding }) => {
  const navigate = useNavigate();
  const { isLoggedIn } = useAuth();
  const { t } = useTranslation();
  const [isUserPanelOpen, setIsUserPanelOpen] = useState(false);
  const [avatarUrl, setAvatarUrl] = useState(null);
  const [displayName, setDisplayName] = useState('');
  const [showHelpPopover, setShowHelpPopover] = useState(false);
  const helpRef = useRef(null);
  const searchInputRef = useRef(null);

  // Search state
  const [searchValue, setSearchValue] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const [searchFocused, setSearchFocused] = useState(false);
  const dropdownRef = useRef(null);

  // Global "/" shortcut to focus search
  useEffect(() => {
    const handleKey = (e) => {
      if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        const tag = document.activeElement?.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || document.activeElement?.isContentEditable) return;
        e.preventDefault();
        searchInputRef.current?.focus();
      }
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, []);

  // Close help popover on outside click
  useEffect(() => {
    if (!showHelpPopover) return;
    const handleClickOutside = (e) => {
      if (helpRef.current && !helpRef.current.contains(e.target)) {
        setShowHelpPopover(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showHelpPopover]);

  // Stock search with debounce (300ms)
  useEffect(() => {
    const query = searchValue.trim();
    if (!query || query.length < 1) {
      setSearchResults([]);
      setSearchLoading(false);
      setShowDropdown(false);
      return;
    }

    const timeoutId = setTimeout(async () => {
      setSearchLoading(true);
      setShowDropdown(true);
      try {
        const result = await searchStocks(query, 12);
        setSearchResults(result.results || []);
      } catch (error) {
        console.error('Stock search failed:', error);
        setSearchResults([]);
      } finally {
        setSearchLoading(false);
      }
    }, 300);

    return () => clearTimeout(timeoutId);
  }, [searchValue]);

  // Close search dropdown on outside click
  useEffect(() => {
    function handleClickOutside(event) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setShowDropdown(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const refreshUser = useCallback(async () => {
    try {
      const data = await getCurrentUser();
      const url = data?.user?.avatar_url;
      const version = data?.user?.updated_at;
      setAvatarUrl(url ? `${url}?v=${version}` : null);
      setDisplayName(data?.user?.display_name || data?.user?.name || '');
    } catch (err) {
      console.error('Failed to fetch user:', err);
    }
  }, []);

  useEffect(() => {
    if (isLoggedIn) refreshUser();
  }, [isLoggedIn, refreshUser]);

  const handlePanelClose = () => {
    setIsUserPanelOpen(false);
    if (isLoggedIn) refreshUser();
  };

  const handleSelectStock = (stock) => {
    if (stock?.symbol) {
      const symbol = stock.symbol.trim().toUpperCase();
      setSearchValue(symbol);
      setShowDropdown(false);
      if (onStockSearch) {
        onStockSearch(symbol, stock);
      } else {
        navigate(`/market?symbol=${encodeURIComponent(symbol)}`);
      }
    }
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    const q = searchValue.trim();
    if (q) {
      const symbol = q.toUpperCase();
      setSearchValue(symbol);
      setShowDropdown(false);
      if (onStockSearch) {
        onStockSearch(symbol, null);
      } else {
        navigate(`/market?symbol=${encodeURIComponent(symbol)}`);
      }
    }
  };

  return (
    <>
      <div
        className="sticky top-0 z-30 flex items-center justify-between px-4 sm:px-6 py-3"
        style={{
          backgroundColor: 'var(--color-bg-page)',
          borderBottom: '1px solid var(--color-border-muted)',
          backdropFilter: 'blur(12px)',
          WebkitBackdropFilter: 'blur(12px)',
        }}
      >
        {/* Search */}
        <div className="flex-1 max-w-xl">
          <div className="dashboard-search-wrapper" ref={dropdownRef}>
            <form
              onSubmit={handleSubmit}
              className="dashboard-search-form relative group flex items-center gap-2 h-10 px-3 rounded-xl border transition-all"
              style={{
                backgroundColor: 'var(--color-bg-input)',
                borderColor: searchFocused ? 'var(--color-accent-primary)' : 'var(--color-border-muted)',
                boxShadow: searchFocused ? '0 0 0 1px var(--color-accent-soft)' : 'none',
              }}
            >
              <Search
                className="dashboard-search-icon transition-colors"
                style={{ color: searchFocused ? 'var(--color-accent-primary)' : 'var(--color-icon-muted)' }}
              />
              <input
                ref={searchInputRef}
                type="text"
                placeholder={t('dashboard.searchPlaceholder')}
                value={searchValue}
                onChange={(e) => setSearchValue(e.target.value)}
                onFocus={() => {
                  setSearchFocused(true);
                  if (searchValue.trim()) setShowDropdown(true);
                }}
                onBlur={() => setSearchFocused(false)}
                className="dashboard-search-input"
                autoComplete="off"
                style={{
                  backgroundColor: 'transparent',
                  border: 'none',
                  color: 'var(--color-text-primary)',
                }}
              />
              {/* "/" shortcut badge */}
              {!searchFocused && !searchValue && (
                <span
                  className="text-xs border rounded px-1.5 py-0.5 flex-shrink-0"
                  style={{
                    color: 'var(--color-text-quaternary, var(--color-text-secondary))',
                    borderColor: 'var(--color-border-default)',
                  }}
                >
                  /
                </span>
              )}
            </form>
            {showDropdown && searchValue.trim() && (
              <div className="dashboard-search-dropdown">
                {searchLoading ? (
                  <div className="dashboard-search-dropdown-item dashboard-search-dropdown-loading">
                    {t('dashboard.searching')}
                  </div>
                ) : searchResults.length === 0 ? (
                  <div className="dashboard-search-dropdown-item dashboard-search-dropdown-empty">
                    {t('dashboard.noResults')}
                  </div>
                ) : (
                  searchResults.slice(0, 12).map((stock, index) => (
                    <button
                      key={`${stock.symbol}-${index}`}
                      type="button"
                      className="dashboard-search-dropdown-item"
                      onClick={() => handleSelectStock(stock)}
                    >
                      <span className="dashboard-search-dropdown-symbol">{stock.symbol}</span>
                      <span className="dashboard-search-dropdown-name">{stock.name || stock.symbol}</span>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
        </div>

        {/* Right actions */}
        <div className="flex items-center gap-3 ml-4">
          {/* Help */}
          <div className="relative" ref={helpRef}>
            <button
              className="p-2 transition-colors"
              style={{ color: showHelpPopover ? 'var(--color-text-primary)' : 'var(--color-text-secondary)' }}
              onClick={() => setShowHelpPopover((prev) => !prev)}
              onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--color-text-primary)')}
              onMouseLeave={(e) => {
                if (!showHelpPopover) e.currentTarget.style.color = 'var(--color-text-secondary)';
              }}
            >
              <HelpCircle size={20} />
            </button>
            {showHelpPopover && (
              <div
                className="absolute right-0 top-full mt-2 z-50 rounded-lg shadow-lg"
                style={{
                  backgroundColor: 'var(--color-bg-elevated)',
                  border: '1px solid var(--color-border-elevated)',
                  width: '280px',
                  padding: '16px',
                }}
              >
                <p
                  className="text-sm font-medium mb-3"
                  style={{ color: 'var(--color-text-primary)' }}
                >
                  {t('dashboard.contactMessage')}
                </p>
                {(import.meta.env.VITE_CONTACT_EMAILS || '').split(',').filter(Boolean).map((email, idx, arr) => (
                  <div
                    key={email}
                    className="flex items-center gap-2 px-3 py-2 rounded-md cursor-pointer transition-colors hover:opacity-80"
                    style={{ backgroundColor: 'var(--color-bg-input)', marginBottom: idx < arr.length - 1 ? '8px' : undefined }}
                    onClick={() => {
                      window.location.href = `mailto:${email.trim()}`;
                      setShowHelpPopover(false);
                    }}
                  >
                    <Mail className="h-4 w-4 flex-shrink-0" style={{ color: 'var(--color-accent-primary)' }} />
                    <div className="min-w-0">
                      <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>Email</p>
                      <p className="text-sm font-medium" style={{ color: 'var(--color-text-primary)' }}>{email.trim()}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Divider */}
          <div className="h-8 w-[1px] mx-2" style={{ backgroundColor: 'var(--color-border-muted)' }} />

          {/* User avatar + name */}
          <button
            className="flex items-center gap-2 text-sm font-medium transition-colors"
            style={{ color: 'var(--color-text-secondary)' }}
            onClick={() => setIsUserPanelOpen(true)}
            onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--color-text-primary)')}
            onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--color-text-secondary)')}
          >
            <div
              className="h-8 w-8 rounded-full flex items-center justify-center overflow-hidden"
              style={{ backgroundColor: 'var(--color-accent-soft)' }}
            >
              {avatarUrl ? (
                <img src={avatarUrl} alt="avatar" className="h-full w-full object-cover" onError={() => setAvatarUrl(null)} />
              ) : (
                <User className="h-4 w-4" style={{ color: 'var(--color-accent-primary)' }} />
              )}
            </div>
            {displayName && <span className="hidden sm:inline">{displayName}</span>}
            <ChevronDown size={14} style={{ color: 'var(--color-text-secondary)' }} />
          </button>
        </div>
      </div>

      <UserConfigPanel
        isOpen={isUserPanelOpen}
        onClose={handlePanelClose}
        onModifyPreferences={onModifyPreferences}
        onStartOnboarding={onStartOnboarding}
        initialTab={null}
      />
    </>
  );
};

export default DashboardHeader;
