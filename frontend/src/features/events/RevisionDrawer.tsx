/** 版本历史抽屉：当前版本、历史版本、修改字段、修改时间、修改人、版本对比。 */

import { useQuery } from '@tanstack/react-query';
import { Drawer, Empty, Skeleton, Space, Table, Tag, Tooltip } from 'antd';

import { cashEventApi } from '@/api/cashflow';
import { errorMessage } from '@/api/client';
import { queryKeys } from '@/api/queryClient';
import { InlineNote, StatusTag } from '@/components/ui';
import type { FieldChange } from '@/types';
import { formatDateTime } from '@/utils/datetime';

export default function RevisionDrawer({
  eventId,
  open,
  onClose,
}: {
  eventId: string | null;
  open: boolean;
  onClose: () => void;
}) {
  const query = useQuery({
    queryKey: queryKeys.cashEventRevisions(eventId ?? 'none'),
    queryFn: () => cashEventApi.revisions(eventId!),
    enabled: open && Boolean(eventId),
  });

  const detailQuery = useQuery({
    queryKey: queryKeys.cashEventDetail(eventId ?? 'none'),
    queryFn: () => cashEventApi.detail(eventId!),
    enabled: open && Boolean(eventId),
  });

  return (
    <Drawer
      title={`版本历史${detailQuery.data ? ` · ${detailQuery.data.title}` : ''}`}
      width={640}
      open={open}
      onClose={onClose}
      destroyOnHidden
    >
      {query.isLoading ? <Skeleton active paragraph={{ rows: 4 }} /> : null}

      {query.isError ? (
        <InlineNote tone="danger">{errorMessage(query.error)}</InlineNote>
      ) : null}

      {query.data && query.data.length === 0 ? (
        <Empty description="暂无版本记录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : null}

      {query.data ? (
        <div className="gew-stack">
          <InlineNote tone="info">
            系统不会静默覆盖历史数据。每一次影响金额计算的修改都会保存旧版本，
            并触发未来 7 天资金重新计算。
          </InlineNote>

          {detailQuery.data ? (
            <div>
              <Space wrap size={4}>
                <StatusTag tone="ok">当前版本 v{detailQuery.data.current_version}</StatusTag>
                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  事项编号 {detailQuery.data.cash_key}
                </span>
              </Space>
            </div>
          ) : null}

          {query.data.map((revision) => (
            <div key={revision.version} className="gew-card gew-card--flat">
              <div className="gew-card__head">
                <Space>
                  <span className="gew-card__title">v{revision.version}</span>
                  {revision.material ? (
                    <Tag color="red" bordered={false}>
                      影响金额计算
                    </Tag>
                  ) : (
                    <Tag bordered={false}>仅说明信息</Tag>
                  )}
                </Space>
                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  {formatDateTime(revision.changed_at)} · {revision.changed_by_name ?? '系统'}
                </span>
              </div>
              <div className="gew-card__body gew-card__body--tight">
                {revision.change_reason ? (
                  <div style={{ marginBottom: 8, color: 'var(--text-secondary)' }}>
                    原因：{revision.change_reason}
                  </div>
                ) : null}
                {revision.changes.length === 0 ? (
                  <span style={{ color: 'var(--text-muted)' }}>
                    {revision.before ? '无字段差异' : '创建事项'}
                  </span>
                ) : (
                  <Table<FieldChange>
                    rowKey="field"
                    size="small"
                    pagination={false}
                    dataSource={revision.changes}
                    columns={[
                      { title: '字段', dataIndex: 'label', width: 110 },
                      {
                        title: '修改前',
                        dataIndex: 'before_text',
                        render: (value: string | null, record) => (
                          <Tooltip title={String(record.before ?? '')}>
                            <span style={{ color: 'var(--text-secondary)' }}>{value ?? '—'}</span>
                          </Tooltip>
                        ),
                      },
                      {
                        title: '修改后',
                        dataIndex: 'after_text',
                        render: (value: string | null, record) => (
                          <Tooltip title={String(record.after ?? '')}>
                            <span style={{ fontWeight: 500 }}>{value ?? '—'}</span>
                          </Tooltip>
                        ),
                      },
                      {
                        title: '',
                        key: 'material',
                        width: 90,
                        render: (_value, record) =>
                          record.material ? (
                            <Tag color="red" bordered={false}>
                              影响计算
                            </Tag>
                          ) : null,
                      },
                    ]}
                  />
                )}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </Drawer>
  );
}
