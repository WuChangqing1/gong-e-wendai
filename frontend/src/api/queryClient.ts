/** TanStack Query 客户端与查询键。 */

import { QueryClient } from '@tanstack/react-query';
import { ApiError } from '@/api/client';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        if (error instanceof ApiError) {
          if ([400, 401, 403, 404, 409, 422].includes(error.status)) return false;
        }
        return failureCount < 2;
      },
    },
    mutations: {
      retry: false,
    },
  },
});

export const queryKeys = {
  health: ['health'] as const,
  me: ['me'] as const,
  merchantProfile: ['merchant', 'profile'] as const,
  accountOverview: ['account', 'overview'] as const,
  accountSnapshots: ['account', 'snapshots'] as const,
  todayAnalysis: ['analysis', 'today'] as const,
  analysisStale: ['analysis', 'stale'] as const,
  analysisHistory: ['analysis', 'history'] as const,
  windowSummary: (params: Record<string, unknown> = {}) =>
    ['analysis', 'window-summary', params] as const,
  cashEvents: (params: Record<string, unknown>) => ['cash-events', params] as const,
  cashEventStats: ['cash-events', 'stats'] as const,
  cashEventDetail: (id: string) => ['cash-events', 'detail', id] as const,
  cashEventRevisions: (id: string) => ['cash-events', 'revisions', id] as const,
  allRevisions: ['cash-events', 'revisions'] as const,
  scenarios: ['scenarios'] as const,
  household: ['household'] as const,
  householdCards: (params: Record<string, unknown>) => ['household', 'cards', params] as const,
  householdCard: (id: string) => ['household', 'card', id] as const,
  consultations: (params: Record<string, unknown>) => ['consultations', params] as const,
  consultation: (id: string) => ['consultations', 'detail', id] as const,
  consultantQueue: (params: Record<string, unknown>) =>
    ['consultations', 'queue', params] as const,
  aiStatus: ['ai', 'status'] as const,
  enhancementOverview: (params: Record<string, unknown> = {}) =>
    ['enhancements', 'overview', params] as const,
  dailyHistory: ['history', 'daily'] as const,
  settlementRecords: ['settlement-records'] as const,
};
