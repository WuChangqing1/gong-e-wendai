/** 共享展示组件。 */

import type { ReactNode } from 'react';

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

export function PageHeader({
  title,
  subtitle,
  extra,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  extra?: ReactNode;
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-between',
        gap: 16,
        flexWrap: 'wrap',
        marginBottom: 20,
      }}
    >
      <div>
        <h1 className="gew-page-title">{title}</h1>
        {subtitle ? <p className="gew-page-subtitle">{subtitle}</p> : null}
      </div>
      {extra}
    </div>
  );
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
