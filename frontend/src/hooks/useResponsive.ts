/**
 * 统一响应式判定。
 *
 * 断点定义（**全项目唯一来源**）：
 *
 * | 名称 | 宽度 | 语义 |
 * | --- | --- | --- |
 * | mobile | `<= 767px` | 手机：隐藏左侧栏，底部标签栏承担导航，表格改卡片 |
 * | tablet | `768 - 1023px` | 平板：保留侧栏布局，但内容按较窄宽度排布 |
 * | desktop | `>= 1024px` | 桌面：左侧栏 + 顶部栏 + 高信息密度表格与图表 |
 *
 * 约束：
 * * `MOBILE_MAX = 767` 必须与 `styles/tokens.css` 的
 *   `@media (max-width: 767px)` 完全一致，否则会出现
 *   「CSS 认为不是手机、JS 认为是手机」的错位。
 * * 侧栏专用断点 `SIDEBAR_MIN_WIDTH = 1024` 必须与
 *   `@media (max-width: 1023px)` 一致。
 * * 页面组件**不得**再自行判断 `window.innerWidth` 或写 767/992/1024 这类魔法数字，
 *   一律通过这里的 Hook 或 `ResponsiveDataView` / `ResponsiveDrawer` 表达。
 */

import { useEffect, useState, useSyncExternalStore } from 'react';

/** 手机断点上限：与 CSS `@media (max-width: 767px)` 保持一致。 */
export const MOBILE_MAX = 767;

/** 平板断点上限：`768 <= width <= 1023` 视为平板。 */
export const TABLET_MAX = 1023;

/** 侧栏出现的最小宽度：与 CSS `@media (max-width: 1023px)` 保持一致。 */
export const SIDEBAR_MIN_WIDTH = 1024;

export interface ResponsiveState {
  /** 手机宽度（<= 767px） */
  isMobile: boolean;
  /** 平板宽度（768 - 1023px） */
  isTablet: boolean;
  /** 桌面宽度（>= 1024px） */
  isDesktop: boolean;
  /** 是否有足够宽度展示左侧栏（>= 1024px） */
  hasRoomForSidebar: boolean;
}

export function classifyWidth(width: number): ResponsiveState {
  const isMobile = width <= MOBILE_MAX;
  const isDesktop = width >= SIDEBAR_MIN_WIDTH;
  return {
    isMobile,
    isTablet: !isMobile && !isDesktop,
    isDesktop,
    hasRoomForSidebar: isDesktop,
  };
}

function currentWidth(): number {
  return typeof window === 'undefined' ? 1440 : window.innerWidth;
}

export function useViewportWidth(): number {
  const [width, setWidth] = useState<number>(currentWidth);

  useEffect(() => {
    const handle = () => setWidth(currentWidth());
    handle();
    window.addEventListener('resize', handle);
    return () => window.removeEventListener('resize', handle);
  }, []);

  return width;
}

/** 仅用于内部订阅：把手写的 resize 监听换成 matchMedia，避免每次 resize 触发重渲染风暴。 */
function subscribeMedia(query: string, onChange: () => void): () => void {
  if (typeof window === 'undefined' || !window.matchMedia) return () => undefined;
  const list = window.matchMedia(query);
  list.addEventListener('change', onChange);
  return () => list.removeEventListener('change', onChange);
}

function useMediaQuery(query: string, fallback: boolean): boolean {
  return useSyncExternalStore(
    (onChange) => subscribeMedia(query, onChange),
    () => (typeof window === 'undefined' || !window.matchMedia ? fallback : window.matchMedia(query).matches),
    () => fallback,
  );
}

/**
 * 统一响应式状态。
 *
 * 由 `window.matchMedia` 驱动：视口跨越断点时才重新渲染，
 * **不会**因为每个像素的 resize 反复触发（避免连带重复请求接口）。
 */
export function useResponsive(): ResponsiveState {
  const isMobile = useMediaQuery(`(max-width: ${MOBILE_MAX}px)`, false);
  const isDesktop = useMediaQuery(`(min-width: ${SIDEBAR_MIN_WIDTH}px)`, true);
  return {
    isMobile,
    isTablet: !isMobile && !isDesktop,
    isDesktop,
    hasRoomForSidebar: isDesktop,
  };
}

export function isMobileWidth(width: number): boolean {
  return width <= MOBILE_MAX;
}

/**
 * 当前是否为手机视口（<= 767px）。
 *
 * 用于需要在**结构层面**分叉的场景（表格换卡片列表、图表精简标签）。
 * 纯样式差异请优先写进 CSS，不要用这个 Hook 去做媒体查询能做到的事。
 */
export function useIsMobile(): boolean {
  return useResponsive().isMobile;
}

/**
 * 抽屉宽度：桌面使用设计宽度，窄屏一律占满可用宽度。
 *
 * 固定宽度会导致抽屉超出手机视口，底部操作按钮点不到。
 */
export function useDrawerWidth(desktopWidth: number): number | string {
  return useIsMobile() ? '100%' : desktopWidth;
}
