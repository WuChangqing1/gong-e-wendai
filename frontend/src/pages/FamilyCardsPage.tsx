/** 家庭成员视角：查看分享给我的协同卡片。 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  App as AntdApp,
  Button,
  Col,
  Empty,
  Input,
  Row,
  Skeleton,
  Space,
  Tag,
  Typography,
} from 'antd';
import {
  CheckOutlined,
  CommentOutlined,
  MessageOutlined,
  ReloadOutlined,
} from '@ant-design/icons';

import { householdApi, householdCardApi } from '@/api/household';
import { errorMessage } from '@/api/client';
import { queryKeys } from '@/api/queryClient';
import { DescriptionGrid, InlineNote, PageHeader, SectionCard, StatusTag } from '@/components/ui';
import { buildCardPayloadItems } from '@/features/household/cardPayload';
import type { CardType, HouseholdCard, ReactionType } from '@/types';
import { formatRelative } from '@/utils/datetime';
import { CARD_TYPE_LABELS, REACTION_LABELS, SHARE_FIELD_LABELS } from '@/utils/labels';

const TABS: { label: string; value: CardType | 'all' }[] = [
  { label: '全部', value: 'all' },
  { label: '家庭决策卡', value: 'decision' },
  { label: '风险提醒卡', value: 'risk' },
  { label: '更正通知卡', value: 'revision' },
];

function CardBody({ card }: { card: HouseholdCard }) {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [comment, setComment] = useState('');

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['household', 'cards'] });
  };

  const reactMutation = useMutation({
    mutationFn: (reaction: ReactionType) => householdCardApi.react(card.id, reaction),
    onSuccess: () => {
      message.success('已记录你的反馈');
      invalidate();
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const readMutation = useMutation({
    mutationFn: () => householdCardApi.markRead(card.id),
    onSuccess: invalidate,
    onError: (error) => message.error(errorMessage(error)),
  });

  const commentMutation = useMutation({
    mutationFn: () => householdCardApi.comment(card.id, comment.trim()),
    onSuccess: () => {
      setComment('');
      message.success('评论已提交');
      invalidate();
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  // 接收端与分享预览共用同一个渲染模型（详见 features/household/cardPayload），
  // 保证「经营者勾了什么」与「家人看到什么」逐项一致。
  const payload: Record<string, unknown> = { ...(card.payload ?? {}) };
  if (
    card.shared_fields.includes('max_withdrawable') &&
    payload.max_withdrawable_cents === undefined &&
    card.system_max_withdrawable_cents !== null
  ) {
    payload.max_withdrawable_cents = card.system_max_withdrawable_cents;
  }
  if (
    card.shared_fields.includes('planned_amount') &&
    payload.planned_household_amount_cents === undefined &&
    card.planned_household_amount_cents !== null
  ) {
    payload.planned_household_amount_cents = card.planned_household_amount_cents;
  }
  const items = buildCardPayloadItems(payload);

  return (
    <div className="gew-stack">
      <Space wrap>
        <StatusTag tone={card.card_type === 'risk' ? 'warning' : 'info'}>
          {CARD_TYPE_LABELS[card.card_type] ?? card.card_type}
        </StatusTag>
        {!card.is_read ? <Tag color="red" bordered={false}>未读</Tag> : null}
        {card.my_reaction ? (
          <Tag bordered={false}>{REACTION_LABELS[card.my_reaction]}</Tag>
        ) : null}
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          {formatRelative(card.created_at)}分享
        </span>
      </Space>

      {items.length > 0 ? (
        <DescriptionGrid items={items} />
      ) : (
        <Typography.Text type="secondary">这张卡片没有共享任何金额字段。</Typography.Text>
      )}

      <div>
        <Space wrap>
          <Button
            icon={<CheckOutlined />}
            type={card.my_reaction === 'agree' ? 'primary' : 'default'}
            onClick={() => reactMutation.mutate('agree')}
            loading={reactMutation.isPending}
          >
            同意
          </Button>
          <Button
            icon={<MessageOutlined />}
            type={card.my_reaction === 'discuss' ? 'primary' : 'default'}
            onClick={() => reactMutation.mutate('discuss')}
            loading={reactMutation.isPending}
          >
            需要商量
          </Button>
          {!card.is_read ? (
            <Button type="link" onClick={() => readMutation.mutate()} loading={readMutation.isPending}>
              标记已读
            </Button>
          ) : null}
        </Space>
      </div>

      <div>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 6 }}>
          针对这张卡片的评论
        </div>
        {card.comments.length === 0 ? (
          <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>还没有评论</div>
        ) : (
          <div className="gew-kv-list">
            {card.comments.map((item) => (
              <div className="gew-kv-list__row" key={item.id}>
                <span className="gew-kv-list__key">
                  {item.display_name} · {formatRelative(item.created_at)}
                </span>
                <span className="gew-kv-list__value">{item.content}</span>
              </div>
            ))}
          </div>
        )}
        <Space.Compact style={{ width: '100%', marginTop: 8 }}>
          <Input
            placeholder="写下你的意见（仅针对这张卡片）"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            maxLength={500}
            onPressEnter={() => comment.trim() && commentMutation.mutate()}
          />
          <Button
            type="primary"
            icon={<CommentOutlined />}
            disabled={!comment.trim()}
            loading={commentMutation.isPending}
            onClick={() => commentMutation.mutate()}
          >
            评论
          </Button>
        </Space.Compact>
      </div>

      <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
        包含字段：
        {card.shared_fields.map((field) => SHARE_FIELD_LABELS[field] ?? '其他信息').join('、') || '—'}
      </div>
    </div>
  );
}

export default function FamilyCardsPage() {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<CardType | 'all'>('all');
  const [joinCode, setJoinCode] = useState('');

  const membershipQuery = useQuery({
    queryKey: [...queryKeys.household, 'mine'],
    queryFn: householdApi.myMemberships,
  });

  const cardsQuery = useQuery({
    queryKey: queryKeys.householdCards({ card_type: tab === 'all' ? undefined : tab }),
    queryFn: () => householdCardApi.list(tab === 'all' ? {} : { card_type: tab }),
  });

  const joinMutation = useMutation({
    mutationFn: () => householdApi.join({ invite_code: joinCode.trim().toUpperCase() }),
    onSuccess: (data) => {
      message.success(`已提交加入「${data.household_name}」的申请，等待经营者确认`);
      setJoinCode('');
      queryClient.invalidateQueries({ queryKey: [...queryKeys.household, 'mine'] });
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const memberships = membershipQuery.data ?? [];
  const activeMembership = memberships.find((item) => item.status === 'active');
  const pendingMembership = memberships.find((item) => item.status === 'pending');
  const cards = cardsQuery.data ?? [];

  return (
    <div className="gew-stack">
      <PageHeader
        title="家庭协同"
        subtitle="这里只显示经营者明确分享给你的协同卡片。经营流水、账户余额与未分享的内容不会展示。"
        extra={
          <Button
            icon={<ReloadOutlined />}
            loading={cardsQuery.isFetching}
            onClick={() => cardsQuery.refetch()}
          >
            刷新
          </Button>
        }
      />

      {!activeMembership && !pendingMembership ? (
        <SectionCard title="加入家庭">
          <Typography.Paragraph type="secondary">
            向经营者索取家庭邀请码，输入后提交加入申请。经营者确认后，你就可以看到分享给你的决策卡片。
          </Typography.Paragraph>
          <Space.Compact style={{ maxWidth: 360 }}>
            <Input
              placeholder="输入邀请码"
              value={joinCode}
              onChange={(event) => setJoinCode(event.target.value)}
              maxLength={16}
            />
            <Button
              type="primary"
              disabled={joinCode.trim().length < 4}
              loading={joinMutation.isPending}
              onClick={() => joinMutation.mutate()}
            >
              申请加入
            </Button>
          </Space.Compact>
        </SectionCard>
      ) : null}

      {pendingMembership ? (
        <InlineNote tone="warning">
          你已提交加入「{pendingMembership.household_name}」的申请，等待经营者确认。
        </InlineNote>
      ) : null}

      {activeMembership ? (
        <SectionCard title={`已加入：${activeMembership.household_name}`}>
          <Space wrap>
            {TABS.map((item) => (
              <Button
                key={item.value}
                type={tab === item.value ? 'primary' : 'default'}
                size="small"
                onClick={() => setTab(item.value)}
              >
                {item.label}
              </Button>
            ))}
          </Space>
        </SectionCard>
      ) : null}

      {cardsQuery.isLoading ? <Skeleton active paragraph={{ rows: 4 }} /> : null}

      {!cardsQuery.isLoading && cards.length === 0 ? (
        <SectionCard>
          <Empty
            description={activeMembership ? '还没有分享给你的卡片' : '加入家庭后即可查看协同卡片'}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          >
            <Typography.Text type="secondary">
              经营者分享决策卡或风险提醒卡后，你会在这里看到具体金额、最紧张时间与需要共同确认的事项。
            </Typography.Text>
          </Empty>
        </SectionCard>
      ) : null}

      <Row gutter={[16, 16]}>
        {cards.map((card) => (
          <Col xs={24} lg={12} key={card.id}>
            <SectionCard title={card.title} flat>
              <CardBody card={card} />
            </SectionCard>
          </Col>
        ))}
      </Row>
    </div>
  );
}
