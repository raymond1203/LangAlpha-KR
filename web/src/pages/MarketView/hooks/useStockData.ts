import { useState, useEffect, useRef, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchStockQuote, fetchCompanyOverview, fetchAnalystData } from '../utils/api';
import { fetchMarketStatus } from '@/lib/marketUtils';
import type { StockInfo, RealTimePrice, SnapshotData } from '@/types/market';
import type { ConnectionStatus, BarData } from './useMarketDataWS';

/** Market status shape returned by fetchMarketStatus */
interface MarketStatusData {
    market?: string;
    afterHours?: boolean;
    earlyHours?: boolean;
    [key: string]: unknown;
}

interface UseStockDataOptions {
    selectedStock: string | null;
    wsStatus: ConnectionStatus;
    setPreviousClose?: (symbol: string, price: number) => void;
    setDayOpen?: (symbol: string, price: number) => void;
}

interface AnalystOverlayData {
    priceTargets: {
        targetHigh?: number;
        targetLow?: number;
        targetConsensus?: number;
        [key: string]: unknown;
    } | null;
    grades: Array<{
        date?: string;
        action?: string;
        [key: string]: unknown;
    }>;
}

export interface UseStockDataReturn {
    stockInfo: StockInfo | null;
    realTimePrice: RealTimePrice | null;
    snapshotData: SnapshotData | null;
    overviewData: unknown;
    overviewLoading: boolean;
    overlayData: AnalystOverlayData | null;
    marketStatus: MarketStatusData | null;
    handleLatestBar: (bar: BarData | null) => void;
}

/**
 * useStockData Hook
 *
 * Extracts data fetching logic out of MarketView to improve modularity.
 * Uses TanStack Query to automatically handle AbortControllers, background refetching,
 * polling intervals, and aggressive caching out-of-the-box.
 */
export function useStockData({
    selectedStock,
    wsStatus,
    setPreviousClose,
    setDayOpen
}: UseStockDataOptions): UseStockDataReturn {
    const [stockInfo, setStockInfo] = useState<StockInfo | null>(null);
    const [realTimePrice, setRealTimePrice] = useState<RealTimePrice | null>(null);
    const [snapshotData, setSnapshotData] = useState<SnapshotData | null>(null);

    // 1. Stock Quote & Snapshot
    const { data: quoteResponse } = useQuery({
        queryKey: ['stockQuote', selectedStock],
        queryFn: async ({ signal }) => {
            if (!selectedStock) return null;
            const data = await fetchStockQuote(selectedStock, { signal });

            // Side effects mapping for WS refs
            if (data.snapshot) {
                if (data.snapshot.previous_close != null && setPreviousClose) {
                    setPreviousClose(selectedStock, data.snapshot.previous_close);
                }
                if (data.snapshot.open != null && setDayOpen) {
                    setDayOpen(selectedStock, data.snapshot.open);
                }
            }
            return data;
        },
        // Polling: disabled if WS is streaming real-time, otherwise poll every 60s
        refetchInterval: wsStatus === 'connected' ? false : 60000,
        refetchIntervalInBackground: false,
        enabled: !!selectedStock,
        staleTime: 1000 * 10, // 10s fresh cache 
    });

    // Isolate pure UI state for the realtime bar updates 
    // This allows WebSocket to update local state extremely fast 
    // without triggering React Query cache updates on every tick.
    useEffect(() => {
        if (!selectedStock) {
            setStockInfo(null);
            setRealTimePrice(null);
            setSnapshotData(null);
        } else if (quoteResponse) {
            setStockInfo(quoteResponse.stockInfo);
            setRealTimePrice(quoteResponse.realTimePrice);
            setSnapshotData(quoteResponse.snapshot);
        }
    }, [quoteResponse, selectedStock]);

    // 2. Company Overview
    const { data: overviewData = null, isLoading: overviewLoading } = useQuery({
        queryKey: ['companyOverview', selectedStock],
        queryFn: ({ signal }) => fetchCompanyOverview(selectedStock!, { signal }),
        enabled: !!selectedStock,
        staleTime: 5 * 60 * 1000, // 5 minutes fresh
    });

    // 3. Analyst Data
    const { data: overlayData = null } = useQuery<AnalystOverlayData | null>({
        queryKey: ['analystData', selectedStock],
        queryFn: async ({ signal }) => {
            const analyst = await fetchAnalystData(selectedStock!, { signal }) as Record<string, unknown> | null;
            return analyst ? {
                priceTargets: (analyst.priceTargets as AnalystOverlayData['priceTargets']) || null,
                grades: (analyst.grades as AnalystOverlayData['grades']) || [],
            } : null;
        },
        enabled: !!selectedStock,
        staleTime: 5 * 60 * 1000, // 5 minutes fresh
    });

    // 4. Market Status
    const { data: marketStatus = null } = useQuery<MarketStatusData | null>({
        queryKey: ['dashboard', 'marketStatus'], // Matches cached value from useDashboardData
        queryFn: fetchMarketStatus,
        refetchInterval: 60000,
        refetchIntervalInBackground: false,
        staleTime: 30000,
    });

    // WebSocket Update Handler (mutates local realTimePrice state)
    const stockInfoRef = useRef(stockInfo);
    useEffect(() => { stockInfoRef.current = stockInfo; }, [stockInfo]);

    const handleLatestBar = useCallback((bar: BarData | null): void => {
        if (!bar?.close) return;
        setRealTimePrice((prev) => {
            if (!prev || !prev.price) return prev;
            const updatedPrice = Math.round(bar.close * 100) / 100;
            // Use previousClose from snapshot if available, else derive from initial quote
            const previousClose = prev.previousClose ?? ((prev.price ?? 0) - (prev.change ?? 0));
            if (!previousClose) {
                // Still update price even without previousClose — just skip change% recalculation
                return { ...prev, price: updatedPrice, close: bar.close, timestamp: bar.time * 1000 };
            }
            const change = bar.close - previousClose;
            const changePct = parseFloat(((change / previousClose) * 100).toFixed(2));
            return {
                ...prev,
                price: updatedPrice,
                close: bar.close,
                change: Math.round(change * 100) / 100,
                changePercent: changePct,
                timestamp: bar.time * 1000,
            };
        });
    }, []);

    return {
        stockInfo,
        realTimePrice,
        snapshotData,
        overviewData,
        overviewLoading,
        overlayData,
        marketStatus,
        handleLatestBar
    };
}
