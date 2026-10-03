/**
 * 手机端的咨询事项卡片列表。
 *
 * 替代桌面端 800px 宽表格：一行看不全、需要横向拖动，
 * 改成卡片后「事项编号 / 类型 / 问题摘要 / 状态 / 提交时间 / 详情入口」
 * 全部在一屏内可见。
 */

import { Button, Empty, Space } from 'antd';
import { PlusOutlined } from '@ant-design/icons';

import { StatusTag } from '@/components/ui';
import type { ConsultationCase, ConsultationStatus } from '@/types';
import {
  CONSULTATION_STATUS_LABELS,
  CONSULTATION_STATUS_TONE,
  QUESTION_TYPE_LABELS,
} from '@/utils/labels';
import { formatDateTime } from '@/utils/datetime';

const QUESTION_PREVIEW_LIMIT = 60;

export interface MobileConsultationListProps {
  items: ConsultationCase[];
  loading?: boolean;
  onOpen: (item: ConsultationCase) => void;
  onCreate: () => void;
  emptyDescription?: string;
  emptyActionLabel?: string;
}

export default function MobileConsultationList({
  items,
  loading = false,
  onOpen,
  onCreate,
  emptyDescription = '还没有咨询记录',
  emptyActionLabel = '发起第一条咨询',
}: MobileConsultationListProps) {
  if (!loading && items.length === 0) {
    return (
      <Empty description={emptyDescription} image={Empty.PRESENTED_IMAGE_SIMPLE}>
        <Button type="primary" icon={<PlusOutlined />} onClick={onCreate}>
          {emptyActionLabel}
        </Button>
      </Empty>
    );
  }

  return (
    <ul className="gew-consult-cards" data-testid="consultation-card-list">
      {items.map((row) => {
        const status = row.status as ConsultationStatus;
        const question = row.question ?? '';
        return (
          <li className="gew-consult-card" key={row.id}>
            <div className="gew-consult-card__top">
              <span className="num gew-consult-card__no">{row.case_no}</span>
              <StatusTag tone={CONSULTATION_STATUS_TONE[status]}>
                {CONSULTATION_STATUS_LABELS[status]}
              </StatusTag>
            </div>

            <div className="gew-consult-card__type">
              {QUESTION_TYPE_LABELS[row.question_type] ?? row.question_type}
            </div>

            {question ? (
              <p className="gew-consult-card__question">
                {question.length > QUESTION_PREVIEW_LIMIT
                  ? `${question.slice(0, QUESTION_PREVIEW_LIMIT)}…`
                  : question}
              </p>
            ) : null}

            <div className="gew-consult-card__foot">
              <span className="gew-consult-card__time">
                {row.submitted_at ? `提交于 ${formatDateTime(row.submitted_at)}` : '尚未提交'}
              </span>
              <Space size={0}>
                <Button size="small" type="link" onClick={() => onOpen(row)}>
                  查看详情
                </Button>
              </Space>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
