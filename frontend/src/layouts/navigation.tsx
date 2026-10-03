/** 一级导航结构（按业务身份区分）。 */

import {
  AppstoreOutlined,
  AuditOutlined,
  BarChartOutlined,
  DashboardOutlined,
  FileTextOutlined,
  MessageOutlined,
  SettingOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons';
import type { ReactNode } from 'react';
import type { Role } from '@/types';

export interface NavItem {
  key: string;
  path: string;
  label: string;
  icon: ReactNode;
  /** 移动端底部导航是否展示 */
  mobile?: boolean;
}

export const MERCHANT_NAV: NavItem[] = [
  { key: 'today', path: '/today', label: '今日决策', icon: <DashboardOutlined />, mobile: true },
  { key: 'events', path: '/events', label: '现金事件', icon: <AppstoreOutlined />, mobile: true },
  { key: 'analysis', path: '/analysis', label: '情景分析', icon: <BarChartOutlined />, mobile: true },
  { key: 'family', path: '/family', label: '家庭协同', icon: <TeamOutlined />, mobile: true },
  {
    key: 'consultations',
    path: '/consultations',
    label: '经营咨询',
    icon: <MessageOutlined />,
    mobile: true,
  },
  { key: 'settings', path: '/settings', label: '我的', icon: <SettingOutlined /> },
];

export const CONSULTANT_NAV: NavItem[] = [
  { key: 'workspace', path: '/consultant', label: '咨询工作台', icon: <AuditOutlined />, mobile: true },
  { key: 'records', path: '/consultant/records', label: '事项记录', icon: <FileTextOutlined />, mobile: true },
  { key: 'settings', path: '/settings', label: '我的', icon: <UserOutlined />, mobile: true },
];

/** 家庭成员的两项导航。 */
export const FAMILY_NAV: NavItem[] = [
  { key: 'cards', path: '/family/cards', label: '家庭协同', icon: <TeamOutlined />, mobile: true },
  { key: 'settings', path: '/settings', label: '我的', icon: <SettingOutlined />, mobile: true },
];

/**
 * 按业务身份取导航。
 *
 * 三种身份彼此独立、没有等级关系，因此这里只做「身份 → 菜单」的映射，
 * 不再有「附加管理员入口」这类叠加逻辑。
 */
export function navForRoles(roles: Role[]): NavItem[] {
  // 一个账户可以同时拥有多个业务身份；导航取最高业务身份对应的一套，
  // 避免同时展示多套菜单造成认知负担。
  if (roles.includes('merchant')) return MERCHANT_NAV;
  if (roles.includes('consultant')) return CONSULTANT_NAV;
  return FAMILY_NAV;
}

export function landingPath(roles: Role[]): string {
  if (roles.includes('merchant')) return '/today';
  if (roles.includes('consultant')) return '/consultant';
  return '/family/cards';
}
