/**
 * 智能录入：把自然语言整理成结构化事项。
 *
 * 边界：
 * * 智能服务只做理解与提取，金额与时间必须由用户确认
 * * 提取结果不会直接入库，必须进入「已为你整理，请确认」页面
 * * 服务不可用时，提示用户手动录入，核心功能不受影响
 */

import { useState } from 'react';
import {
  Alert,
  App as AntdApp,
  Button,
  Drawer,
  Input,
  Space,
  Spin,
  Typography,
} from 'antd';
import { RobotOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';

import { aiApi, type AiExtractedEvent } from '@/api/ai';
import { cashEventApi } from '@/api/cashflow';
import { AI_FALLBACK_MESSAGE, errorMessage } from '@/api/client';
import EventFormDrawer from '@/features/events/EventFormDrawer';
import { useDrawerWidth } from '@/hooks/useResponsive';
import { DescriptionGrid, InlineNote, StatusTag } from '@/components/ui';
import { formatCny } from '@/utils/money';
import { formatDateTime } from '@/utils/datetime';
import { DIRECTION_LABELS, STATE_LABELS } from '@/utils/labels';

const EXAMPLE = '您尾号8821的商户结算款2358.60元预计10月3日完成结算。';

export default function SmartInputDrawer({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const drawerWidth = useDrawerWidth(560);
  const { message } = AntdApp.useApp();
  const [text, setText] = useState('');
  const [extracted, setExtracted] = useState<AiExtractedEvent | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [manualOpen, setManualOpen] = useState(false);

  const extractMutation = useMutation({
    mutationFn: () => aiApi.extract({ text }),
    onSuccess: (data) => {
      setExtracted(data.event);
      setFailed(null);
    },
    onError: (error) => {
      const text2 = errorMessage(error) || AI_FALLBACK_MESSAGE;
      setFailed(text2);
      setExtracted(null);
      message.warning(text2);
    },
  });

  const confirmMutation = useMutation({
    mutationFn: () => {
      if (!extracted) throw new Error('没有可确认的内容');
      return cashEventApi.create({
        title: extracted.title,
        direction: extracted.direction,
        amount_cents: extracted.amount_cents,
        scheduled_at: extracted.scheduled_at,
        state: extracted.state,
        source_label: extracted.source_label ?? '智能录入',
        note: `原始内容：${text}`,
      });
    },
    onSuccess: () => {
      message.success('事项已创建，来源标记为智能录入');
      onSaved();
      reset();
      onClose();
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const reset = () => {
    setText('');
    setExtracted(null);
    setFailed(null);
  };

  const lowConfidence = extracted
    ? Object.values(extracted.confidence ?? {}).some((value) => value < 0.6)
    : false;

  return (
    <>
      <Drawer
        title={
          <Space size={6}>
            <RobotOutlined aria-hidden="true" />
            <span>智能录入</span>
          </Space>
        }
        width={drawerWidth}
        open={open}
        onClose={() => {
          reset();
          onClose();
        }}
        destroyOnHidden
      >
        <div className="gew-stack">
          <Typography.Paragraph style={{ color: 'var(--text-secondary)', marginBottom: 0 }}>
            把结算通知、到账提醒、付款约定的原文粘贴进来，系统会整理成一条收付款事项。
            整理结果需要你确认后才会写入。
          </Typography.Paragraph>

          <Input.TextArea
            rows={4}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={EXAMPLE}
            maxLength={2000}
            showCount
          />

          <Space wrap>
            <Button
              type="primary"
              onClick={() => extractMutation.mutate()}
              loading={extractMutation.isPending}
              disabled={text.trim().length < 4}
            >
              整理成收付款事项
            </Button>
            <Button onClick={() => setText(EXAMPLE)}>使用示例文本</Button>
            <Button type="link" onClick={() => setManualOpen(true)}>
              改为手动录入
            </Button>
          </Space>

          {extractMutation.isPending ? <Spin tip="正在整理…" /> : null}

          {failed ? (
            <Alert
              type="warning"
              showIcon
              message={AI_FALLBACK_MESSAGE}
              description="你可以直接使用手动录入完成这条事项，所有计算功能不受影响。"
              action={
                <Button size="small" onClick={() => setManualOpen(true)}>
                  手动录入
                </Button>
              }
            />
          ) : null}

          {extracted ? (
            <div className="gew-card gew-card--flat">
              <div className="gew-card__head">
                <span className="gew-card__title">已为你整理，请确认</span>
                <StatusTag tone={lowConfidence ? 'warning' : 'ok'}>
                  {lowConfidence ? '有不确定内容' : '识别完成'}
                </StatusTag>
              </div>
              <div className="gew-card__body gew-card--tight">
                <DescriptionGrid
                  items={[
                    { label: '事项名称', value: extracted.title },
                    { label: '收支方向', value: DIRECTION_LABELS[extracted.direction] },
                    {
                      label: '金额',
                      value: <span className="num">{formatCny(extracted.amount_cents)}</span>,
                    },
                    { label: '预计时间', value: formatDateTime(extracted.scheduled_at) },
                    { label: '状态', value: STATE_LABELS[extracted.state as 'scheduled'] },
                    { label: '来源说明', value: extracted.source_label ?? '—' },
                  ]}
                />

                {extracted.warnings.length > 0 ? (
                  <div style={{ marginTop: 12 }}>
                    <InlineNote tone="warning">
                      <ul style={{ margin: 0, paddingLeft: 18 }}>
                        {extracted.warnings.map((item, index) => (
                          <li key={index}>{item}</li>
                        ))}
                      </ul>
                    </InlineNote>
                  </div>
                ) : null}

                <div style={{ marginTop: 16 }}>
                  <InlineNote tone="info">
                    请确认金额与时间是否与原文一致。确认后系统会按你确认的内容写入事项，
                    计算仍由确定性引擎完成。
                  </InlineNote>
                </div>

                <Space style={{ marginTop: 16 }} wrap>
                  <Button
                    type="primary"
                    loading={confirmMutation.isPending}
                    onClick={() => confirmMutation.mutate()}
                  >
                    确认并写入事项
                  </Button>
                  <Button onClick={() => extractMutation.mutate()}>重新整理</Button>
                  <Button onClick={() => setManualOpen(true)}>手动调整</Button>
                  <Button type="text" onClick={reset}>
                    清空
                  </Button>
                </Space>
              </div>
            </div>
          ) : null}
        </div>
      </Drawer>

      <EventFormDrawer
        open={manualOpen}
        event={null}
        onClose={() => setManualOpen(false)}
        onSaved={onSaved}
      />
    </>
  );
}
