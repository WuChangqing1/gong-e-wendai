/**
 * 响应式尺寸工具。
 *
 * 断点与 `styles/tokens.css` 的 `@media (max-width: 767px)` 严格一致：
 * CSS 负责导航/间距等纯样式切换，这里负责需要**改变组件结构或图表配置**的场景
 * （表格换成卡片列表、图表精简标签、页头去重标题）。
 *
 * 两处断点必须一起改，否则会出现「CSS 认为不是手机、JS 认为是手机」的错位。
 */

import { useEffect, useState } from 'react';

/** 手机断点：与 CSS `@media (max-width: 767px)` 保持一致。 */
export const MOBILE_MAX = 767;

export function useViewportWidth(): number {
  const [width, setWidth] = useState<number>(() =>
    typeof window === 'undefined' ? 1440 : window.innerWidth,
  );

  useEffect(() => {
    const handle = () => setWidth(window.innerWidth);
    handle();
    window.addEventListener('resize', handle);
    return () => window.removeEventListener('resize', handle);
  }, []);

  return width;
}

export function isMobileWidth(width: number): boolean {
  return width <= MOBILE_MAX;
}

/**
 * 当前是否为手机视口（≤ 767px）。
 *
 * 用于需要在结构层面分叉的场景。纯样式差异请优先写进 CSS，
 * 不要用这个 Hook 去做本可以由媒体查询完成的事。
 */
export function useIsMobile(): boolean {
  return isMobileWidth(useViewportWidth());
}

/**
 * 抽屉宽度：桌面使用设计宽度，窄屏一律占满可用宽度。
 *
 * 固定宽度会导致抽屉超出手机视口，底部操作按钮点不到。
 */
export function useDrawerWidth(desktopWidth: number): number | string {
  const width = useViewportWidth();
  return isMobileWidth(width) ? '100%' : desktopWidth;
}
