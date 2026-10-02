/** 智能服务接口（AI 辅助能力）。 */

import { get, post } from '@/api/client';

export interface AiExplainPayload {
  max_withdrawable_cents: number | null;
  opening_balance_cents: number;
  buffer_cents: number;
  status: string;
  limiting_timestamp: string | null;
  limiting_balance_cents: number | null;
  limiting_event_title: string | null;
  limiting_reason: string;
  payment_gap_cents: number;
  buffer_gap_cents: number;
  window_inflow_cents: number;
  window_outflow_cents: number;
  pending_inflows: { title: string; amount_text: string; scheduled_at: string }[];
}

export interface AiExtractPayload {
  text: string;
  timezone?: string;
}

export interface AiExtractedEvent {
  title: string;
  direction: 'inflow' | 'outflow';
  amount_cents: number;
  scheduled_at: string;
  state: string;
  source_label: string | null;
  confidence: Record<string, number>;
  warnings: string[];
}

export interface AiDraftPayload {
  question_type: string;
  question: string;
  fields: Record<string, unknown>;
}

export interface AiStatus {
  enabled: boolean;
  configured: boolean;
  available: boolean;
  model: string | null;
}

export const aiApi = {
  status: () => get<AiStatus>('/ai/status'),
  explain: (payload: AiExplainPayload) =>
    post<{ explanation: string; used_fields: string[] }>('/ai/explain-analysis', payload),
  extract: (payload: AiExtractPayload) =>
    post<{ event: AiExtractedEvent; raw_text: string }>('/ai/extract-cash-event', payload),
  draftConsultation: (payload: AiDraftPayload) =>
    post<{ draft: string; allowed_fields: string[] }>('/ai/draft-consultation', payload),
};
