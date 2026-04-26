import React, { type ReactElement, type ReactNode } from 'react';
import { render, renderHook, type RenderOptions } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
// FORK (#32): MarketContext 가 도입된 이후 useDashboardData 등이 useMarket() 사용 → 테스트도 같은 provider tree 필요
import { MarketProvider, type MarketSetting } from '@/contexts/MarketContext';

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

function createWrapper(
  queryClient: QueryClient,
  route = '/',
  initialMarket?: MarketSetting,
) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          {/*
            FORK (#32): initialMarket prop 으로 명시하면 MarketProvider 가 localStorage 무시.
            명시하지 않은 테스트는 default 'auto' + locale 으로 도출. 테스트 격리 필요한 경우
            beforeEach 에서 localStorage.removeItem('marketRegion') + i18n.changeLanguage('en-US') 권장.
          */}
          <MarketProvider initialSetting={initialMarket}>{children}</MarketProvider>
        </MemoryRouter>
      </QueryClientProvider>
    );
  };
}

interface RenderWithProvidersOptions extends Omit<RenderOptions, 'wrapper'> {
  route?: string;
  queryClient?: QueryClient;
  /** MarketProvider 초기값 override — 명시 시 localStorage 무시. 테스트 격리용. */
  initialMarket?: MarketSetting;
}

export function renderWithProviders(ui: ReactElement, { route = '/', queryClient, initialMarket, ...options }: RenderWithProvidersOptions = {}) {
  const client = queryClient || createTestQueryClient();
  const Wrapper = createWrapper(client, route, initialMarket);
  return { ...render(ui, { wrapper: Wrapper, ...options }), queryClient: client };
}

export function renderHookWithProviders<TResult>(hook: () => TResult, { route = '/', queryClient, initialMarket, ...options }: RenderWithProvidersOptions = {}) {
  const client = queryClient || createTestQueryClient();
  const wrapper = createWrapper(client, route, initialMarket);
  return { ...renderHook(hook, { wrapper, ...options }), queryClient: client };
}

export { createTestQueryClient };
