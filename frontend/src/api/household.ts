/** 家庭协同接口。 */

import { del, get, patch, post } from '@/api/client';
import type {
  CardType,
  Household,
  HouseholdCard,
  HouseholdMember,
  ReactionType,
} from '@/types';

export interface ShareFieldSelection {
  max_withdrawable: boolean;
  planned_amount: boolean;
  limiting_point: boolean;
  key_payments: boolean;
  risk_summary: boolean;
  pending_inflows: boolean;
}

export const householdApi = {
  current: () => get<Household | null>('/households/current'),
  create: (payload: { name: string }) => post<Household>('/households', payload),
  rotateInvite: () => post<{ invite_code: string }>('/households/invite-code/rotate', {}),
  join: (payload: { invite_code: string; relation_label?: string | null }) =>
    post<{ membership_id: string; status: string; household_name: string }>(
      '/households/join',
      payload,
    ),
  members: () => get<HouseholdMember[]>('/households/members'),
  approve: (membershipId: string) =>
    post<{ message: string }>(`/households/members/${membershipId}/approve`, {}),
  remove: (membershipId: string) =>
    post<{ message: string }>(`/households/members/${membershipId}/remove`, {}),
  myMemberships: () =>
    get<{ household_id: string; household_name: string; status: string }[]>(
      '/households/memberships/mine',
    ),
};

export interface CardCreatePayload {
  card_type: CardType;
  title: string;
  summary?: string | null;
  shared_fields: string[];
  analysis_result_id?: string | null;
  cash_event_id?: string | null;
  planned_household_amount_cents?: number | null;
}

export const householdCardApi = {
  list: (params: { card_type?: CardType; unread_only?: boolean } = {}) =>
    get<HouseholdCard[]>('/household-cards', { params }),
  detail: (id: string) => get<HouseholdCard>(`/household-cards/${id}`),
  create: (payload: CardCreatePayload) => post<HouseholdCard>('/household-cards', payload),
  preview: (payload: { card_type: CardType; shared_fields: string[]; cash_event_id?: string | null; analysis_result_id?: string | null; planned_household_amount_cents?: number | null }) =>
    post<{ title: string; summary: string; payload: Record<string, unknown>; shared_fields: string[] }>(
      '/household-cards/preview',
      payload,
    ),
  markRead: (id: string) => post<HouseholdCard>(`/household-cards/${id}/read`, {}),
  react: (id: string, reaction: ReactionType) =>
    post<HouseholdCard>(`/household-cards/${id}/react`, { reaction }),
  comment: (id: string, content: string) =>
    post<HouseholdCard>(`/household-cards/${id}/comments`, { content }),
  remove: (id: string) => del<{ message: string }>(`/household-cards/${id}`),
  revisionCards: () => get<HouseholdCard[]>('/household-cards/revision-notices'),
  patch: (id: string, payload: { planned_household_amount_cents?: number | null }) =>
    patch<HouseholdCard>(`/household-cards/${id}`, payload),
};
