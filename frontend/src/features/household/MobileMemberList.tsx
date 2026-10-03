/**
 * 手机端的家庭成员卡片。
 *
 * 桌面端用表格展示成员（称呼 / 账户 / 关系 / 状态 / 加入时间 / 操作）；
 * 手机上改为纵向卡片，审核与移除按钮保持在卡片内可点。
 */

import { Button, Empty, Space } from 'antd';
import { UserAddOutlined } from '@ant-design/icons';

import { StatusTag } from '@/components/ui';
import type { HouseholdMember } from '@/types';
import { formatDateTime } from '@/utils/datetime';

export interface MobileMemberListProps {
  members: HouseholdMember[];
  onApprove: (membershipId: string) => void;
  onRemove: (member: HouseholdMember) => void;
  onInvite: () => void;
  approvePending?: boolean;
}

const STATUS_LABELS: Record<string, string> = {
  active: '已加入',
  pending: '待确认',
  rejected: '已拒绝',
  removed: '已移除',
};

const STATUS_TONE: Record<string, 'ok' | 'warning' | 'neutral'> = {
  active: 'ok',
  pending: 'warning',
  rejected: 'neutral',
  removed: 'neutral',
};

export default function MobileMemberList({
  members,
  onApprove,
  onRemove,
  onInvite,
  approvePending = false,
}: MobileMemberListProps) {
  if (members.length === 0) {
    return (
      <Empty description="还没有家庭成员加入" image={Empty.PRESENTED_IMAGE_SIMPLE}>
        <Button icon={<UserAddOutlined />} onClick={onInvite}>
          复制邀请码并邀请
        </Button>
      </Empty>
    );
  }

  return (
    <ul className="gew-member-cards" data-testid="member-card-list">
      {members.map((row) => (
        <li className="gew-member-card" key={row.membership_id}>
          <div>
            <div className="gew-member-card__name">{row.display_name}</div>
            <div className="gew-member-card__meta">
              <span className="num">{row.username}</span>
              {row.relation_label ? ` · ${row.relation_label}` : ''}
            </div>
            <div className="gew-member-card__meta">
              {row.joined_at ? `加入于 ${formatDateTime(row.joined_at)}` : '尚未加入'}
            </div>
            <div style={{ marginTop: 6 }}>
              <StatusTag tone={STATUS_TONE[row.status] ?? 'neutral'}>
                {STATUS_LABELS[row.status] ?? row.status}
              </StatusTag>
            </div>
          </div>
          <Space size={4} direction="vertical">
            {row.status === 'pending' ? (
              <Button
                size="small"
                type="primary"
                block
                loading={approvePending}
                onClick={() => onApprove(row.membership_id)}
              >
                通过申请
              </Button>
            ) : null}
            {row.status !== 'removed' ? (
              <Button size="small" danger block onClick={() => onRemove(row)}>
                移除
              </Button>
            ) : null}
          </Space>
        </li>
      ))}
    </ul>
  );
}
