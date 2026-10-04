/** 共享展示组件。 */

import type { ReactNode } from 'react';

import { useIsMobile } from '@/hooks/useResponsive';

type Tone = 'ok' | 'warning' | 'danger' | 'info' | 'neutral';

const TONE_ICON: Record<Tone, string> = {
  ok: '✓',
  warning: '!',
  danger: '×',
  info: 'i',
  neutral: '·',
};

/**
 * 状态标签：始终用「图标 + 文字 + 颜色」三重表达，不单靠颜色传达风险。
 */
export function StatusTag({
  tone = 'neutral',
  children,
  title,
}: {
  tone?: Tone;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span className={`gew-status gew-status--${tone}`} title={title}>
      <span aria-hidden="true" style={{ fontWeight: 700, fontSize: 11 }}>
        {TONE_ICON[tone]}
      </span>
      {children}
    </span>
  );
}

export function MetricCard({
  label,
  value,
  footnote,
  tone = 'default',
  hint,
}: {
  label: ReactNode;
  value: ReactNode;
  footnote?: ReactNode;
  tone?: 'default' | 'inflow' | 'outflow' | 'danger';
  hint?: string;
}) {
  const toneClass =
    tone === 'inflow'
      ? ' gew-metric__value--inflow'
      : tone === 'outflow'
        ? ' gew-metric__value--outflow'
        : tone === 'danger'
          ? ' gew-metric__value--danger'
          : '';
  return (
    <div className="gew-metric" title={hint}>
      <span className="gew-metric__label">{label}</span>
      <span className={`gew-metric__value${toneClass}`}>{value}</span>
      {footnote ? <span className="gew-metric__footnote">{footnote}</span> : null}
    </div>
  );
}

export function SectionCard({
  title,
  extra,
  children,
  flat = false,
  bodyClassName,
}: {
  title?: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
  flat?: boolean;
  bodyClassName?: string;
}) {
  return (
    <section className={`gew-card${flat ? ' gew-card--flat' : ''}`}>
      {title ? (
        <header className="gew-card__head">
          <span className="gew-card__title">{title}</span>
          {extra}
        </header>
      ) : null}
      <div className={`gew-card__body${bodyClassName ? ` ${bodyClassName}` : ''}`}>{children}</div>
    </section>
  );
}

/**
 * 页头：只有标题与右侧操作区。
 *
 * 刻意**不支持**副标题：页面标题下面再写一句「这一页是干什么的」，
 * 既占位置又像产品说明，真正的口径说明应当出现在它约束的那块内容旁边。
 */
export function PageHeader({
  title,
  extra,
}: {
  title: ReactNode;
  extra?: ReactNode;
}) {
  return (
    <div className="gew-page-header">
      <div>
        {/* 手机端由 CSS 把这个 H1 视觉隐藏（顶部栏已显示当前页面名），
            但仍留在 DOM 中，保证屏幕阅读器与自动化用例能定位到页面标题。 */}
        <h1 className="gew-page-title">{title}</h1>
      </div>
      {extra ? <div className="gew-page-header__extra">{extra}</div> : null}
    </div>
  );
}

/**
 * 响应式数据视图：桌面渲染高信息密度表格，手机渲染卡片列表。
 *
 * 这是「手机端不再横向拖动宽表格」的统一入口。页面组件不要自己判断
 * `window.innerWidth`，也不要重复写 767 这类魔法数字。
 *
 * 两个视图都必须是**纯展示**：数据、分页与查询状态由调用方持有，
 * 切换视口不会触发任何接口请求。
 */
export function ResponsiveDataView({
  desktopTable,
  mobileCards,
}: {
  /** 桌面（>= 768px）视图，通常是 Ant Design `<Table>` */
  desktopTable: ReactNode;
  /** 手机（<= 767px）视图，通常是卡片列表 */
  mobileCards: ReactNode;
}) {
  const isMobile = useIsMobile();
  return <>{isMobile ? mobileCards : desktopTable}</>;
}

export function InlineNote({
  tone = 'neutral',
  children,
}: {
  tone?: 'neutral' | 'ok' | 'warning' | 'danger' | 'info';
  children: ReactNode;
}) {
  const icon =
    tone === 'danger' ? '×' : tone === 'warning' ? '!' : tone === 'info' ? 'i' : tone === 'ok' ? '✓' : '·';
  const modifier = tone === 'ok' ? 'info' : tone;
  return (
    <div className={`gew-inline-note${modifier === 'neutral' ? '' : ` gew-inline-note--${modifier}`}`}>
      <span aria-hidden="true" style={{ fontWeight: 700 }}>
        {icon}
      </span>
      <div>{children}</div>
    </div>
  );
}

export function KeyValueList({ items }: { items: { key: ReactNode; value: ReactNode }[] }) {
  return (
    <div className="gew-kv-list">
      {items.map((item, index) => (
        <div className="gew-kv-list__row" key={index}>
          <span className="gew-kv-list__key">{item.key}</span>
          <span className="gew-kv-list__value">{item.value}</span>
        </div>
      ))}
    </div>
  );
}

export function DescriptionGrid({
  items,
}: {
  items: { label: ReactNode; value: ReactNode }[];
}) {
  return (
    <div className="gew-desc-grid">
      {items.map((item, index) => (
        <div key={index}>
          <div className="gew-desc-item__label">{item.label}</div>
          <div className="gew-desc-item__value">{item.value}</div>
        </div>
      ))}
    </div>
  );
}
