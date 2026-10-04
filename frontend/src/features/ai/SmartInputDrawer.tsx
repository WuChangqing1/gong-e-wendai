/**
 * 智能录入：把自然语言或截图整理成结构化事项。
 *
 * 边界：
 * * 智能服务只做理解与提取，金额与时间必须由用户确认
 * * 提取结果不会直接入库，必须进入「已为你整理，请确认」页面
 * * 截图只在内存与私有目录处理，不进入公开静态资源
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
  Tabs,
  Typography,
  Upload,
} from 'antd';
import { InboxOutlined, RobotOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';
import type { UploadFile } from 'antd';

import { aiApi, type AiExtractedEvent } from '@/api/ai';
import { cashEventApi } from '@/api/cashflow';
import { AI_FALLBACK_MESSAGE, errorMessage } from '@/api/client';
import EventFormDrawer from '@/features/events/EventFormDrawer';
import { useDrawerWidth } from '@/hooks/useResponsive';
import { DescriptionGrid, InlineNote, StatusTag } from '@/components/ui';
import { formatCny } from '@/utils/money';
import { formatDateTime } from '@/utils/datetime';
import { DIRECTION_LABELS, EVENT_TYPE_LABELS, STATE_LABELS } from '@/utils/labels';

const IMAGE_PLACEHOLDER = '上传结算通知、付款通知或收付款凭证截图';
const MAX_IMAGE_MB = 5;
const ACCEPTED_TYPES = ['image/png', 'image/jpeg', 'image/webp'];

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
  const [mode, setMode] = useState<'text' | 'image'>('text');
  const [text, setText] = useState('');
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [extracted, setExtracted] = useState<AiExtractedEvent | null>(null);
  const [filteredAmounts, setFilteredAmounts] = useState<string[]>([]);
  const [failed, setFailed] = useState<string | null>(null);
  const [manualOpen, setManualOpen] = useState(false);

  const extractMutation = useMutation({
    mutationFn: () => aiApi.extract({ text }),
    onSuccess: (data) => {
      setExtracted(data.event);
      setFilteredAmounts(data.filtered_amounts ?? []);
      setFailed(null);
    },
    onError: (error) => {
      const text2 = errorMessage(error) || AI_FALLBACK_MESSAGE;
      setFailed(text2);
      setExtracted(null);
      message.warning(text2);
    },
  });

  const imageMutation = useMutation({
    mutationFn: () => {
      const file = fileList[0]?.originFileObj as File | undefined;
      if (!file) throw new Error('请先选择一张截图');
      return aiApi.extractFromImage(file);
    },
    onSuccess: (data) => {
      setExtracted(data.event);
      setFilteredAmounts(data.filtered_amounts ?? []);
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
      if (!extracted.direction) throw new Error('请先选择这笔款项是收入还是支出');
      if (!extracted.amount_cents) throw new Error('请先确认金额');
      if (!extracted.scheduled_at) throw new Error('请先确认预计时间');
      return cashEventApi.create({
        title: extracted.title || '待补充事项',
        direction: extracted.direction,
        amount_cents: extracted.amount_cents,
        scheduled_at: extracted.scheduled_at,
        state: extracted.state,
        event_type: extracted.event_type,
        source_label: extracted.source_label ?? (mode === 'image' ? '截图识别' : '智能录入'),
        note:
          mode === 'text'
            ? `原始内容：${text}`
            : `截图识别来源：${fileList[0]?.name ?? '截图'}`,
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
    setFileList([]);
    setExtracted(null);
    setFilteredAmounts([]);
    setFailed(null);
  };

  const lowConfidence = extracted
    ? Object.values(extracted.confidence ?? {}).some((value) => value < 0.6) ||
      !extracted.amount_cents ||
      !extracted.scheduled_at
    : false;

  const pending = extractMutation.isPending || imageMutation.isPending;

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
          <Tabs
            activeKey={mode}
            onChange={(key) => {
              setMode(key as 'text' | 'image');
              setExtracted(null);
              setFailed(null);
            }}
            items={[
              { key: 'text', label: '粘贴文字' },
              { key: 'image', label: '上传截图' },
            ]}
          />

          {mode === 'text' ? (
            <>
              <Typography.Paragraph style={{ color: 'var(--text-secondary)', marginBottom: 0 }}>
                把结算通知、到账提醒、付款约定的原文粘贴进来，系统会整理成一条收付款事项。
                整理结果需要你核对后才会写入。
              </Typography.Paragraph>

              <Input.TextArea
                rows={4}
                value={text}
                onChange={(event) => setText(event.target.value)}
                placeholder="粘贴结算通知、付款通知或其他收付款信息"
                maxLength={4000}
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
                <Button type="link" onClick={() => setManualOpen(true)}>
                  改为手动录入
                </Button>
              </Space>
            </>
          ) : (
            <>
              <Typography.Paragraph style={{ color: 'var(--text-secondary)', marginBottom: 0 }}>
                {IMAGE_PLACEHOLDER}，系统会读取截图里的金额与时间。
              </Typography.Paragraph>

              <Upload.Dragger
                accept={ACCEPTED_TYPES.join(',')}
                maxCount={1}
                fileList={fileList}
                beforeUpload={(file) => {
                  if (!ACCEPTED_TYPES.includes(file.type)) {
                    message.error('只支持 PNG、JPEG、WEBP 格式的截图');
                    return Upload.LIST_IGNORE;
                  }
                  if (file.size > MAX_IMAGE_MB * 1024 * 1024) {
                    message.error(`图片不能超过 ${MAX_IMAGE_MB}MB`);
                    return Upload.LIST_IGNORE;
                  }
                  return false;
                }}
                onChange={({ fileList: next }) => setFileList(next.slice(-1))}
                onRemove={() => setFileList([])}
              >
                <p className="ant-upload-drag-icon">
                  <InboxOutlined />
                </p>
                <p className="ant-upload-text">点击或拖拽截图到此处</p>
                <p className="ant-upload-hint">
                  一次一张，PNG / JPEG / WEBP，最大 {MAX_IMAGE_MB}MB
                </p>
              </Upload.Dragger>

              <Space wrap>
                <Button
                  type="primary"
                  onClick={() => imageMutation.mutate()}
                  loading={imageMutation.isPending}
                  disabled={fileList.length === 0}
                >
                  识别截图内容
                </Button>
                <Button type="link" onClick={() => setManualOpen(true)}>
                  改为手动录入
                </Button>
              </Space>
            </>
          )}

          {pending ? <Spin tip="正在整理…" /> : null}

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
                    { label: '事项名称', value: extracted.title || '—' },
                    {
                      label: '收支方向',
                      value: extracted.direction ? DIRECTION_LABELS[extracted.direction] : '需确认',
                    },
                    {
                      label: '金额',
                      value: (
                        <span className="num">
                          {extracted.amount_cents ? formatCny(extracted.amount_cents) : '需确认'}
                        </span>
                      ),
                    },
                    {
                      label: '预计时间',
                      value: extracted.scheduled_at ? formatDateTime(extracted.scheduled_at) : '需确认',
                    },
                    { label: '状态', value: STATE_LABELS[extracted.state as 'scheduled'] },
                    {
                      label: '事项类型',
                      value: EVENT_TYPE_LABELS[extracted.event_type] ?? extracted.event_type,
                    },
                    { label: '渠道', value: extracted.channel ?? '—' },
                    { label: '来源说明', value: extracted.source_label ?? '—' },
                  ]}
                />

                {filteredAmounts.length > 0 ? (
                  <div style={{ marginTop: 12 }}>
                    <InlineNote tone="warning">
                      识别出的金额 {filteredAmounts.join('、')} 没有在原文中找到依据，已被系统移除。
                    </InlineNote>
                  </div>
                ) : null}

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
                    请确认金额与时间是否与原文或截图一致。确认后系统会按你确认的内容写入事项，
                    计算仍由确定性引擎完成。
                  </InlineNote>
                </div>

                <Space style={{ marginTop: 16 }} wrap>
                  <Button
                    type="primary"
                    loading={confirmMutation.isPending}
                    disabled={!extracted.direction || !extracted.amount_cents || !extracted.scheduled_at}
                    onClick={() => confirmMutation.mutate()}
                  >
                    确认并写入事项
                  </Button>
                  <Button
                    onClick={() => (mode === 'text' ? extractMutation.mutate() : imageMutation.mutate())}
                  >
                    重新识别
                  </Button>
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
