/** 现金事件（收付款事项）页面。 */

import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  App as AntdApp,
  Button,
  DatePicker,
  Empty,
  Input,
  Pagination,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  EditOutlined,
  FileSearchOutlined,
  FilterOutlined,
  HistoryOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  StopOutlined,
  UploadOutlined,
} from '@ant-design/icons';

import { cashEventApi, type CashEventQuery } from '@/api/cashflow';
import { errorMessage } from '@/api/client';
import { queryKeys, queryClient } from '@/api/queryClient';
import { MetricCard, PageHeader, SectionCard, StatusTag } from '@/components/ui';
import EventFormDrawer from '@/features/events/EventFormDrawer';
import MobileEventList from '@/features/events/MobileEventList';
import RevisionDrawer from '@/features/events/RevisionDrawer';
import SourceDrawer from '@/features/events/SourceDrawer';
import SmartInputDrawer from '@/features/ai/SmartInputDrawer';
import ImportDrawer from '@/features/import/ImportDrawer';
import { HistoryPane, SettlementPane } from '@/features/enhancement/HistoryPanes';
import { useIsMobile } from '@/hooks/useResponsive';
import type { CashEvent, Direction } from '@/types';
import { formatCny, formatSigned } from '@/utils/money';
import { formatDateTime } from '@/utils/datetime';
import {
  DIRECTION_LABELS,
  EVENT_TYPE_LABELS,
  SOURCE_LABELS,
  STATE_LABELS,
} from '@/utils/labels';

const PAGE_SIZE = 15;

export default function EventsPage() {
  const { message } = AntdApp.useApp();
  const queryClientInstance = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const isMobile = useIsMobile();
  const [filtersOpen, setFiltersOpen] = useState(false);

  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [direction, setDirection] = useState<Direction | undefined>();
  const [state, setState] = useState<string | undefined>();
  const [eventType, setEventType] = useState<string | undefined>();
  const [range, setRange] = useState<[string, string] | null>(null);

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<CashEvent | null>(null);
  const [revisionId, setRevisionId] = useState<string | null>(null);
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [smartOpen, setSmartOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [tab, setTab] = useState<'events' | 'history' | 'settlements'>('events');

  const focusId = searchParams.get('focus');
  useEffect(() => {
    if (focusId) {
      setSourceId(focusId);
    }
  }, [focusId]);

  /** 手机端折叠筛选时，用来提示「当前有几项筛选条件生效」，避免用户在无结果时找不到原因。 */
  const activeFilterCount =
    (search ? 1 : 0) +
    (direction ? 1 : 0) +
    (state ? 1 : 0) +
    (eventType ? 1 : 0) +
    (range ? 1 : 0);

  const params: CashEventQuery = useMemo(
    () => ({
      page,
      page_size: PAGE_SIZE,
      search: search || undefined,
      direction,
      state,
      event_type: eventType,
      start: range?.[0],
      end: range?.[1],
    }),
    [page, search, direction, state, eventType, range],
  );

  const listQuery = useQuery({
    queryKey: queryKeys.cashEvents(params as unknown as Record<string, unknown>),
    queryFn: () => cashEventApi.list(params),
  });

  const statsQuery = useQuery({
    queryKey: queryKeys.cashEventStats,
    queryFn: cashEventApi.stats,
  });

  const cancelMutation = useMutation({
    mutationFn: (id: string) => cashEventApi.cancel(id, '商户取消'),
    onSuccess: () => {
      message.success('事项已取消，历史记录仍然保留');
      queryClientInstance.invalidateQueries({ queryKey: ['cash-events'] });
      queryClientInstance.invalidateQueries({ queryKey: queryKeys.analysisStale });
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const columns: ColumnsType<CashEvent> = [
    {
      title: '预计时间',
      dataIndex: 'scheduled_at',
      width: 150,
      render: (value: string) => <span className="num">{formatDateTime(value)}</span>,
    },
    {
      title: '事项',
      dataIndex: 'title',
      render: (_value, record) => (
        <div>
          <div style={{ fontWeight: 500 }}>{record.title}</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            {record.cash_key} · {EVENT_TYPE_LABELS[record.event_type] ?? record.event_type}
          </div>
        </div>
      ),
    },
    {
      title: '方向',
      dataIndex: 'direction',
      width: 80,
      render: (value: Direction) => (
        <Tag color={value === 'inflow' ? 'green' : 'default'} bordered={false}>
          {DIRECTION_LABELS[value]}
        </Tag>
      ),
    },
    {
      title: '金额',
      dataIndex: 'amount_cents',
      width: 140,
      align: 'right',
      render: (value: number, record) => (
        <span className={`num ${record.direction === 'inflow' ? 'gew-amount-inflow' : 'gew-amount-outflow'}`}>
          {record.direction === 'inflow' ? formatSigned(value) : `-${formatCny(value, false)}`}
        </span>
      ),
    },
    {
      title: '状态',
      dataIndex: 'state',
      width: 130,
      render: (value: string, record) => (
        <Space size={4} direction="vertical">
          <StatusTag
            tone={value === 'cancelled' ? 'neutral' : value === 'included_in_opening' ? 'info' : 'ok'}
          >
            {STATE_LABELS[value as keyof typeof STATE_LABELS] ?? value}
          </StatusTag>
          {record.current_version > 1 ? (
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>v{record.current_version}</span>
          ) : null}
        </Space>
      ),
    },
    {
      title: '来源',
      dataIndex: 'source_type',
      width: 110,
      render: (value: keyof typeof SOURCE_LABELS, record) => (
        <Tooltip title={record.source_label ?? ''}>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            {SOURCE_LABELS[value] ?? value}
          </span>
        </Tooltip>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      render: (_value, record) => (
        <Space size={4}>
          <Tooltip title="修改">
            <Button
              type="text"
              size="small"
              icon={<EditOutlined />}
              onClick={() => {
                setEditing(record);
                setFormOpen(true);
              }}
            />
          </Tooltip>
          <Tooltip title="来源">
            <Button
              type="text"
              size="small"
              icon={<FileSearchOutlined />}
              onClick={() => setSourceId(record.id)}
            />
          </Tooltip>
          <Tooltip title="版本">
            <Button
              type="text"
              size="small"
              icon={<HistoryOutlined />}
              onClick={() => setRevisionId(record.id)}
            />
          </Tooltip>
          {record.state !== 'cancelled' ? (
            <Popconfirm
              title="取消该事项？"
              description="取消后不参与未来计算，但历史记录与来源会完整保留。"
              okText="确认取消"
              cancelText="返回"
              onConfirm={() => cancelMutation.mutate(record.id)}
            >
              <Tooltip title="取消事项">
                <Button type="text" size="small" danger icon={<StopOutlined />} />
              </Tooltip>
            </Popconfirm>
          ) : null}
        </Space>
      ),
    },
  ];

  return (
    <div className="gew-stack">
      <PageHeader
        title="现金事件"
        subtitle="这里是你已经规范化的收付款事项。只有状态为「计划中」的事项会进入未来 7 天推演。"
        extra={
          <Space wrap>
            <Button icon={<RobotOutlined />} onClick={() => setSmartOpen(true)}>
              智能录入
            </Button>
            <Button icon={<UploadOutlined />} onClick={() => setImportOpen(true)}>
              导入 CSV
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => {
                setEditing(null);
                setFormOpen(true);
              }}
            >
              新增事项
            </Button>
          </Space>
        }
      />

      <Tabs
        activeKey={tab}
        onChange={(key) => setTab(key as 'events' | 'history' | 'settlements')}
        items={[
          { key: 'events', label: '收付款事项' },
          { key: 'history', label: '历史经营数据' },
          { key: 'settlements', label: '结算记录' },
        ]}
      />

      {tab === 'history' ? (
        <HistoryPane />
      ) : tab === 'settlements' ? (
        <SettlementPane />
      ) : (
        <>
      <div
        style={{
          display: 'grid',
          gap: 16,
          gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
        }}
      >
        <SectionCard flat bodyClassName="gew-card__body--tight">
          <MetricCard
            label="计划中收入"
            value={formatCny(statsQuery.data?.inflow_cents ?? 0)}
            tone="inflow"
            footnote={`${statsQuery.data?.scheduled ?? 0} 笔计划中事项`}
          />
        </SectionCard>
        <SectionCard flat bodyClassName="gew-card__body--tight">
          <MetricCard
            label="计划中支出"
            value={formatCny(statsQuery.data?.outflow_cents ?? 0)}
            footnote="已确认的未来付款"
          />
        </SectionCard>
        <SectionCard flat bodyClassName="gew-card__body--tight">
          <MetricCard
            label="已计入期初"
            value={String(statsQuery.data?.included_in_opening ?? 0)}
            footnote="不重复计入未来变化"
          />
        </SectionCard>
        <SectionCard flat bodyClassName="gew-card__body--tight">
          <MetricCard
            label="已取消"
            value={String(statsQuery.data?.cancelled ?? 0)}
            footnote="历史记录仍完整保留"
          />
        </SectionCard>
      </div>

      <SectionCard
        title="事项列表"
        extra={
          <Tooltip title="刷新">
            <Button
              size="small"
              icon={<ReloadOutlined />}
              loading={listQuery.isFetching}
              onClick={() => listQuery.refetch()}
            />
          </Tooltip>
        }
      >
        <Space wrap style={{ marginBottom: isMobile && !filtersOpen ? 12 : 16 }}>
          {isMobile ? (
            <Button
              icon={<FilterOutlined />}
              onClick={() => setFiltersOpen((value) => !value)}
              aria-expanded={filtersOpen}
              type={activeFilterCount > 0 ? 'primary' : 'default'}
              ghost={activeFilterCount > 0}
            >
              筛选{activeFilterCount > 0 ? ` (${activeFilterCount})` : ''}
            </Button>
          ) : null}
          {!isMobile || filtersOpen ? (
            <>
              <Input.Search
                allowClear
                placeholder="搜索事项名称、编号、备注"
                style={{ width: isMobile ? '100%' : 240 }}
                onSearch={(value) => {
                  setSearch(value);
                  setPage(1);
                }}
              />
              <Select
                allowClear
                placeholder="收支方向"
                style={{ width: isMobile ? 100 : 130 }}
                value={direction}
                onChange={(value) => {
                  setDirection(value);
                  setPage(1);
                }}
                options={[
                  { value: 'inflow', label: '收入' },
                  { value: 'outflow', label: '支出' },
                ]}
              />
              <Select
                allowClear
                placeholder="状态"
                style={{ width: isMobile ? 100 : 150 }}
                value={state}
                onChange={(value) => {
                  setState(value);
                  setPage(1);
                }}
                options={[
                  { value: 'scheduled', label: '计划中' },
                  { value: 'included_in_opening', label: '已计入期初' },
                  { value: 'cancelled', label: '已取消' },
                ]}
              />
              <Select
                allowClear
                placeholder="事项类型"
                style={{ width: isMobile ? 110 : 150 }}
                value={eventType}
                onChange={(value) => {
                  setEventType(value);
                  setPage(1);
                }}
                options={Object.entries(EVENT_TYPE_LABELS).map(([value, label]) => ({ value, label }))}
              />
              <DatePicker.RangePicker
                showTime={{ format: 'HH:mm' }}
                style={{ width: isMobile ? '100%' : undefined }}
                onChange={(values) => {
                  if (values && values[0] && values[1]) {
                    setRange([values[0].toISOString(), values[1].toISOString()]);
                  } else {
                    setRange(null);
                  }
                  setPage(1);
                }}
              />
              <Button
                onClick={() => {
                  setSearch('');
                  setDirection(undefined);
                  setState(undefined);
                  setEventType(undefined);
                  setRange(null);
                  setPage(1);
                }}
              >
                重置
              </Button>
            </>
          ) : null}
        </Space>

        {isMobile ? (
          <MobileEventList
            items={listQuery.data?.items ?? []}
            loading={listQuery.isLoading}
            onCreate={() => {
              setEditing(null);
              setFormOpen(true);
            }}
            onImport={() => setImportOpen(true)}
            onEdit={(record) => {
              setEditing(record);
              setFormOpen(true);
            }}
            onShowSource={(record) => setSourceId(record.id)}
            onShowRevisions={(record) => setRevisionId(record.id)}
            onCancel={(record) => cancelMutation.mutate(record.id)}
          />
        ) : (
          <Table<CashEvent>
          rowKey="id"
          columns={columns}
          dataSource={listQuery.data?.items ?? []}
          loading={listQuery.isLoading}
          size="middle"
          scroll={{ x: 980 }}
          locale={{
            emptyText: (
              <Empty
                description="还没有收付款事项"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              >
                <Space>
                  <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    onClick={() => {
                      setEditing(null);
                      setFormOpen(true);
                    }}
                  >
                    新增事项
                  </Button>
                  <Button icon={<UploadOutlined />} onClick={() => setImportOpen(true)}>
                    导入 CSV
                  </Button>
                </Space>
              </Empty>
            ),
          }}
          pagination={{
            current: listQuery.data?.meta.page ?? 1,
            pageSize: PAGE_SIZE,
            total: listQuery.data?.meta.total ?? 0,
            showSizeChanger: false,
            onChange: setPage,
            showTotal: (total) => `共 ${total} 条`,
          }}
        />
        )}

        {isMobile && (!listQuery.data || listQuery.data.meta.total > PAGE_SIZE) ? (
          <Pagination
            size="small"
            style={{ marginTop: 16, textAlign: 'center' }}
            current={listQuery.data?.meta.page ?? 1}
            pageSize={PAGE_SIZE}
            total={listQuery.data?.meta.total ?? 0}
            showSizeChanger={false}
            onChange={setPage}
            showTotal={(total) => `共 ${total} 条`}
          />
        ) : null}
      </SectionCard>
        </>
      )}

      <EventFormDrawer
        open={formOpen}
        event={editing}
        onClose={() => {
          setFormOpen(false);
          setEditing(null);
        }}
        onSaved={() => {
          queryClientInstance.invalidateQueries({ queryKey: ['cash-events'] });
          queryClientInstance.invalidateQueries({ queryKey: queryKeys.analysisStale });
          queryClient.invalidateQueries({ queryKey: queryKeys.accountOverview });
        }}
      />

      <RevisionDrawer
        eventId={revisionId}
        open={Boolean(revisionId)}
        onClose={() => setRevisionId(null)}
      />

      <SourceDrawer
        eventId={sourceId}
        open={Boolean(sourceId)}
        onClose={() => {
          setSourceId(null);
          if (focusId) {
            searchParams.delete('focus');
            setSearchParams(searchParams, { replace: true });
          }
        }}
        onEdit={(event) => {
          setEditing(event);
          setFormOpen(true);
        }}
      />

      <SmartInputDrawer
        open={smartOpen}
        onClose={() => setSmartOpen(false)}
        onSaved={() => {
          queryClientInstance.invalidateQueries({ queryKey: ['cash-events'] });
          queryClientInstance.invalidateQueries({ queryKey: queryKeys.analysisStale });
        }}
      />

      <ImportDrawer
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={() => {
          queryClientInstance.invalidateQueries({ queryKey: ['cash-events'] });
          queryClientInstance.invalidateQueries({ queryKey: queryKeys.analysisStale });
        }}
      />
    </div>
  );
}
