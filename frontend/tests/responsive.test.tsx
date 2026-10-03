/**
 * 响应式架构单元测试。
 *
 * 覆盖 V3 要求：统一断点、ResponsiveDataView 在手机端渲染卡片、
 * 底部导航不再出现已删除的管理入口。
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ResponsiveDataView } from '@/components/ui';
import { classifyWidth, MOBILE_MAX, SIDEBAR_MIN_WIDTH } from '@/hooks/useResponsive';
import { navForRoles } from '@/layouts/navigation';
import type { Role } from '@/types';
import { setViewportWidth } from './setup';

const MERCHANT: Role[] = ['merchant'];
const FAMILY: Role[] = ['family_member'];
const CONSULTANT: Role[] = ['consultant'];

describe('断点判定', () => {
  it('以 767px 为手机上限，与 CSS 媒体查询一致', () => {
    expect(MOBILE_MAX).toBe(767);
    expect(SIDEBAR_MIN_WIDTH).toBe(1024);
  });

  it('三个区间的判定互斥且完备', () => {
    const cases: [number, 'mobile' | 'tablet' | 'desktop'][] = [
      [375, 'mobile'],
      [390, 'mobile'],
      [430, 'mobile'],
      [MOBILE_MAX, 'mobile'],
      [MOBILE_MAX + 1, 'tablet'],
      [768, 'tablet'],
      [1023, 'tablet'],
      [SIDEBAR_MIN_WIDTH, 'desktop'],
      [1440, 'desktop'],
    ];
    for (const [width, expected] of cases) {
      const state = classifyWidth(width);
      expect(
        state.isMobile ? 'mobile' : state.isDesktop ? 'desktop' : 'tablet',
        `宽度 ${width} 应判定为 ${expected}`,
      ).toBe(expected);
    }
  });

  it('只有桌面宽度才展示左侧栏', () => {
    expect(classifyWidth(390).hasRoomForSidebar).toBe(false);
    expect(classifyWidth(1023).hasRoomForSidebar).toBe(false);
    expect(classifyWidth(1024).hasRoomForSidebar).toBe(true);
  });
});

describe('业务身份导航', () => {
  it('经营者可见 6 项，且没有系统管理入口', () => {
    const labels = navForRoles(MERCHANT).map((item) => item.label);
    expect(labels).toEqual(['今日决策', '现金事件', '情景分析', '家庭协同', '经营咨询', '我的']);
    expect(labels).not.toContain('系统管理');
    expect(labels).not.toContain('系统概览');
    expect(labels).not.toContain('用户管理');
  });

  it('家庭成员只有家庭协同与我的', () => {
    expect(navForRoles(FAMILY).map((item) => item.label)).toEqual(['家庭协同', '我的']);
  });

  it('咨询人员只有咨询工作台、事项记录与我的', () => {
    expect(navForRoles(CONSULTANT).map((item) => item.label)).toEqual([
      '咨询工作台',
      '事项记录',
      '我的',
    ]);
  });

  it('手机端底部导航最多 5 项', () => {
    for (const roles of [MERCHANT, FAMILY, CONSULTANT]) {
      const mobileItems = navForRoles(roles).filter((item) => item.mobile);
      expect(mobileItems.length).toBeLessThanOrEqual(5);
    }
  });
});

describe('ResponsiveDataView', () => {
  it('手机视口渲染卡片列表', () => {
    setViewportWidth(390);
    render(
      <ResponsiveDataView
        desktopTable={<div data-testid="table-view">表格</div>}
        mobileCards={<div data-testid="card-view">卡片</div>}
      />,
    );
    expect(screen.getByTestId('card-view')).toBeInTheDocument();
    expect(screen.queryByTestId('table-view')).not.toBeInTheDocument();
  });

  it('桌面视口渲染表格', () => {
    setViewportWidth(1440);
    render(
      <ResponsiveDataView
        desktopTable={<div data-testid="table-view">表格</div>}
        mobileCards={<div data-testid="card-view">卡片</div>}
      />,
    );
    expect(screen.getByTestId('table-view')).toBeInTheDocument();
    expect(screen.queryByTestId('card-view')).not.toBeInTheDocument();
  });

  it('平板视口保留表格（不是手机就不断言为卡片）', () => {
    setViewportWidth(900);
    render(
      <ResponsiveDataView
        desktopTable={<div data-testid="table-view">表格</div>}
        mobileCards={<div data-testid="card-view">卡片</div>}
      />,
    );
    expect(screen.getByTestId('table-view')).toBeInTheDocument();
  });
});
