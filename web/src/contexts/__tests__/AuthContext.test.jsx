import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Enable Supabase auth code path (AuthProvider checks VITE_SUPABASE_URL)
// Must be set before the dynamic import below.
vi.stubEnv('VITE_SUPABASE_URL', 'https://test.supabase.co');

// Mock supabase with a functional mock auth object
const mockGetSession = vi.fn().mockResolvedValue({ data: { session: null } });
const mockOnAuthStateChange = vi.fn().mockReturnValue({
  data: { subscription: { unsubscribe: vi.fn() } },
});

vi.mock('../../lib/supabase', () => ({
  supabase: {
    auth: {
      getSession: (...args) => mockGetSession(...args),
      onAuthStateChange: (...args) => mockOnAuthStateChange(...args),
      signInWithPassword: vi.fn(),
      signUp: vi.fn(),
      signInWithOAuth: vi.fn(),
      signOut: vi.fn(),
    },
  },
}));

vi.mock('../../api/client', () => ({
  setTokenGetter: vi.fn(),
}));

// Dynamic import so mocks and env stubs are applied first
const { AuthProvider, useAuth } = await import('../AuthContext');

function TestConsumer() {
  const auth = useAuth();
  return (
    <div>
      <span data-testid="userId">{auth.userId ?? 'none'}</span>
      <span data-testid="isLoggedIn">{String(auth.isLoggedIn)}</span>
      <span data-testid="isInitialized">{String(auth.isInitialized)}</span>
    </div>
  );
}

function renderWithQueryClient(ui) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>
  );
}

describe('AuthContext', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSession.mockResolvedValue({ data: { session: null } });
    mockOnAuthStateChange.mockReturnValue({
      data: { subscription: { unsubscribe: vi.fn() } },
    });
  });

  describe('when no session exists', () => {
    it('shows isInitialized true and isLoggedIn false after bootstrap', async () => {
      renderWithQueryClient(
        <AuthProvider>
          <TestConsumer />
        </AuthProvider>
      );

      await waitFor(() =>
        expect(screen.getByTestId('isInitialized').textContent).toBe('true')
      );
      expect(screen.getByTestId('isLoggedIn').textContent).toBe('false');
      expect(screen.getByTestId('userId').textContent).toBe('none');
    });
  });

  describe('when a session exists', () => {
    it('shows isLoggedIn true and exposes userId', async () => {
      mockGetSession.mockResolvedValue({
        data: {
          session: {
            user: { id: 'user-abc' },
            access_token: 'tok-123',
          },
        },
      });

      renderWithQueryClient(
        <AuthProvider>
          <TestConsumer />
        </AuthProvider>
      );

      await waitFor(() =>
        expect(screen.getByTestId('isLoggedIn').textContent).toBe('true')
      );
      expect(screen.getByTestId('userId').textContent).toBe('user-abc');
    });
  });

  describe('useAuth', () => {
    it('throws when used outside AuthProvider', () => {
      function BadConsumer() {
        useAuth();
        return null;
      }

      const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
      expect(() => render(<BadConsumer />)).toThrow(
        'useAuth must be used within AuthProvider'
      );
      spy.mockRestore();
    });
  });

  describe('AuthProvider renders children', () => {
    it('renders child components', async () => {
      renderWithQueryClient(
        <AuthProvider>
          <div data-testid="child">Hello</div>
        </AuthProvider>
      );

      await waitFor(() =>
        expect(screen.getByTestId('child').textContent).toBe('Hello')
      );
    });
  });

  describe('onAuthStateChange subscription', () => {
    it('subscribes to auth state changes on mount', async () => {
      renderWithQueryClient(
        <AuthProvider>
          <TestConsumer />
        </AuthProvider>
      );

      await waitFor(() =>
        expect(screen.getByTestId('isInitialized').textContent).toBe('true')
      );
      expect(mockOnAuthStateChange).toHaveBeenCalled();
    });
  });
});
