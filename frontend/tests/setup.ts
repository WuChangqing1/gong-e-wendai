import '@testing-library/jest-dom/vitest';

/**
 * 可用的 matchMedia 桩。
 *
 * `useResponsive()` 依赖 `window.matchMedia` 判定 mobile / tablet / desktop。
 * 早期桩无条件返回 `matches: false`，会让所有用例被判定为「平板」——
 * 一旦测试需要切换视口就会失真。这里按真实需求求值：
 * 支持 `(min-width: Npx)` 与 `(max-width: Npx)`，并允许动态改变视口宽度。
 */

type Listener = (event: { matches: boolean; media: string }) => void;

const listeners = new Set<{ query: string; handler: Listener }>();

function evaluate(query: string): boolean {
  const width = window.innerWidth;
  const min = query.match(/min-width:\s*(\d+)px/);
  const max = query.match(/max-width:\s*(\d+)px/);
  if (min && width < Number(min[1])) return false;
  if (max && width > Number(max[1])) return false;
  // 只认宽度查询；其它查询（prefers-* 等）一律 false，与真实环境常见取值一致。
  return Boolean(min || max);
}

function createMediaQueryList(query: string) {
  return {
    matches: evaluate(query),
    media: query,
    onchange: null,
    addListener: (handler: Listener) => {
      listeners.add({ query, handler });
    },
    removeListener: (handler: Listener) => {
      [...listeners].forEach((item) => {
        if (item.query === query && item.handler === handler) listeners.delete(item);
      });
    },
    addEventListener: (_type: string, handler: Listener) => {
      listeners.add({ query, handler });
    },
    removeEventListener: (_type: string, handler: Listener) => {
      [...listeners].forEach((item) => {
        if (item.query === query && item.handler === handler) listeners.delete(item);
      });
    },
    dispatchEvent: () => false,
  };
}

window.matchMedia = ((query: string) =>
  createMediaQueryList(query)) as unknown as typeof window.matchMedia;

/** 设置测试视口宽度，并通知所有已注册的媒体查询监听者。 */
export function setViewportWidth(width: number): void {
  // jsdom 的 window.innerWidth 是只读访问器，必须用 defineProperty 覆盖。
  Object.defineProperty(window, 'innerWidth', {
    configurable: true,
    writable: true,
    value: width,
  });
  Object.defineProperty(document.documentElement, 'clientWidth', {
    configurable: true,
    value: width,
  });
  [...listeners].forEach(({ query, handler }) => {
    handler({ matches: evaluate(query), media: query });
  });
}

if (!window.ResizeObserver) {
  window.ResizeObserver = class {
    observe() {
      return undefined;
    }
    unobserve() {
      return undefined;
    }
    disconnect() {
      return undefined;
    }
  } as unknown as typeof window.ResizeObserver;
}
