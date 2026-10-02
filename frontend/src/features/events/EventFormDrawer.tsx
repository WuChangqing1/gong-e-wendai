/**
 * 新增 / 修改收付款事项。
 *
 * 提交前展示确认摘要；金额与时间错误会阻止提交。
 * 修改金额、时间、方向或状态会生成新版本（由后端保证），页面提示影响。
 */

import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  App as AntdApp,
  Button,
  DatePicker,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Select,
  Space,
} from 'antd';
import { useMutation } from '@tanstack/react-query';
import dayjs, { type Dayjs } from 'dayjs';

import { cashEventApi, type CashEventPayload } from '@/api/cashflow';
import { errorMessage } from '@/api/client';
import { useDrawerWidth } from '@/hooks/useResponsive';
import { DescriptionGrid, InlineNote } from '@/components/ui';
import type { CashEvent } from '@/types';
import { formatCny, yuanToCents } from '@/utils/money';
import { formatDateTime } from '@/utils/datetime';
import { DIRECTION_LABELS, EVENT_TYPE_LABELS, SOURCE_LABELS, STATE_HINTS, STATE_LABELS } from '@/utils/labels';

interface FormValues {
  title: string;
  direction: 'inflow' | 'outflow';
  amount: number;
  scheduled_at: Dayjs;
  state: 'scheduled' | 'included_in_opening' | 'cancelled';
  event_type: string;
  cash_key?: string;
  source_label?: string;
  note?: string;
  sequence_index_optional?: number;
}

const MATERIAL_HINT =
  '金额、预计时间、收支方向与状态的修改都会保存旧版本，并触发未来 7 天资金重新计算。';

export default function EventFormDrawer({
  open,
  event,
  onClose,
  onSaved,
  onRequestSmartInput: _onRequestSmartInput,
}: {
  open: boolean;
  event: CashEvent | null;
  onClose: () => void;
  onSaved: () => void;
  onRequestSmartInput?: () => void;
}) {
  const { message } = AntdApp.useApp();
  const drawerWidth = useDrawerWidth(560);
  const [form] = Form.useForm<FormValues>();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pending, setPending] = useState<FormValues | null>(null);
  const isEdit = Boolean(event);

  useEffect(() => {
    if (!open) return;
    if (event) {
      form.setFieldsValue({
        title: event.title,
        direction: event.direction,
        amount: event.amount_cents / 100,
        scheduled_at: dayjs(event.scheduled_at),
        state: event.state,
        event_type: event.event_type,
        cash_key: event.cash_key,
        source_label: event.source_label ?? undefined,
        note: event.note ?? undefined,
        sequence_index_optional: event.sequence_index_optional ?? undefined,
      });
    } else {
      form.resetFields();
      form.setFieldsValue({
        direction: 'outflow',
        state: 'scheduled',
        event_type: 'supplier_payment',
        scheduled_at: dayjs().add(1, 'day').hour(10).minute(0).second(0),
      });
    }
  }, [open, event, form]);

  const mutation = useMutation({
    mutationFn: async (values: FormValues) => {
      const amountCents = yuanToCents(values.amount);
      if (amountCents === null || amountCents < 0) {
        throw new Error('金额格式不正确');
      }
      const payload: CashEventPayload = {
        title: values.title.trim(),
        direction: values.direction,
        amount_cents: amountCents,
        scheduled_at: values.scheduled_at.toISOString(),
        state: values.state,
        event_type: values.event_type,
        cash_key: values.cash_key?.trim() || null,
        source_label: values.source_label?.trim() || null,
        note: values.note?.trim() || null,
        sequence_index_optional: values.sequence_index_optional ?? null,
      };
      if (event) {
        return cashEventApi.update(event.id, {
          ...payload,
          change_reason: '商户修改事项',
        });
      }
      return cashEventApi.create(payload);
    },
    onSuccess: (saved) => {
      setConfirmOpen(false);
      setPending(null);
      message.success(
        isEdit
          ? `已保存，当前版本 v${saved.current_version}${saved.current_version > 1 ? '，已触发重新计算' : ''}`
          : '事项已创建',
      );
      onSaved();
      onClose();
    },
    onError: (error) => {
      setConfirmOpen(false);
      message.error(errorMessage(error));
    },
  });

  const materialFields = useMemo(() => {
    if (!event || !pending) return [];
    const changes: string[] = [];
    if (yuanToCents(pending.amount) !== event.amount_cents) changes.push('金额');
    if (pending.scheduled_at.toISOString() !== dayjs(event.scheduled_at).toISOString())
      changes.push('预计时间');
    if (pending.direction !== event.direction) changes.push('收支方向');
    if (pending.state !== event.state) changes.push('状态');
    return changes;
  }, [event, pending]);

  const submit = async () => {
    let values: FormValues;
    try {
      // 必须先用校验结果驱动，确认通过后才展示确认摘要
      values = await form.validateFields();
    } catch {
      message.warning('请先修正表单中的问题');
      return;
    }
    setPending(values);
    setConfirmOpen(true);
  };

  const stateValue = Form.useWatch('state', form) ?? 'scheduled';

  return (
    <>
      <Drawer
        title={isEdit ? `修改事项 · ${event?.cash_key}` : '新增收付款事项'}
        width={drawerWidth}
        open={open}
        onClose={onClose}
        destroyOnHidden
        extra={
          <Space>
            <Button onClick={onClose}>取消</Button>
            <Button type="primary" onClick={submit}>
              {isEdit ? '保存修改' : '创建事项'}
            </Button>
          </Space>
        }
      >
        <Form<FormValues> form={form} layout="vertical" requiredMark={false}>
          <Form.Item
            name="title"
            label="事项名称"
            rules={[{ required: true, message: '请输入事项名称' }, { max: 128 }]}
          >
            <Input placeholder="例如：供应商货款、商户结算款" size="large" />
          </Form.Item>

          <Form.Item name="direction" label="收支方向" rules={[{ required: true }]}>
            <Radio.Group optionType="button" buttonStyle="solid">
              <Radio.Button value="inflow">收入</Radio.Button>
              <Radio.Button value="outflow">支出</Radio.Button>
            </Radio.Group>
          </Form.Item>

          <Form.Item
            name="amount"
            label="金额（元）"
            rules={[
              {
                validator: (_: unknown, value: unknown) => {
                  if (value === null || value === undefined || value === '') {
                    return Promise.reject(new Error('请输入金额'));
                  }
                  const cents = yuanToCents(value as number);
                  if (cents === null) return Promise.reject(new Error('金额格式不正确'));
                  if (cents < 0) return Promise.reject(new Error('金额不能为负数'));
                  if (cents === 0) return Promise.reject(new Error('金额必须大于 0'));
                  return Promise.resolve();
                },
              },
            ]}
          >
            <InputNumber
              min={0}
              precision={2}
              step={100}
              controls={false}
              style={{ width: '100%' }}
              size="large"
              placeholder="0.00"
              addonBefore="¥"
              // 金额一律为整数分，这里阻止负号与非法字符进入输入
              onKeyDown={(event) => {
                if (['-', 'e', 'E', '+'].includes(event.key)) event.preventDefault();
              }}
            />
          </Form.Item>

          <Form.Item
            name="scheduled_at"
            label="预计时间"
            rules={[{ required: true, message: '请选择预计时间' }]}
          >
            <DatePicker
              showTime={{ format: 'HH:mm' }}
              format="YYYY-MM-DD HH:mm"
              style={{ width: '100%' }}
              size="large"
            />
          </Form.Item>

          <Form.Item name="state" label="状态" rules={[{ required: true }]}>
            <Select
              size="large"
              options={Object.entries(STATE_LABELS).map(([value, label]) => ({
                value,
                label,
              }))}
            />
          </Form.Item>
          <div style={{ marginTop: -12, marginBottom: 16 }}>
            <InlineNote tone="neutral">{STATE_HINTS[stateValue]}</InlineNote>
          </div>

          <Form.Item name="event_type" label="事项类型">
            <Select
              size="large"
              showSearch
              optionFilterProp="label"
              options={Object.entries(EVENT_TYPE_LABELS).map(([value, label]) => ({ value, label }))}
            />
          </Form.Item>

          <Form.Item
            name="cash_key"
            label="事项编号（选填）"
            extra={
              isEdit
                ? '事项编号用于避免重复录入，同一商户范围内不允许重复。'
                : '留空则自动生成。如果你从其他系统带入编号，请填写以便去重。'
            }
          >
            <Input placeholder="例如：SETTLE-20251003-001" disabled={isEdit} />
          </Form.Item>

          <Form.Item name="source_label" label="来源说明">
            <Input placeholder="例如：结算通知 8821、采购合同 HT-2025-018" />
          </Form.Item>

          <Form.Item name="note" label="备注">
            <Input.TextArea rows={3} maxLength={2000} showCount placeholder="补充说明" />
          </Form.Item>

          <Form.Item
            name="sequence_index_optional"
            label="同一时刻的处理次序（选填）"
            extra="同一时间存在多笔事项且没有指定次序时，系统先处理支出再处理收入，以暴露中途可能出现的资金缺口。"
          >
            <InputNumber min={0} style={{ width: '100%' }} placeholder="数字越小越先处理" />
          </Form.Item>

          {isEdit ? <Alert type="info" showIcon message={MATERIAL_HINT} /> : null}
        </Form>
      </Drawer>

      <Modal
        title={isEdit ? '确认修改内容' : '确认新增事项'}
        open={confirmOpen}
        onCancel={() => setConfirmOpen(false)}
        onOk={() => pending && mutation.mutate(pending)}
        confirmLoading={mutation.isPending}
        okText={isEdit ? '确认修改' : '确认创建'}
        cancelText="返回检查"
        width={520}
        destroyOnHidden
      >
        {pending ? (
          <div className="gew-stack">
            <DescriptionGrid
              items={[
                { label: '事项名称', value: pending.title },
                {
                  label: '收支方向',
                  value: DIRECTION_LABELS[pending.direction],
                },
                {
                  label: '金额',
                  value: (
                    <span className="num">{formatCny(yuanToCents(pending.amount) ?? 0)}</span>
                  ),
                },
                { label: '预计时间', value: formatDateTime(pending.scheduled_at.toISOString()) },
                { label: '状态', value: STATE_LABELS[pending.state] },
                {
                  label: '事项类型',
                  value: EVENT_TYPE_LABELS[pending.event_type] ?? pending.event_type,
                },
                {
                  label: '来源',
                  value: pending.source_label || SOURCE_LABELS.manual,
                },
              ]}
            />
            {materialFields.length > 0 ? (
              <InlineNote tone="warning">
                本次修改涉及：{materialFields.join('、')}。保存后会保留旧版本（当前 v
                {event?.current_version}），并要求重新计算未来 7 天资金。
              </InlineNote>
            ) : (
              <InlineNote>本次修改不影响金额计算，仅更新说明信息。</InlineNote>
            )}
          </div>
        ) : null}
      </Modal>
    </>
  );
}
