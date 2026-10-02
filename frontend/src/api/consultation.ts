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

export const adminApi = {
  overview: () =>
    get<{
      users: number;
      merchants: number;
      cash_events: number;
      consultations: number;
      households: number;
      analysis_results: number;
      ai_enabled: boolean;
      app_env: string;
      version: string;
    }>('/admin/overview'),
  users: (params: { page?: number; page_size?: number; search?: string } = {}) =>
    get<Page<{ id: string; username: string; display_name: string; roles: string[]; status: string; created_at: string; last_login_at: string | null }>>(
      '/admin/users',
      { params },
    ),
  setUserStatus: (userId: string, status: 'active' | 'disabled') =>
    post<{ message: string }>(`/admin/users/${userId}/status`, { status }),
  runtime: () =>
    get<{
      database_ok: boolean;
      database_size_bytes: number;
      wal_enabled: boolean;
      uptime_seconds: number;
      event_count: number;
      stale_results: number;
      recent_errors: string[];
    }>('/admin/runtime'),
  audit: (limit = 50) => get<Record<string, unknown>[]>('/admin/audit-logs', { params: { limit } }),
};
