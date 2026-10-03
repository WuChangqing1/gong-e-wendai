/** 经营咨询接口。 */

import { get, post } from '@/api/client';
import type { ConsultationCase, ConsultationStatus, Page } from '@/types';

export interface ConsultationCreatePayload {
  cash_event_id: string;
  question_type: string;
  question: string;
  ai_draft?: string | null;
  status?: 'draft' | 'submitted';
}

export const consultationApi = {
  list: (params: { page?: number; page_size?: number; status?: ConsultationStatus } = {}) =>
    get<Page<ConsultationCase>>('/consultations', { params }),
  detail: (id: string) => get<ConsultationCase>(`/consultations/${id}`),
  create: (payload: ConsultationCreatePayload) =>
    post<ConsultationCase>('/consultations', payload),
  submit: (id: string) => post<ConsultationCase>(`/consultations/${id}/submit`, {}),
  approveUpdate: (id: string) =>
    post<{ cash_event_id: string; new_version: number }>(`/consultations/${id}/apply-update`, {}),
  allowedFields: (cashEventId: string) =>
    get<{ allowed_fields: string[]; forbidden_fields: string[]; preview: Record<string, unknown> }>(
      `/consultations/allowed-fields`,
      { params: { cash_event_id: cashEventId } },
    ),
};

export const consultantApi = {
  detail: (id: string) => get<ConsultationCase>(`/consultations/${id}`),
  queue: (params: { bucket?: string; page?: number; page_size?: number } = {}) =>
    get<Page<ConsultationCase>>('/consultations/queue', { params }),
  start: (id: string) => post<ConsultationCase>(`/consultations/${id}/start`, {}),
  requestInfo: (id: string, content: string) =>
    post<ConsultationCase>(`/consultations/${id}/request-info`, { content }),
  verify: (id: string, payload: { resolution_summary: string; resolution_fields?: Record<string, unknown> }) =>
    post<ConsultationCase>(`/consultations/${id}/verify`, payload),
  close: (id: string, payload: { resolution_summary?: string } = {}) =>
    post<ConsultationCase>(`/consultations/${id}/close`, payload),
};
