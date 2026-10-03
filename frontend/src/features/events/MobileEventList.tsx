/**
 * 手机端的收付款事项列表（卡片式）。
 *
 * 宽表格（桌面 980px）在 360px 视口里只能靠左右拖动查看，一行看不全，
 * 是手机上最影响使用的部分。这里把每笔事项改成一张卡片：
 *
 * ```
 * 商户结算款                    ✓ 计划中
 * DEV-SETTLE-0001 · 结算款
 * 2026-10-04 16:15   收入   +¥2,200.00
 * 来源：手工录入        [改] [源] [版] [取消]
 * ```
 *
 * 信息量与表格列完全一致（时间/事项/编号/类型/方向/金额/状态/版本/来源），
 * 只是改为纵向排布，不再需要横向滚动。
 */

import { Button, Empty, Popconfirm, Space, Tag, Tooltip } from 'antd';
import {
  EditOutlined,
  FileSearchOutlined,
  HistoryOutlined,
  PlusOutlined,
  StopOutlined,
  UploadOutlined,
} from '@ant-design/icons';

import { StatusTag } from '@/components/ui';
import type { CashEvent, Direction } from '@/types';
import { DIRECTION_LABELS, EVENT_TYPE_LABELS, SOURCE_LABELS, STATE_LABELS } from '@/utils/labels';
import { formatCny, formatSigned } from '@/utils/money';
import { formatDateTime } from '@/utils/datetime';

export interface MobileEventListProps {
  items: CashEvent[];
  loading?: boolean;
  onEdit: (event: CashEvent) => void;
  onShowSource: (event: CashEvent) => void;
  onShowRevisions: (event: CashEvent) => void;
  onCancel: (event: CashEvent) => void;
  onCreate: () => void;
  onImport: () => void;
}

function stateTone(state: string): 'neutral' | 'info' | 'ok' {
  if (state === 'cancelled') return 'neutral';
  if (state === 'included_in_opening') return 'info';
  return 'ok';
}

export default function MobileEventList({
  items,
  loading = false,
  onEdit,
  onShowSource,
  onShowRevisions,
  onCancel,
  onCreate,
  onImport,
}: MobileEventListProps) {
  if (!loading && items.length === 0) {
    return (
      <Empty description="还没有收付款事项" image={Empty.PRESENTED_IMAGE_SIMPLE}>
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={onCreate}>
            新增事项
          </Button>
          <Button icon={<UploadOutlined />} onClick={onImport}>
            导入 CSV
          </Button>
        </Space>
      </Empty>
    );
  }

  return (
    <ul className="gew-event-cards" data-testid="event-card-list">
      {items.map((record) => {
        const direction: Direction = record.direction;
        return (
          <li className="gew-event-card" key={record.id}>
            <div className="gew-event-card__top">
              <span className="gew-event-card__title">{record.title}</span>
              <Space size={4}>
                <StatusTag tone={stateTone(record.state)}>
                  {STATE_LABELS[record.state as keyof typeof STATE_LABELS] ?? record.state}
                </StatusTag>
                {record.current_version > 1 ? (
                  <span className="gew-event-card__version">v{record.current_version}</span>
                ) : null}
              </Space>
            </div>

            <div className="gew-event-card__meta">
              {record.cash_key} · {EVENT_TYPE_LABELS[record.event_type] ?? record.event_type}
            </div>

            <div className="gew-event-card__amounts">
              <span className="num gew-event-card__time">{formatDateTime(record.scheduled_at)}</span>
              <Tag color={direction === 'inflow' ? 'green' : 'default'} bordered={false}>
                {DIRECTION_LABELS[direction]}
              </Tag>
              <span
                className={`num ${direction === 'inflow' ? 'gew-amount-inflow' : 'gew-amount-outflow'}`}
              >
                {direction === 'inflow'
                  ? formatSigned(record.amount_cents)
                  : `-${formatCny(record.amount_cents, false)}`}
              </span>
            </div>

            <div className="gew-event-card__foot">
              <span className="gew-event-card__source">
                来源：{SOURCE_LABELS[record.source_type] ?? record.source_type}
              </span>
              <Space size={0}>
                <Tooltip title="修改">
                  <Button
                    type="text"
                    size="small"
                    aria-label={`修改 ${record.title}`}
                    icon={<EditOutlined />}
                    onClick={() => onEdit(record)}
                  />
                </Tooltip>
                <Tooltip title="来源">
                  <Button
                    type="text"
                    size="small"
                    aria-label={`查看 ${record.title} 的来源`}
                    icon={<FileSearchOutlined />}
                    onClick={() => onShowSource(record)}
                  />
                </Tooltip>
                <Tooltip title="版本">
                  <Button
                    type="text"
                    size="small"
                    aria-label={`查看 ${record.title} 的版本`}
                    icon={<HistoryOutlined />}
                    onClick={() => onShowRevisions(record)}
                  />
                </Tooltip>
                {record.state !== 'cancelled' ? (
                  <Popconfirm
                    title="取消该事项？"
                    description="取消后不参与未来计算，但历史记录与来源会完整保留。"
                    okText="确认取消"
                    cancelText="返回"
                    onConfirm={() => onCancel(record)}
                  >
                    <Tooltip title="取消事项">
                      <Button
                        type="text"
                        size="small"
                        danger
                        aria-label={`取消 ${record.title}`}
                        icon={<StopOutlined />}
                      />
                    </Tooltip>
                  </Popconfirm>
                ) : null}
              </Space>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
