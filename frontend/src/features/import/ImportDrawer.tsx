/**
 * CSV 导入工作流。
 *
 * 上传 → 解析 → 字段映射 → 数据校验 → 问题提示 → 预览 → 用户确认 → 写入事项
 *
 * 上传后绝不直接入库；存在错误行时不允许提交。
 */

import { useState } from 'react';
import {
  Alert,
  App as AntdApp,
  Button,
  Drawer,
  Empty,
  Radio,
  Select,
  Space,
  Steps,
  Table,
  Tag,
  Upload,
  Typography,
} from 'antd';
import type { UploadFile } from 'antd';
import { InboxOutlined, ReloadOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';

import { importApi } from '@/api/import';
import { errorMessage } from '@/api/client';
import { DescriptionGrid, InlineNote, StatusTag } from '@/components/ui';
import type { ImportPreview, ImportPreviewRow } from '@/types';
import { formatCny } from '@/utils/money';
import { formatDateTime } from '@/utils/datetime';
import { DIRECTION_LABELS, STATE_LABELS } from '@/utils/labels';

const COLUMN_OPTIONS = [
  { value: 'cash_key', label: '事项编号' },
  { value: 'title', label: '事项名称' },
  { value: 'direction', label: '收支方向' },
  { value: 'amount', label: '金额' },
  { value: 'event_time', label: '交易时间' },
  { value: 'scheduled_at', label: '预计时间' },
  { value: 'state', label: '状态' },
  { value: 'source_label', label: '来源说明' },
  { value: 'note', label: '备注' },
  { value: 'ignore', label: '忽略该列' },
];

const FIELD_KEYS = COLUMN_OPTIONS.filter((item) => item.value !== 'ignore').map(
  (item) => item.value,
);

export default function ImportDrawer({
  open,
  onClose,
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  onImported: () => void;
}) {
  const { message } = AntdApp.useApp();
  const [step, setStep] = useState(0);
  const [fileType, setFileType] = useState<'transaction' | 'payment_plan'>('transaction');
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [failed, setFailed] = useState<string | null>(null);

  const reset = () => {
    setStep(0);
    setFileList([]);
    setPreview(null);
    setMapping({});
    setFailed(null);
  };

  const uploadMutation = useMutation({
    mutationFn: async () => {
      const file = fileList[0]?.originFileObj;
      if (!file) throw new Error('请先选择 CSV 文件');
      return importApi.upload(file, fileType);
    },
    onSuccess: (data) => {
      setPreview(data);
      setMapping(data.mapping);
      setFailed(null);
      setStep(1);
    },
    onError: (error) => {
      const text = errorMessage(error);
      setFailed(text);
      message.error(text);
    },
  });

  const remapMutation = useMutation({
    mutationFn: () => {
      if (!preview) throw new Error('没有可用的预览');
      return importApi.remap(preview.batch_id, mapping);
    },
    onSuccess: (data) => {
      setPreview(data);
      message.success('字段映射已更新，请重新检查问题提示');
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const commitMutation = useMutation({
    mutationFn: () => {
      if (!preview) throw new Error('没有可用的预览');
      return importApi.commit(preview.batch_id);
    },
    onSuccess: (result) => {
      message.success(
        `导入完成：新增 ${result.created} 条，跳过 ${result.skipped} 条，失败 ${result.failed} 条`,
      );
      onImported();
      reset();
      onClose();
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const errors = preview?.issues.filter((item) => item.severity === 'error') ?? [];
  const warnings = preview?.issues.filter((item) => item.severity === 'warning') ?? [];
  const hasErrorRows = (preview?.rows ?? []).some((row) => !row.valid);

  const columns = [
    { title: '行号', dataIndex: 'row_number', width: 64 },
    {
      title: '事项名称',
      dataIndex: 'title',
      render: (value: string | null) => value ?? <span style={{ color: 'var(--text-muted)' }}>—</span>,
    },
    {
      title: '方向',
      dataIndex: 'direction',
      width: 76,
      render: (value: string | null) =>
        value ? DIRECTION_LABELS[value as 'inflow'] : <span style={{ color: 'var(--danger)' }}>缺失</span>,
    },
    {
      title: '金额',
      dataIndex: 'amount_cents',
      width: 120,
      align: 'right' as const,
      render: (value: number | null) =>
        value === null ? (
          <span style={{ color: 'var(--danger)' }}>缺失</span>
        ) : (
          <span className="num">{formatCny(value)}</span>
        ),
    },
    {
      title: '预计时间',
      dataIndex: 'scheduled_at',
      width: 150,
      render: (value: string | null) =>
        value ? formatDateTime(value) : <span style={{ color: 'var(--danger)' }}>缺失</span>,
    },
    {
      title: '状态',
      dataIndex: 'state',
      width: 100,
      render: (value: string | null) =>
        value ? STATE_LABELS[value as 'scheduled'] : <span style={{ color: 'var(--danger)' }}>缺失</span>,
    },
    {
      title: '校验',
      key: 'valid',
      width: 160,
      render: (_value: unknown, row: ImportPreviewRow) =>
        row.valid ? (
          <StatusTag tone="ok">可导入</StatusTag>
        ) : (
          <StatusTag tone="danger">{row.issues[0]?.message ?? '存在问题'}</StatusTag>
        ),
    },
  ];

  return (
    <Drawer
      title="导入 CSV"
      width={860}
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      destroyOnHidden
      extra={
        <Space>
          {step > 0 ? (
            <Button icon={<ReloadOutlined />} onClick={reset}>
              重新上传
            </Button>
          ) : null}
          {step === 1 ? (
            <Button
              type="primary"
              onClick={() => remapMutation.mutate()}
              loading={remapMutation.isPending}
            >
              应用字段映射
            </Button>
          ) : null}
          {step === 2 ? (
            <Button
              type="primary"
              disabled={!preview?.can_commit}
              loading={commitMutation.isPending}
              onClick={() => commitMutation.mutate()}
            >
              确认导入 {preview?.valid_rows ?? 0} 条
            </Button>
          ) : null}
        </Space>
      }
    >
      <Steps
        current={step}
        size="small"
        style={{ marginBottom: 20 }}
        items={[
          { title: '选择文件' },
          { title: '字段映射与校验' },
          { title: '预览并确认' },
        ]}
      />

      {failed ? (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message="文件无法解析"
          description={failed}
        />
      ) : null}

      {step === 0 ? (
        <div className="gew-stack">
          <div>
            <Typography.Text strong>文件类型</Typography.Text>
            <div style={{ marginTop: 8 }}>
              <Radio.Group
                value={fileType}
                onChange={(event) => setFileType(event.target.value)}
                optionType="button"
                buttonStyle="solid"
              >
                <Radio.Button value="transaction">交易流水</Radio.Button>
                <Radio.Button value="payment_plan">未来付款计划</Radio.Button>
              </Radio.Group>
            </div>
          </div>

          <div className="gew-inline-note">
            <div>
              <div style={{ marginBottom: 4 }}>支持 UTF-8、UTF-8 BOM 与 GB18030 编码，自动识别常见中文 CSV。</div>
              <div className="gew-mono" style={{ fontSize: 12 }}>
                交易流水：cash_key,title,direction,amount,event_time,state,source_label,note
              </div>
              <div className="gew-mono" style={{ fontSize: 12 }}>
                付款计划：cash_key,title,direction,amount,scheduled_at,state,source_label,note
              </div>
            </div>
          </div>

          <Upload.Dragger
            accept=".csv,text/csv"
            maxCount={1}
            fileList={fileList}
            beforeUpload={() => false}
            onChange={({ fileList: list }) => setFileList(list)}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽 CSV 文件到此处</p>
            <p className="ant-upload-hint">单个文件不超过 5 MB。上传后不会直接入库，必须经过校验与确认。</p>
          </Upload.Dragger>

          <Button
            type="primary"
            disabled={fileList.length === 0}
            loading={uploadMutation.isPending}
            onClick={() => uploadMutation.mutate()}
          >
            解析文件
          </Button>
        </div>
      ) : null}

      {step >= 1 && preview ? (
        <div className="gew-stack">
          <DescriptionGrid
            items={[
              { label: '文件名', value: preview.file_name },
              { label: '编码', value: preview.encoding },
              { label: '分隔符', value: preview.delimiter === '\t' ? 'Tab' : preview.delimiter },
              { label: '总行数', value: <span className="num">{preview.total_rows}</span> },
              {
                label: '可导入',
                value: (
                  <span className="num" style={{ color: 'var(--success)' }}>
                    {preview.valid_rows}
                  </span>
                ),
              },
              {
                label: '存在问题',
                value: (
                  <span
                    className="num"
                    style={{ color: preview.invalid_rows ? 'var(--danger)' : 'var(--text-primary)' }}
                  >
                    {preview.invalid_rows}
                  </span>
                ),
              },
              {
                label: '与已有编号重复',
                value: <span className="num">{preview.duplicate_rows}</span>,
              },
            ]}
          />

          {preview.missing_columns.length > 0 ? (
            <InlineNote tone="danger">
              缺少必要字段：{preview.missing_columns.join('、')}。请在下方映射中指定对应列，或修正文件后重新上传。
            </InlineNote>
          ) : null}

          {step === 1 ? (
            <div>
              <Typography.Text strong>字段映射</Typography.Text>
              <div
                style={{
                  display: 'grid',
                  gap: 12,
                  gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
                  marginTop: 12,
                }}
              >
                {FIELD_KEYS.map((field) => (
                  <div key={field}>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>
                      {COLUMN_OPTIONS.find((item) => item.value === field)?.label}
                    </div>
                    <Select
                      style={{ width: '100%' }}
                      value={mapping[field] ?? 'ignore'}
                      onChange={(value) => setMapping((prev) => ({ ...prev, [field]: value }))}
                      options={[
                        { value: 'ignore', label: '未映射' },
                        ...preview.columns
                          .filter((column) => column)
                          .map((column) => ({ value: column, label: column })),
                      ]}
                    />
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {errors.length > 0 ? (
            <Alert
              type="error"
              showIcon
              message={`发现 ${errors.length} 个必须处理的问题`}
              description={
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {errors.slice(0, 8).map((item, index) => (
                    <li key={index}>
                      第 {item.row_number ?? '?'} 行：{item.message}
                    </li>
                  ))}
                  {errors.length > 8 ? <li>还有 {errors.length - 8} 条…</li> : null}
                </ul>
              }
            />
          ) : null}

          {warnings.length > 0 ? (
            <Alert
              type="warning"
              showIcon
              message={`${warnings.length} 条提示`}
              description={
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {warnings.slice(0, 6).map((item, index) => (
                    <li key={index}>
                      第 {item.row_number ?? '?'} 行：{item.message}
                    </li>
                  ))}
                </ul>
              }
            />
          ) : null}

          {hasErrorRows ? (
            <InlineNote tone="danger">
              存在问题的行不会进入正式计算。请修正文件后重新上传，或使用字段映射纠正列对应关系。
            </InlineNote>
          ) : null}

          <div>
            <Typography.Text strong>数据预览</Typography.Text>
            <Table<ImportPreviewRow>
              rowKey="row_number"
              size="small"
              style={{ marginTop: 8 }}
              columns={columns}
              dataSource={preview.rows.slice(0, 50)}
              pagination={{ pageSize: 10, size: 'small' }}
              scroll={{ x: 760 }}
              onRow={(record) => ({
                style: record.valid ? undefined : { background: 'var(--danger-soft)' },
              })}
            />
          </div>

          {step === 1 ? (
            <Button type="primary" onClick={() => setStep(2)} disabled={Boolean(preview.missing_columns.length)}>
              下一步：预览并确认
            </Button>
          ) : null}

          {step === 2 ? (
            <div className="gew-stack">
              <InlineNote tone={preview.can_commit ? 'info' : 'danger'}>
                {preview.can_commit
                  ? `将写入 ${preview.valid_rows} 条收付款事项，来源标记为「CSV 导入」并保存原始行内容。`
                  : '当前文件仍存在必须处理的问题，无法导入。请返回修正后再试。'}
              </InlineNote>
              <Space>
                <Button onClick={() => setStep(1)}>返回上一步</Button>
                <Button
                  type="primary"
                  disabled={!preview.can_commit}
                  loading={commitMutation.isPending}
                  onClick={() => commitMutation.mutate()}
                >
                  确认导入
                </Button>
              </Space>
            </div>
          ) : null}
        </div>
      ) : null}

      {step >= 1 && !preview ? (
        <Empty description="没有可预览的数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : null}

      <div style={{ marginTop: 16 }}>
        <Tag bordered={false} color="default">
          导入的事项可以在「现金事件」中查看来源、版本与原始行内容
        </Tag>
      </div>
    </Drawer>
  );
}
