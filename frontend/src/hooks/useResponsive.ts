/** 响应式尺寸工具。 */

import { useEffect, useState } from 'react';

const MOBILE_MAX = 767;

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
 * 抽屉宽度：桌面使用设计宽度，窄屏一律占满可用宽度。
 *
 * 固定宽度会导致抽屉超出手机视口，底部操作按钮点不到。
 */
export function useDrawerWidth(desktopWidth: number): number | string {
  const width = useViewportWidth();
  return isMobileWidth(width) ? '100%' : desktopWidth;
}
