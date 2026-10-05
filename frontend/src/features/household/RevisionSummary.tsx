/**
 * 更正通知卡里的「改前 → 改后」明细。
 *
 * 数据来自后端按真实 `CashEventRevision` 生成的 `revision_summary`：
 * 只渲染中文 `label` 与 `before_text` / `after_text`，
 * 绝不把 `field` 这类内部字段名显示给用户。
 */

export interface RevisionChange {
  field?: string;
  label?: string;
  before_text?: string | null;
  after_text?: string | null;
}

export default function RevisionSummary({ data }: { data: Record<string, unknown> }) {
  const changes = Array.isArray(data.changes) ? (data.changes as RevisionChange[]) : [];
  if (changes.length === 0) return <>—</>;

  return (
    <div className="gew-revision-summary">
      {typeof data.event_title === 'string' && data.event_title ? (
        <div className="gew-revision-summary__event">事项：{data.event_title}</div>
      ) : null}
      {changes.map((item, index) => (
        <div className="gew-revision-summary__row" key={`${item.field ?? 'change'}-${index}`}>
          <span className="gew-revision-summary__field">{item.label ?? '变更'}</span>
          <span className="gew-revision-summary__before">{item.before_text ?? '—'}</span>
          <span className="gew-revision-summary__arrow" aria-hidden="true">
            →
          </span>
          <span className="gew-revision-summary__after">{item.after_text ?? '—'}</span>
        </div>
      ))}
    </div>
  );
}
