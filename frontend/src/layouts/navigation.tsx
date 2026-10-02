/** 一级导航结构（按角色区分）。 */

import {
  AppstoreOutlined,
  AuditOutlined,
  BarChartOutlined,
  DashboardOutlined,
  FileTextOutlined,
  HomeOutlined,
  MessageOutlined,
  SafetyCertificateOutlined,
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

export const ADMIN_NAV: NavItem[] = [
  { key: 'overview', path: '/admin', label: '系统概览', icon: <HomeOutlined />, mobile: true },
  { key: 'users', path: '/admin/users', label: '用户管理', icon: <TeamOutlined />, mobile: true },
  {
    key: 'runtime',
    path: '/admin/runtime',
    label: '运行状态',
    icon: <SafetyCertificateOutlined />,
    mobile: true,
  },
];

export function navForRoles(roles: Role[]): NavItem[] {
  // 一个账户可以同时拥有多个角色；导航取"最高业务角色"对应的一套，
  // 避免同时展示多套菜单造成认知负担。管理员额外可见管理入口。
  if (roles.includes('merchant')) {
    const items = [...MERCHANT_NAV];
    if (roles.includes('admin')) {
      items.push({ key: 'admin', path: '/admin', label: '系统管理', icon: <SafetyCertificateOutlined /> });
    }
    return items;
  }
  if (roles.includes('consultant')) return CONSULTANT_NAV;
  if (roles.includes('admin')) return ADMIN_NAV;
  return [
    { key: 'cards', path: '/family/cards', label: '家庭协同', icon: <TeamOutlined />, mobile: true },
    { key: 'settings', path: '/settings', label: '我的', icon: <SettingOutlined />, mobile: true },
  ];
}

export function landingPath(roles: Role[]): string {
  if (roles.includes('merchant')) return '/today';
  if (roles.includes('consultant')) return '/consultant';
  if (roles.includes('admin')) return '/admin';
  return '/family/cards';
}
