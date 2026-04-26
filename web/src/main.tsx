import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from './contexts/ThemeContext'
import { AuthProvider } from './contexts/AuthContext'
import { MarketProvider } from './contexts/MarketContext'
import App from './App'
import './i18n'
import './index.css'
import { Toaster } from './components/ui/toaster'

// Initialize a global QueryClient for data fetching and caching
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: true, // Auto-refetch when user comes back to the tab
      retry: 1,                   // Retry failed requests once before showing error
      staleTime: 1000 * 60 * 2,   // Data is considered fresh for 2 minutes by default
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={queryClient}>
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <ThemeProvider>
        <AuthProvider>
          {/* FORK: MarketProvider 는 AuthProvider 안쪽 — 사용자 컨텍스트가 갖춰진 뒤 시장 설정 적용. i18n 은 ./i18n 의 side-effect import 로 이미 초기화됨. */}
          <MarketProvider>
            <App />
            <Toaster />
          </MarketProvider>
        </AuthProvider>
      </ThemeProvider>
    </BrowserRouter>
  </QueryClientProvider>,
)
