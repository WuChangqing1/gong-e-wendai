/** 认证与账户接口。 */

import { get, patch, post } from '@/api/client';
import type {
  AccountOverview,
  AccountSnapshot,
  HealthStatus,
  MerchantProfile,
  UserMe,
  UserPublic,
} from '@/types';

export interface LoginPayload {
  username: string;
  password: string;
}

export interface RegisterPayload {
  username: string;
  password: string;
  display_name: string;
  roles: string[];
  phone?: string | null;
  business_name?: string | null;
  business_type?: string | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: UserPublic;
}

export const authApi = {
  register: (payload: RegisterPayload) => post<TokenResponse>('/auth/register', payload),
  login: (payload: LoginPayload) => post<TokenResponse>('/auth/login', payload),
  refresh: () => post<TokenResponse>('/auth/refresh', {}),
  logout: () => post<{ message: string }>('/auth/logout', {}),
};

export const meApi = {
  read: () => get<UserMe>('/me'),
  update: (payload: { display_name?: string; phone?: string; email?: string }) =>
    patch<UserMe>('/me', payload),
  changePassword: (payload: { current_password: string; new_password: string }) =>
    post<{ message: string; code: string }>('/me/password', payload),
  sessions: () => get<{ active_sessions: number }>('/me/sessions'),
};

export const merchantApi = {
  profile: () => get<MerchantProfile>('/merchant/profile'),
  updateProfile: (payload: Partial<MerchantProfile>) =>
    patch<MerchantProfile>('/merchant/profile', payload),
  overview: () => get<AccountOverview>('/account/overview'),
  snapshots: (limit = 30) => get<AccountSnapshot[]>('/account/snapshots', { params: { limit } }),
  latestSnapshot: () => get<AccountSnapshot | null>('/account/snapshots/latest'),
  createSnapshot: (payload: {
    opening_balance_cents: number;
    pending_settlement_cents?: number;
    snapshot_at?: string | null;
    note?: string | null;
  }) => post<AccountSnapshot>('/account/snapshots', payload),
};

export const healthApi = {
  read: () => get<HealthStatus>('/health'),
};
