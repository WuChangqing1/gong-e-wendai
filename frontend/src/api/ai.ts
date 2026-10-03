/** 智能服务接口（AI 辅助能力）。 */

import { API_BASE_URL, get, post } from '@/api/client';

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
  direction: 'inflow' | 'outflow' | null;
  amount_cents: number | null;
  scheduled_at: string | null;
  state: string;
  event_type: string;
  source_label: string | null;
  channel: string | null;
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
  provider: string;
  text_model: string | null;
  vision_model: string | null;
}

export interface AiExtractResponse {
  event: AiExtractedEvent;
  raw_text: string;
  filtered_amounts: string[];
  note: string;
}

/** 一次性上传一张截图并提取结构化信息。 */
async function extractFromImage(file: File): Promise<AiExtractResponse> {
  const form = new FormData();
  form.append('file', file);
  const response = await fetch(`${API_BASE_URL}/ai/extract-cash-event-from-image`, {
    method: 'POST',
    body: form,
    credentials: 'include',
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
  });
  if (!response.ok) {
    let message = '智能服务暂时不可用，你仍可以手动完成当前操作';
    try {
      const body = (await response.json()) as { message?: string };
      if (body?.message) message = body.message;
    } catch {
      // 保持统一降级文案
    }
    throw new Error(message);
  }
  return (await response.json()) as AiExtractResponse;
}

export const aiApi = {
  status: () => get<AiStatus>('/ai/status'),
  explain: (payload: AiExplainPayload) =>
    post<{ explanation: string; used_fields: string[] }>('/ai/explain-analysis', payload),
  extract: (payload: AiExtractPayload) =>
    post<AiExtractResponse>('/ai/extract-cash-event', payload),
  extractFromImage,
  draftConsultation: (payload: AiDraftPayload) =>
    post<{ draft: string; allowed_fields: string[] }>('/ai/draft-consultation', payload),
};
