/**
 * 历史经营数据与结算记录管理。
 *
 * 关键产品约束：
 * * 缺失的日期**不能**当成 0：必须由用户明确确认「该日期范围内数据完整」，
 *   缺失交易的完整日期才允许聚合为 0
 * * 支持 UTF-8 / UTF-8 BOM / GB18030 编码识别
 * * 流程固定为：选择文件 → 编码识别 → 字段映射 → 数据校验 → 日期范围确认 →
 *   数据完整性确认 → 预览 → 保存
 * * 只统计已完成的结算记录；未完成项单独计数
 */

import { useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  Alert,
  App as AntdApp,
  Button,
  Checkbox,
  Empty,
  InputNumber,
  Space,
  Table,
  Tag,
  Upload,
} from 'antd';
import { InboxOutlined, ReloadOutlined } from '@ant-design/icons';

import {
  enhancementApi,
  type DailyHistoryRow,
  type HistoryImportRow,
  type SettlementRecordRow,
} from '@/api/enhancements';
import { errorMessage } from '@/api/client';
import { queryKeys, queryClient } from '@/api/queryClient';
import { InlineNote, MetricCard, ResponsiveDataView, SectionCard } from '@/components/ui';
import MobileHistoryCards from '@/features/enhancement/MobileHistoryCards';
import { formatCny } from '@/utils/money';
import { formatDateTime } from '@/utils/datetime';
import { decodeCsvFile, parseCsvText } from '@/utils/csv';

const HISTORY_COLUMN_ALIASES: Record<string, string[]> = {
  day: ['日期', 'day', 'date', '自然日', '交易日期'],
  inflow: ['收入', '到账', 'inflow', '收入金额', '到账金额'],
  outflow: ['支出', '采购', 'outflow', '支出金额', '采购金额'],
  source: ['来源', 'source', '来源说明', '备注'],
};

const SETTLEMENT_COLUMN_ALIASES: Record<string, string[]> = {
  external_key: ['编号', '单号', 'external_key', '结算单号', '流水号'],
  channel: ['渠道', 'channel', '收款渠道', '平台'],
  scheduled_at: ['预计时间', '预计到账', 'scheduled_at', '结算日期'],
  actual_at: ['实际时间', '实际到账', 'actual_at', '到账时间'],
  known_at: ['登记时间', 'known_at'],
  status: ['状态', 'status'],
  source_ref: ['来源', 'source_ref', '来源说明'],
};

function findColumn(columns: string[], aliases: string[]): string | null {
  for (const alias of aliases) {
    const hit = columns.find((item) => item.trim().toLowerCase() === alias.toLowerCase());
    if (hit) return hit;
  }
  // 退一步做包含匹配
  for (const alias of aliases) {
    const hit = columns.find((item) => item.toLowerCase().includes(alias.toLowerCase()));
    if (hit) return hit;
  }
  return null;
}

function toCents(raw: string): number {
  const cleaned = (raw ?? '').replace(/[￥¥,\s]/g, '');
  if (!cleaned) return 0;
  const value = Number(cleaned);
  if (!Number.isFinite(value)) return 0;
  return Math.round(value * 100);
}

// ---------------------------------------------------------------------------
// 历史经营数据
// ---------------------------------------------------------------------------
export function HistoryPane() {
  const { message } = AntdApp.useApp();
  const [rows, setRows] = useState<HistoryImportRow[]>([]);
  const [fileName, setFileName] = useState('');
  const [encoding, setEncoding] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [fillMissing, setFillMissing] = useState(true);
  const [token, setToken] = useState('');
  const [preview, setPreview] = useState<Awaited<
    ReturnType<typeof enhancementApi.previewHistory>
  > | null>(null);

  const historyQuery = useQuery({
    queryKey: queryKeys.dailyHistory,
    queryFn: enhancementApi.dailyHistory,
  });

  const previewMutation = useMutation({
    mutationFn: () =>
      enhancementApi.previewHistory({
        rows,
        completeness_confirmed: confirmed,
        fill_missing_days: fillMissing,
        file_name: fileName,
      }),
    onSuccess: (data) => {
      setPreview(data);
      setToken(data.preview_token);
      if (data.requires_completeness_confirmation && !confirmed) {
        message.warning('该日期范围内存在没有记录的日期，请先确认数据完整性。');
      }
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const confirmMutation = useMutation({
    mutationFn: () =>
      enhancementApi.confirmHistory({
        preview_token: token,
        rows,
        completeness_confirmed: confirmed,
        fill_missing_days: fillMissing,
      }),
    onSuccess: (data) => {
      message.success(data.message);
      setRows([]);
      setPreview(null);
      setToken('');
      setConfirmed(false);
      queryClient.invalidateQueries({ queryKey: queryKeys.dailyHistory });
      queryClient.invalidateQueries({ queryKey: ['enhancements'] });
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const handleFile = async (file: File) => {
    try {
      const decoded = await decodeCsvFile(file);
      const parsed = parseCsvText(decoded.text);
      if (!parsed.columns.length || !parsed.rows.length) {
        message.error('文件里没有可识别的数据行');
        return;
      }
      const dayCol = findColumn(parsed.columns, HISTORY_COLUMN_ALIASES.day);
      if (!dayCol) {
        message.error('缺少必要字段：日期');
        return;
      }
      const inflowCol = findColumn(parsed.columns, HISTORY_COLUMN_ALIASES.inflow);
      const outflowCol = findColumn(parsed.columns, HISTORY_COLUMN_ALIASES.outflow);
      const sourceCol = findColumn(parsed.columns, HISTORY_COLUMN_ALIASES.source);

      const mapped: HistoryImportRow[] = [];
      for (const row of parsed.rows) {
        const rawDay = (row[dayCol] ?? '').trim();
        const day = rawDay.replace(/[/.]/g, '-').replace(/年|月/g, '-').replace(/日/g, '');
        if (!/^\d{4}-\d{1,2}-\d{1,2}$/.test(day)) continue;
        const [y, m, d] = day.split('-').map((item) => Number(item));
        const iso = `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        mapped.push({
          day: iso,
          inflow_cents: inflowCol ? toCents(row[inflowCol] ?? '') : 0,
          outflow_cents: outflowCol ? toCents(row[outflowCol] ?? '') : 0,
          source_label: sourceCol ? (row[sourceCol] ?? '').trim() : fileName,
          complete: true,
        });
      }
      if (!mapped.length) {
        message.error('没有解析出有效的日期行，请检查日期列格式');
        return;
      }
      mapped.sort((a, b) => a.day.localeCompare(b.day));
      setRows(mapped);
      setFileName(file.name);
      setEncoding(decoded.encoding);
      setPreview(null);
      setToken('');
      setConfirmed(false);
      message.success(
        `已读取 ${mapped.length} 行（编码 ${decoded.encoding}），请确认日期范围与数据完整性`,
      );
    } catch (error) {
      message.error(error instanceof Error ? error.message : '文件解析失败');
    }
  };

  const range = useMemo(() => {
    if (!rows.length) return null;
    return { start: rows[0].day, end: rows[rows.length - 1].day };
  }, [rows]);

  return (
    <div className="gew-stack">
      <SectionCard title="历史经营数据">
        <InlineNote tone="info">
          只导入**已经确认完整**的自然日日常到账与日常采购。房租、税款、退款等固定义务请继续在
          「收付款事项」里登记，不要在这里重复计入。
        </InlineNote>

        <div style={{ marginTop: 16 }}>
          <Upload.Dragger
            accept=".csv,text/csv"
            maxCount={1}
            showUploadList={false}
            beforeUpload={(file) => {
              void handleFile(file as unknown as File);
              return false;
            }}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽 CSV 文件到此处</p>
            <p className="ant-upload-hint">
              支持 UTF-8、UTF-8 BOM、GB18030；至少需要「日期」列，收入与支出列可选
            </p>
          </Upload.Dragger>
        </div>

        {rows.length > 0 ? (
          <div style={{ marginTop: 16 }} className="gew-stack">
            <Space wrap>
              <Tag bordered={false}>文件：{fileName || '未命名'}</Tag>
              <Tag bordered={false}>编码：{encoding}</Tag>
              <Tag bordered={false}>行数：{rows.length}</Tag>
              {range ? (
                <Tag bordered={false}>
                  日期范围：{range.start} ~ {range.end}
                </Tag>
              ) : null}
            </Space>

            <Checkbox checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)}>
              该日期范围内的数据完整；没有记录的日期代表当天确实没有对应收付。
            </Checkbox>

            <Checkbox
              checked={fillMissing}
              onChange={(event) => setFillMissing(event.target.checked)}
              disabled={!confirmed}
            >
              把范围内没有记录的日期补为 0（只有勾选上面一项才可用）
            </Checkbox>

            <Space wrap>
              <Button
                type="primary"
                loading={previewMutation.isPending}
                onClick={() => previewMutation.mutate()}
              >
                预览导入结果
              </Button>
              <Button
                loading={confirmMutation.isPending}
                disabled={!token || !preview?.can_confirm}
                onClick={() => confirmMutation.mutate()}
              >
                确认导入
              </Button>
              <Button
                onClick={() => {
                  setRows([]);
                  setPreview(null);
                  setToken('');
                  setConfirmed(false);
                }}
              >
                清空
              </Button>
            </Space>

            {preview ? (
              <Alert
                type={preview.can_confirm ? 'success' : 'warning'}
                showIcon
                message={
                  preview.can_confirm
                    ? `可导入 ${preview.valid_rows} 天数据`
                    : '还不能导入，请先处理下面的问题'
                }
                description={
                  <div>
                    <div>
                      日期范围 {preview.range_start} ~ {preview.range_end}；
                      范围内没有记录的日期 {preview.missing_row_count} 天；
                      重复行 {preview.duplicate_rows} 行。
                    </div>
                    {preview.requires_completeness_confirmation && !confirmed ? (
                      <div style={{ marginTop: 6 }}>
                        请勾选「该日期范围内的数据完整」，否则缺失的日期不会被当作 0。
                      </div>
                    ) : null}
                    {preview.issues.length > 0 ? (
                      <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                        {preview.issues.slice(0, 6).map((issue, index) => (
                          <li key={index}>
                            第 {issue.row ?? '-'} 行：{issue.reason}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                }
              />
            ) : null}

            <Table
              size="small"
              rowKey="day"
              pagination={{ pageSize: 8 }}
              dataSource={rows}
              scroll={{ x: 520 }}
              columns={[
                { title: '日期', dataIndex: 'day', width: 120 },
                {
                  title: '收入',
                  dataIndex: 'inflow_cents',
                  align: 'right' as const,
                  render: (value: number) => <span className="num">{formatCny(value)}</span>,
                },
                {
                  title: '支出',
                  dataIndex: 'outflow_cents',
                  align: 'right' as const,
                  render: (value: number) => <span className="num">{formatCny(value)}</span>,
                },
                { title: '来源', dataIndex: 'source_label' },
              ]}
            />
          </div>
        ) : null}
      </SectionCard>

      <SectionCard
        title="已保存的历史经营数据"
        extra={
          <Button
            size="small"
            icon={<ReloadOutlined />}
            loading={historyQuery.isFetching}
            onClick={() => historyQuery.refetch()}
          />
        }
      >
        {historyQuery.data && historyQuery.data.complete_days > 0 ? (
          <div className="gew-stack">
            <div className="gew-mini-metrics">
              <MetricCard label="完整天数" value={String(historyQuery.data.complete_days)} />
              <MetricCard
                label="区间"
                value={`${historyQuery.data.first_day ?? '—'} ~ ${historyQuery.data.last_day ?? '—'}`}
              />
              <MetricCard
                label="累计到账"
                value={formatCny(historyQuery.data.total_inflow_cents)}
                tone="inflow"
              />
              <MetricCard
                label="累计采购"
                value={formatCny(historyQuery.data.total_outflow_cents)}
                tone="outflow"
              />
            </div>
            {historyQuery.data.continuity_warning ? (
              <Alert
                type="warning"
                showIcon
                message="历史日期不连续"
                description={`${historyQuery.data.continuity_warning}（缺少 ${
                  historyQuery.data.missing_days.length
                } 天）`}
              />
            ) : null}
            <ResponsiveDataView
              mobileCards={<MobileHistoryCards items={historyQuery.data.items as DailyHistoryRow[]} />}
              desktopTable={
                <Table
                  size="small"
                  rowKey="id"
                  pagination={{ pageSize: 10 }}
                  dataSource={historyQuery.data.items}
                  scroll={{ x: 560 }}
                  columns={[
                    { title: '日期', dataIndex: 'day', width: 120 },
                    {
                      title: '收入',
                      dataIndex: 'inflow_cents',
                      align: 'right' as const,
                      render: (value: number) => <span className="num">{formatCny(value)}</span>,
                    },
                    {
                      title: '支出',
                      dataIndex: 'outflow_cents',
                      align: 'right' as const,
                      render: (value: number) => <span className="num">{formatCny(value)}</span>,
                    },
                    {
                      title: '净额',
                      dataIndex: 'net_cents',
                      align: 'right' as const,
                      render: (value: number) => <span className="num">{formatCny(value)}</span>,
                    },
                    {
                      title: '完整性',
                      dataIndex: 'completeness_confirmed',
                      width: 130,
                      render: (value: boolean) => (
                        <Tag bordered={false} color={value ? 'green' : 'default'}>
                          {value ? '已确认完整' : '按记录导入'}
                        </Tag>
                      ),
                    },
                  ]}
                />
              }
            />
          </div>
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="还没有历史经营数据。导入后才能形成 7 天日常收付参考与留底建议。"
          />
        )}
      </SectionCard>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 结算记录
// ---------------------------------------------------------------------------
export function SettlementPane() {
  const { message } = AntdApp.useApp();
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [token, setToken] = useState('');
  const [preview, setPreview] = useState<Awaited<
    ReturnType<typeof enhancementApi.previewSettlementRecords>
  > | null>(null);
  const [manualKey, setManualKey] = useState('');
  const [manualChannel, setManualChannel] = useState('');

  const recordsQuery = useQuery({
    queryKey: queryKeys.settlementRecords,
    queryFn: enhancementApi.settlementRecords,
  });

  const previewMutation = useMutation({
    mutationFn: () => enhancementApi.previewSettlementRecords({ rows }),
    onSuccess: (data) => {
      setPreview(data);
      setToken(data.preview_token);
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const confirmMutation = useMutation({
    mutationFn: () =>
      enhancementApi.confirmSettlementRecords({ preview_token: token, rows }),
    onSuccess: (data) => {
      message.success(data.message);
      setRows([]);
      setPreview(null);
      setToken('');
      queryClient.invalidateQueries({ queryKey: queryKeys.settlementRecords });
      queryClient.invalidateQueries({ queryKey: ['enhancements'] });
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const handleFile = async (file: File) => {
    try {
      const decoded = await decodeCsvFile(file);
      const parsed = parseCsvText(decoded.text);
      if (!parsed.columns.length || !parsed.rows.length) {
        message.error('文件里没有可识别的数据行');
        return;
      }
      const keyCol = findColumn(parsed.columns, SETTLEMENT_COLUMN_ALIASES.external_key);
      const channelCol = findColumn(parsed.columns, SETTLEMENT_COLUMN_ALIASES.channel);
      const scheduledCol = findColumn(parsed.columns, SETTLEMENT_COLUMN_ALIASES.scheduled_at);
      if (!keyCol || !channelCol || !scheduledCol) {
        message.error('缺少必要字段：编号 / 渠道 / 预计时间');
        return;
      }
      const actualCol = findColumn(parsed.columns, SETTLEMENT_COLUMN_ALIASES.actual_at);
      const knownCol = findColumn(parsed.columns, SETTLEMENT_COLUMN_ALIASES.known_at);
      const statusCol = findColumn(parsed.columns, SETTLEMENT_COLUMN_ALIASES.status);
      const sourceCol = findColumn(parsed.columns, SETTLEMENT_COLUMN_ALIASES.source_ref);

      const mapped = parsed.rows
        .map((row) => {
          const scheduled = (row[scheduledCol] ?? '').trim();
          const actual = actualCol ? (row[actualCol] ?? '').trim() : '';
          const statusRaw = statusCol ? (row[statusCol] ?? '').trim() : '';
          const status =
            statusRaw === '已完成' || statusRaw === 'completed'
              ? 'completed'
              : statusRaw === '已取消' || statusRaw === 'cancelled'
                ? 'cancelled'
                : 'open';
          return {
            external_key: (row[keyCol] ?? '').trim(),
            channel: (row[channelCol] ?? '').trim(),
            scheduled_at: scheduled,
            actual_at: status === 'completed' ? actual || null : null,
            known_at: knownCol ? (row[knownCol] ?? '').trim() || null : null,
            status,
            source_ref: sourceCol ? (row[sourceCol] ?? '').trim() : '结算记录导入',
          };
        })
        .filter((item) => item.external_key && item.channel && item.scheduled_at);

      if (!mapped.length) {
        message.error('没有解析出有效行，请检查编号、渠道、预计时间三列');
        return;
      }
      setRows(mapped);
      setPreview(null);
      setToken('');
      message.success(`已读取 ${mapped.length} 条结算记录（编码 ${decoded.encoding}）`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '文件解析失败');
    }
  };

  return (
    <div className="gew-stack">
      <SectionCard title="结算记录（预计 vs 实际到账）">
        <InlineNote tone="info">
          只有**已完成**的记录会参与历史延迟统计；未完成项单独计数，不会被当成 0 天延迟。
        </InlineNote>

        <div style={{ marginTop: 16 }}>
          <Upload.Dragger
            accept=".csv,text/csv"
            maxCount={1}
            showUploadList={false}
            beforeUpload={(file) => {
              void handleFile(file as unknown as File);
              return false;
            }}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽结算记录 CSV 到此处</p>
            <p className="ant-upload-hint">
              需要「编号 / 渠道 / 预计时间」列；「实际时间」列用于计算延迟，状态填「已完成」或「未完成」
            </p>
          </Upload.Dragger>
        </div>

        <Space wrap style={{ marginTop: 12 }}>
          <InputNumber
            placeholder="手动补录：结算款金额（元）"
            min={0}
            precision={2}
            onChange={(value) => setManualKey(value === null ? '' : String(value))}
          />
          <Button
            disabled={!manualKey}
            onClick={() => {
              setManualKey('');
              setManualChannel('');
              message.info('请使用「收付款事项」登记结算款，或导入结算记录 CSV。');
            }}
          >
            说明
          </Button>
          <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
            渠道可留空，导入后可在下方按渠道查看统计
          </span>
          <span style={{ display: 'none' }}>{manualChannel}</span>
        </Space>

        {rows.length > 0 ? (
          <div style={{ marginTop: 16 }} className="gew-stack">
            <Space wrap>
              <Tag bordered={false}>待导入：{rows.length} 条</Tag>
              <Button
                type="primary"
                loading={previewMutation.isPending}
                onClick={() => previewMutation.mutate()}
              >
                预览导入结果
              </Button>
              <Button
                loading={confirmMutation.isPending}
                disabled={!token || !preview?.can_confirm}
                onClick={() => confirmMutation.mutate()}
              >
                确认导入
              </Button>
              <Button
                onClick={() => {
                  setRows([]);
                  setPreview(null);
                  setToken('');
                }}
              >
                清空
              </Button>
            </Space>

            {preview ? (
              <Alert
                type={preview.can_confirm ? 'success' : 'warning'}
                showIcon
                message={
                  preview.can_confirm
                    ? `可导入 ${preview.valid_rows} 条（已完成 ${preview.completed_count} 条，未完成 ${preview.open_count} 条）`
                    : '还不能导入，请先处理下面的问题'
                }
                description={
                  preview.issues.length > 0 ? (
                    <ul style={{ margin: 0, paddingLeft: 18 }}>
                      {preview.issues.slice(0, 6).map((issue, index) => (
                        <li key={index}>
                          第 {issue.row} 行：{issue.reason}
                        </li>
                      ))}
                    </ul>
                  ) : null
                }
              />
            ) : null}
          </div>
        ) : null}
      </SectionCard>

      <SectionCard
        title="已保存的结算记录"
        extra={
          <Button
            size="small"
            icon={<ReloadOutlined />}
            loading={recordsQuery.isFetching}
            onClick={() => recordsQuery.refetch()}
          />
        }
      >
        {recordsQuery.data && recordsQuery.data.items.length > 0 ? (
          <div className="gew-stack">
            <div className="gew-mini-metrics">
              <MetricCard label="已完成" value={String(recordsQuery.data.completed_count)} />
              <MetricCard label="未完成" value={String(recordsQuery.data.open_count)} />
              <MetricCard
                label="渠道"
                value={recordsQuery.data.channels.join('、') || '—'}
              />
            </div>
            <Table<SettlementRecordRow>
              size="small"
              rowKey="id"
              pagination={{ pageSize: 10 }}
              dataSource={recordsQuery.data.items}
              scroll={{ x: 720 }}
              columns={[
                { title: '编号', dataIndex: 'external_key', width: 140 },
                { title: '渠道', dataIndex: 'channel', width: 120 },
                {
                  title: '预计到账',
                  dataIndex: 'scheduled_at',
                  render: (value: string) => formatDateTime(value),
                },
                {
                  title: '实际到账',
                  dataIndex: 'actual_at',
                  render: (value: string | null) => (value ? formatDateTime(value) : '—'),
                },
                {
                  title: '延迟',
                  dataIndex: 'delay_days',
                  width: 90,
                  align: 'right' as const,
                  render: (value: number | null) =>
                    value === null ? '—' : `${value} 天`,
                },
                {
                  title: '状态',
                  dataIndex: 'status',
                  width: 100,
                  render: (value: string) => (
                    <Tag bordered={false} color={value === 'completed' ? 'green' : 'default'}>
                      {value === 'completed' ? '已完成' : value === 'open' ? '未完成' : '已取消'}
                    </Tag>
                  ),
                },
              ]}
            />
          </div>
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="还没有结算记录。导入后才能计算历史经验延迟参考。"
          />
        )}
      </SectionCard>
    </div>
  );
}
