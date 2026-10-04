/**
 * 分享预览抽屉。
 *
 * 商户点击「分享给家庭」后必须先进入分享预览：逐项选择要共享的字段，
 * 默认不勾选完整经营余额、全部交易明细等敏感内容。
 */

import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  App as AntdApp,
  Button,
  Checkbox,
  Drawer,
  Empty,
  InputNumber,
  Space,
  Spin,
  Typography,
} from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { householdApi, householdCardApi } from '@/api/household';
import { errorMessage } from '@/api/client';
import { queryKeys } from '@/api/queryClient';
import { useDrawerWidth } from '@/hooks/useResponsive';
import { InlineNote } from '@/components/ui';
import { buildCardPayloadItems } from '@/features/household/cardPayload';
import type { AnalysisResult, CardType } from '@/types';
import { formatCny } from '@/utils/money';
import { formatDateTime } from '@/utils/datetime';

const DEFAULT_FIELDS = ['max_withdrawable', 'limiting_point', 'risk_summary'];

export default function ShareCardDrawer({
  open,
  onClose,
  result,
  cardType = 'decision',
  cashEventId = null,
}: {
  open: boolean;
  onClose: () => void;
  result: AnalysisResult | null;
  cardType?: CardType;
  cashEventId?: string | null;
}) {
  const drawerWidth = useDrawerWidth(560);
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [fields, setFields] = useState<string[]>(DEFAULT_FIELDS);
  const [plannedAmount, setPlannedAmount] = useState<number | null>(null);

  useEffect(() => {
    if (open) {
      setFields(DEFAULT_FIELDS);
      setPlannedAmount(
        result?.max_withdrawable_cents ? result.max_withdrawable_cents / 100 : null,
      );
    }
  }, [open, result?.max_withdrawable_cents]);

  const householdQuery = useQuery({
    queryKey: queryKeys.household,
    queryFn: householdApi.current,
    enabled: open,
  });

  const previewQuery = useQuery({
    queryKey: ['household', 'preview', cardType, fields, result?.id, cashEventId],
    queryFn: () =>
      householdCardApi.preview({
        card_type: cardType,
        shared_fields: fields,
        analysis_result_id: result?.id ?? null,
        cash_event_id: cashEventId,
        planned_household_amount_cents:
          plannedAmount === null ? null : Math.round(plannedAmount * 100),
      }),
    enabled: open && fields.length > 0,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      householdCardApi.create({
        card_type: cardType,
        title: previewQuery.data?.title ?? '家庭决策卡',
        summary: previewQuery.data?.summary ?? null,
        shared_fields: fields,
        analysis_result_id: result?.id ?? null,
        cash_event_id: cashEventId,
        planned_household_amount_cents:
          plannedAmount === null ? null : Math.round(plannedAmount * 100),
      }),
    onSuccess: () => {
      message.success('已分享给家庭成员');
      queryClient.invalidateQueries({ queryKey: queryKeys.household });
      queryClient.invalidateQueries({ queryKey: ['household', 'cards'] });
      onClose();
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const members = householdQuery.data?.members.filter((item) => item.status === 'active') ?? [];

  const previewItems = useMemo(
    () => buildCardPayloadItems(previewQuery.data?.payload ?? {}),
    [previewQuery.data],
  );

  return (
    <Drawer
      title="分享预览"
      width={drawerWidth}
      open={open}
      onClose={onClose}
      destroyOnHidden
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button
            type="primary"
            disabled={!householdQuery.data || members.length === 0 || fields.length === 0}
            loading={createMutation.isPending}
            onClick={() => createMutation.mutate()}
          >
            确认分享
          </Button>
        </Space>
      }
    >
      {householdQuery.isLoading ? <Spin /> : null}

      {!householdQuery.isLoading && !householdQuery.data ? (
        <Empty description="尚未创建家庭">
          <Button type="primary" onClick={onClose}>
            先到「家庭协同」创建家庭
          </Button>
        </Empty>
      ) : null}

      {householdQuery.data ? (
        <div className="gew-stack">
          {members.length === 0 ? (
            <Alert
              type="warning"
              showIcon
              message="还没有已加入的家庭成员"
              description="请先邀请家庭成员并使用邀请码加入，通过审核后才能分享。"
            />
          ) : (
            <div className="gew-desc-item">
              <div className="gew-desc-item__label">分享对象（已加入的家庭成员）</div>
              <div className="gew-desc-item__value">
                {members.map((item) => item.display_name).join('、')}
              </div>
            </div>
          )}

          <div>
            <Typography.Text strong>选择要共享的内容</Typography.Text>
            <div style={{ marginTop: 8 }}>
              <Checkbox.Group
                value={fields}
                onChange={(values) => setFields(values as string[])}
                style={{ display: 'grid', gap: 8 }}
              >
                {/* 与后端 household_service.SHAREABLE_FIELDS 保持一致：
                    这里少一项，用户就永远勾不到那一项。 */}
                <Checkbox value="max_withdrawable">今日可提用金额</Checkbox>
                <Checkbox value="limiting_point">最紧张时间</Checkbox>
                <Checkbox value="limiting_balance">最紧时点余额</Checkbox>
                <Checkbox value="end_balance">期末余额</Checkbox>
                <Checkbox value="key_payments">关键经营付款</Checkbox>
                <Checkbox value="risk_summary">风险摘要</Checkbox>
                <Checkbox value="payment_gap">付款缺口</Checkbox>
                <Checkbox value="buffer_gap">留底缺口</Checkbox>
                <Checkbox value="pending_inflows">尚未到账的收入</Checkbox>
              </Checkbox.Group>
            </div>
            <div style={{ marginTop: 8 }}>
              <InlineNote>
                以下内容默认不分享：经营账户完整余额、全部交易明细、完整导入文件、经营咨询记录。
              </InlineNote>
            </div>
          </div>

          <div>
            <Typography.Text strong>计划家庭提用金额（元）</Typography.Text>
            <div style={{ marginTop: 8, maxWidth: 220 }}>
              <InputNumber
                min={0}
                precision={2}
                value={plannedAmount}
                onChange={(value) => setPlannedAmount(value ?? null)}
                style={{ width: '100%' }}
                inputMode="decimal"
              />
            </div>
            <div style={{ marginTop: 6, fontSize: 12, color: 'var(--text-muted)' }}>
              系统今日可提用上限 {formatCny(result?.max_withdrawable_cents ?? null)}
            </div>
          </div>

          <div>
            <Typography.Text strong>分享内容预览</Typography.Text>
            {previewQuery.isLoading ? (
              <Spin style={{ marginTop: 12 }} />
            ) : (
              <div className="gew-kv-list" style={{ marginTop: 8 }}>
                {previewItems.map((item) => (
                  <div className="gew-kv-list__row" key={item.key}>
                    <span className="gew-kv-list__key">{item.label}</span>
                    <span className="gew-kv-list__value">{item.value}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {result?.limiting_timestamp ? (
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              最紧张时间：{formatDateTime(result.limiting_timestamp)}
            </div>
          ) : null}
        </div>
      ) : null}
    </Drawer>
  );
}
