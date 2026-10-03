/**
 * 轻量 UI 状态与当前用户缓存。
 *
 * 服务器数据由 TanStack Query 管理，这里只存放界面状态与必要的最小用户信息。
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { Role, UserMe } from '@/types';

export interface AuthState {
  user: UserMe | null;
  initialised: boolean;
  setUser: (user: UserMe | null) => void;
  setInitialised: (value: boolean) => void;
  clear: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      initialised: false,
      setUser: (user) => set({ user, initialised: true }),
      setInitialised: (initialised) => set({ initialised }),
      clear: () => set({ user: null, initialised: true }),
    }),
    {
      name: 'gew-auth',
      partialize: (state) => ({ user: state.user }),
    },
  ),
);

export function primaryRole(user: UserMe | null): Role | null {
  if (!user || user.roles.length === 0) return null;
  const order: Role[] = ['merchant', 'consultant', 'family_member'];
  return order.find((role) => user.roles.includes(role)) ?? user.roles[0];
}

export function hasRole(user: UserMe | null, role: Role): boolean {
  return Boolean(user?.roles.includes(role));
}

export function hasPermission(user: UserMe | null, permission: string): boolean {
  return Boolean(user?.permissions.includes(permission));
}

export interface UiState {
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  /** 当前是否为窄屏（由布局断点写入） */
  isMobile: boolean;
  setIsMobile: (value: boolean) => void;
  lastStaleNoticeId: string | null;
  setLastStaleNoticeId: (id: string | null) => void;
}

export const useUiStore = create<UiState>()((set) => ({
  sidebarCollapsed: false,
  toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
  isMobile: false,
  setIsMobile: (value) => set({ isMobile: value }),
  lastStaleNoticeId: null,
  setLastStaleNoticeId: (id) => set({ lastStaleNoticeId: id }),
}));
