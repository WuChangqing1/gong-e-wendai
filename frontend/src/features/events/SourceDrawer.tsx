/** 来源抽屉：结果 -> 限制事件 -> CashEvent -> SourceRecord 完整可追踪。 */

import { useQuery } from '@tanstack/react-query';
import { Button, Drawer, Empty, Skeleton, Space } from 'antd';
import { EditOutlined } from '@ant-design/icons';

import { cashEventApi } from '@/api/cashflow';
import { errorMessage } from '@/api/client';
import { queryKeys } from '@/api/queryClient';
import { useDrawerWidth } from '@/hooks/useResponsive';
import { DescriptionGrid, InlineNote, StatusTag } from '@/components/ui';
import type { CashEvent } from '@/types';
import { formatCny } from '@/utils/money';
import { formatDateTime } from '@/utils/datetime';
import {
  DIRECTION_LABELS,
  EVENT_TYPE_LABELS,
  SOURCE_LABELS,
  STATE_HINTS,
  STATE_LABELS,
} from '@/utils/labels';

export default function SourceDrawer({
  eventId,
  open,
  onClose,
  onEdit,
}: {
  eventId: string | null;
  open: boolean;
  onClose: () => void;
  onEdit: (event: CashEvent) => void;
}) {
  const drawerWidth = useDrawerWidth(600);
  const query = useQuery({
    queryKey: [...queryKeys.cashEventDetail(eventId ?? 'none'), 'source'],
    queryFn: () => cashEventApi.source(eventId!),
    enabled: open && Boolean(eventId),
  });

  const data = query.data;

  return (
    <Drawer
      title="来源与追踪"
      width={drawerWidth}
      open={open}
      onClose={onClose}
      destroyOnHidden
      extra={
        data ? (
          <Button
            icon={<EditOutlined />}
            onClick={() => {
              onEdit(data);
              onClose();
            }}
          >
            修改事项
          </Button>
        ) : null
      }
    >
      {query.isLoading ? <Skeleton active paragraph={{ rows: 5 }} /> : null}

      {query.isError ? <InlineNote tone="danger">{errorMessage(query.error)}</InlineNote> : null}

      {data ? (
        <div className="gew-stack">
          <div>
            <Space wrap>
              <StatusTag
                tone={
                  data.state === 'cancelled'
                    ? 'neutral'
                    : data.state === 'included_in_opening'
                      ? 'info'
                      : 'ok'
                }
              >
                {STATE_LABELS[data.state]}
              </StatusTag>
              <StatusTag tone="neutral">v{data.current_version}</StatusTag>
            </Space>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 6 }}>
              {STATE_HINTS[data.state]}
            </div>
          </div>

          <DescriptionGrid
            items={[
              { label: '事项名称', value: data.title },
              { label: '事项编号', value: <span className="num">{data.cash_key}</span> },
              { label: '收支方向', value: DIRECTION_LABELS[data.direction] },
              {
                label: '金额',
                value: <span className="num">{formatCny(data.amount_cents)}</span>,
              },
              { label: '预计时间', value: formatDateTime(data.scheduled_at) },
              {
                label: '事项类型',
                value: EVENT_TYPE_LABELS[data.event_type] ?? data.event_type,
              },
              { label: '来源类型', value: SOURCE_LABELS[data.source_type] ?? data.source_type },
              { label: '来源说明', value: data.source_label ?? '—' },
            ]}
          />

          {data.note ? (
            <div>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>备注</div>
              <div>{data.note}</div>
            </div>
          ) : null}

          <div>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>来源记录</div>
            {data.source_record ? (
              <div className="gew-stack">
                <DescriptionGrid
                  items={[
                    { label: '来源类型', value: SOURCE_LABELS[data.source_record.source_type] },
                    { label: '来源文件', value: data.source_record.file_name ?? '—' },
                    {
                      label: '原始行号',
                      value: data.source_record.row_number ?? '—',
                    },
                    { label: '创建时间', value: formatDateTime(data.source_record.created_at) },
                    {
                      label: '导入批次',
                      value: data.source_record.import_batch_id ?? '—',
                    },
                  ]}
                />
                <div>
                  <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
                    原始内容
                  </div>
                  <pre className="gew-pre">{data.source_record.raw_content ?? '无'}</pre>
                </div>
                {data.source_record.content_hash ? (
                  <div>
                    <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
                      内容指纹（用于识别重复上传）
                    </div>
                    <div className="gew-mono" style={{ fontSize: 12, wordBreak: 'break-all' }}>
                      {data.source_record.content_hash}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : (
              <Empty description="没有关联的来源记录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </div>

          <InlineNote tone="info">
            追踪链路：资金结论 → 限制时点事项 → 收付款事项 → 来源记录。
            任何一笔影响结论的金额都可以按这条链路回溯到最初的录入内容。
          </InlineNote>
        </div>
      ) : null}
    </Drawer>
  );
}
