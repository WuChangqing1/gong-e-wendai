/** 管理员：运行状态。 */

import { useQuery } from '@tanstack/react-query';
import { Col, Row, Skeleton } from 'antd';

import { adminApi } from '@/api/consultation';
import { healthApi } from '@/api/auth';
import { errorMessage } from '@/api/client';
import { InlineNote, MetricCard, PageHeader, SectionCard, StatusTag } from '@/components/ui';

function formatBytes(bytes: number): string {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export default function AdminRuntimePage() {
  const healthQuery = useQuery({ queryKey: ['health'], queryFn: healthApi.read, refetchInterval: 30_000 });
  const runtimeQuery = useQuery({ queryKey: ['admin', 'runtime'], queryFn: adminApi.runtime });

  if (runtimeQuery.isLoading) return <Skeleton active paragraph={{ rows: 6 }} />;
  if (runtimeQuery.isError)
    return <InlineNote tone="danger">{errorMessage(runtimeQuery.error)}</InlineNote>;

  const data = runtimeQuery.data!;
  const health = healthQuery.data;

  return (
    <div className="gew-stack">
      <PageHeader
        title="运行状态"
        subtitle="服务与依赖的运行情况。本页面不展示数据库路径、密钥或服务器凭证。"
      />

      <Row gutter={[16, 16]}>
        <Col xs={12} lg={6}>
          <SectionCard flat bodyClassName="gew-card__body--tight">
            <MetricCard
              label="服务状态"
              value={health?.status === 'ok' ? '正常' : '异常'}
              tone={health?.status === 'ok' ? 'default' : 'danger'}
              footnote={health?.env}
            />
          </SectionCard>
        </Col>
        <Col xs={12} lg={6}>
          <SectionCard flat bodyClassName="gew-card__body--tight">
            <MetricCard
              label="数据库"
              value={data.database_ok ? '正常' : '不可用'}
              tone={data.database_ok ? 'default' : 'danger'}
              footnote={`WAL ${data.wal_enabled ? '已启用' : '未启用'}`}
            />
          </SectionCard>
        </Col>
        <Col xs={12} lg={6}>
          <SectionCard flat bodyClassName="gew-card__body--tight">
            <MetricCard label="数据文件体积" value={formatBytes(data.database_size_bytes)} />
          </SectionCard>
        </Col>
        <Col xs={12} lg={6}>
          <SectionCard flat bodyClassName="gew-card__body--tight">
            <MetricCard
              label="待重算结果"
              value={String(data.stale_results)}
              footnote="事项变更后等待重新计算"
            />
          </SectionCard>
        </Col>
      </Row>

      <SectionCard title="详细信息">
        <Row gutter={[16, 16]}>
          <Col xs={24} md={8}>
            <span style={{ color: 'var(--text-secondary)' }}>应用版本</span>
            <div className="num">{health?.version ?? '—'}</div>
          </Col>
          <Col xs={24} md={8}>
            <span style={{ color: 'var(--text-secondary)' }}>收付款事项总数</span>
            <div className="num">{data.event_count}</div>
          </Col>
          <Col xs={24} md={8}>
            <span style={{ color: 'var(--text-secondary)' }}>智能服务</span>
            <div>
              {health?.ai_enabled ? (
                <StatusTag tone="ok">已配置</StatusTag>
              ) : (
                <StatusTag tone="neutral">未启用</StatusTag>
              )}
            </div>
          </Col>
        </Row>
      </SectionCard>

      <SectionCard title="近期异常">
        {data.recent_errors.length === 0 ? (
          <InlineNote tone="ok">未发现持续异常。</InlineNote>
        ) : (
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {data.recent_errors.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        )}
      </SectionCard>

      <InlineNote tone="info">
        健康检查接口只返回状态与版本信息，不暴露数据库路径、密钥或任何凭据。
        生产部署为单应用实例、单 worker，以配合 SQLite 的写入特性。
      </InlineNote>
    </div>
  );
}
