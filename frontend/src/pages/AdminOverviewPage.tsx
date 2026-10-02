/** 管理员：系统概览。 */

import { useQuery } from '@tanstack/react-query';
import { Col, Row, Skeleton, Table, Tag, Typography } from 'antd';

import { adminApi } from '@/api/consultation';
import { errorMessage } from '@/api/client';
import { InlineNote, MetricCard, PageHeader, SectionCard, StatusTag } from '@/components/ui';
import { formatDateTime } from '@/utils/datetime';

export default function AdminOverviewPage() {
  const overviewQuery = useQuery({
    queryKey: ['admin', 'overview'],
    queryFn: adminApi.overview,
  });

  const auditQuery = useQuery({
    queryKey: ['admin', 'audit'],
    queryFn: () => adminApi.audit(20),
  });

  if (overviewQuery.isLoading) return <Skeleton active paragraph={{ rows: 6 }} />;
  if (overviewQuery.isError)
    return <InlineNote tone="danger">{errorMessage(overviewQuery.error)}</InlineNote>;

  const data = overviewQuery.data!;

  return (
    <div className="gew-stack">
      <PageHeader
        title="系统概览"
        subtitle="基础运行与管理信息。管理员不具备查看商户经营明细的权限。"
      />

      <Row gutter={[16, 16]}>
        <Col xs={12} lg={6}>
          <SectionCard flat bodyClassName="gew-card__body--tight">
            <MetricCard label="账户总数" value={String(data.users)} footnote={`其中经营主体 ${data.merchants}`} />
          </SectionCard>
        </Col>
        <Col xs={12} lg={6}>
          <SectionCard flat bodyClassName="gew-card__body--tight">
            <MetricCard label="收付款事项" value={String(data.cash_events)} />
          </SectionCard>
        </Col>
        <Col xs={12} lg={6}>
          <SectionCard flat bodyClassName="gew-card__body--tight">
            <MetricCard label="咨询事项" value={String(data.consultations)} />
          </SectionCard>
        </Col>
        <Col xs={12} lg={6}>
          <SectionCard flat bodyClassName="gew-card__body--tight">
            <MetricCard label="家庭" value={String(data.households)} footnote={`分析结果 ${data.analysis_results}`} />
          </SectionCard>
        </Col>
      </Row>

      <SectionCard title="运行信息">
        <Row gutter={[16, 16]}>
          <Col xs={24} md={8}>
            <span style={{ color: 'var(--text-secondary)' }}>应用环境</span>
            <div className="num">{data.app_env}</div>
          </Col>
          <Col xs={24} md={8}>
            <span style={{ color: 'var(--text-secondary)' }}>应用版本</span>
            <div className="num">{data.version}</div>
          </Col>
          <Col xs={24} md={8}>
            <span style={{ color: 'var(--text-secondary)' }}>智能服务</span>
            <div>
              {data.ai_enabled ? <StatusTag tone="ok">已启用</StatusTag> : <StatusTag tone="neutral">未启用</StatusTag>}
            </div>
          </Col>
        </Row>
      </SectionCard>

      <SectionCard title="最近操作记录">
        <Table
          rowKey={(row) => String(row.id)}
          size="small"
          pagination={false}
          dataSource={auditQuery.data ?? []}
          locale={{ emptyText: '暂无记录' }}
          columns={[
            {
              title: '时间',
              dataIndex: 'created_at',
              width: 170,
              render: (value: string) => formatDateTime(value),
            },
            { title: '操作人', dataIndex: 'actor_name', width: 130, render: (value: string | null) => value ?? '—' },
            {
              title: '动作',
              dataIndex: 'action',
              width: 200,
              render: (value: string) => <Tag bordered={false}>{value}</Tag>,
            },
            { title: '资源', dataIndex: 'resource_type', width: 180, render: (value: string | null) => value ?? '—' },
          ]}
        />
      </SectionCard>

      <InlineNote tone="info">
        审计日志不会记录密码、刷新令牌或完整 API Key。管理员第一版只提供基础管理能力。
      </InlineNote>

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        数据来源：本系统数据库。系统不展示任何密钥、数据库路径或服务器凭证。
      </Typography.Text>
    </div>
  );
}
