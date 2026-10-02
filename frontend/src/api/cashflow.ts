/** 收付款事项、分析、情景接口。 */

import { del, get, patch, post } from '@/api/client';
import type {
  AnalysisMode,
  AnalysisResult,
  CashEvent,
  CashEventDetail,
  CashEventStats,
  Direction,
  Page,
  RevisionDiff,
  RevisionWithEvent,
  Scenario,
  StaleStatus,
  WindowSummary,
} from '@/types';

export interface CashEventPayload {
  cash_key?: string | null;
  title: string;
  direction: Direction;
  amount_cents: number;
  scheduled_at: string;
  event_type?: string;
  state?: string;
  source_label?: string | null;
  note?: string | null;
  sequence_index_optional?: number | null;
}

export interface CashEventQuery {
  page?: number;
  page_size?: number;
  search?: string;
  direction?: Direction;
  state?: string;
  event_type?: string;
  source_type?: string;
  start?: string;
  end?: string;
}

export const cashEventApi = {
  list: (params: CashEventQuery) => get<Page<CashEvent>>('/cash-events', { params }),
  stats: () => get<CashEventStats>('/cash-events/stats'),
  detail: (id: string) => get<CashEventDetail>(`/cash-events/${id}`),
  create: (payload: CashEventPayload) => post<CashEventDetail>('/cash-events', payload),
  update: (
    id: string,
    payload: Partial<CashEventPayload> & { change_reason?: string | null },
  ) => patch<CashEventDetail>(`/cash-events/${id}`, payload),
  cancel: (id: string, reason?: string) =>
    post<CashEventDetail>(`/cash-events/${id}/cancel`, { reason: reason ?? null }),
  revisions: (id: string) => get<RevisionDiff[]>(`/cash-events/${id}/revisions`),
  allRevisions: (limit = 50) =>
    get<RevisionWithEvent[]>('/cash-events/revisions', { params: { limit } }),
  source: (id: string) => get<CashEventDetail>(`/cash-events/${id}/source`),
};

export interface AnalysisRunPayload {
  mode: AnalysisMode;
  snapshot_at?: string | null;
  scenario_ids?: string[] | null;
  delay_days?: number;
  buffer_cents?: number | null;
}

export const analysisApi = {
  today: (persist = false) => get<AnalysisResult>('/analysis/today', { params: { persist } }),
  run: (payload: AnalysisRunPayload) => post<AnalysisResult>('/analysis/run', payload),
  stale: () => get<StaleStatus>('/analysis/stale'),
  history: (limit = 20) => get<Record<string, unknown>[]>('/analysis/history', { params: { limit } }),
  /** 未来 7 天窗口聚合，供各图表使用 */
  windowSummary: (params: { buffer_cents?: number; reference_at?: string } = {}) =>
    get<WindowSummary>('/analysis/window-summary', { params }),
};

export const scenarioApi = {
  list: () => get<Scenario[]>('/scenarios'),
  create: (payload: {
    name: string;
    kind?: string;
    description?: string | null;
    overrides?: {
      cash_event_id: string;
      scheduled_at?: string | null;
      amount_cents?: number | null;
      direction?: Direction | null;
      state?: string | null;
      note?: string | null;
    }[];
  }) => post<Scenario>('/scenarios', payload),
  update: (
    id: string,
    payload: {
      name?: string;
      description?: string | null;
      overrides?: {
        cash_event_id: string;
        scheduled_at?: string | null;
        amount_cents?: number | null;
        note?: string | null;
      }[];
    },
  ) => patch<Scenario>(`/scenarios/${id}`, payload),
  remove: (id: string) => del<{ message: string }>(`/scenarios/${id}`),
  compare: (scenarioIds: string[], delayDays = 3) =>
    post<AnalysisResult>(
      `/scenarios/compare?delay_days=${delayDays}`,
      scenarioIds,
    ),
};
