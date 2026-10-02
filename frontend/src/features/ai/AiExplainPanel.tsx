/**
 * 「帮我讲清楚」：把确定性计算结果转成普通经营者容易理解的中文。
 *
 * 边界：
 * * 只把结构化分析结果发给智能服务，不发送完整流水
 * * 结果中的任何金额都来自引擎，智能服务不得新增未知金额
 * * 智能服务不可用时，页面给出明确提示，核心功能不受影响
 */

import { useState } from 'react';
import { Alert, App as AntdApp, Button, Skeleton, Space } from 'antd';
import { BulbOutlined, ReloadOutlined } from '@ant-design/icons';

import { aiApi } from '@/api/ai';
import { AI_FALLBACK_MESSAGE, errorMessage } from '@/api/client';
import { SectionCard } from '@/components/ui';
import type { AnalysisResult } from '@/types';

export default function AiExplainPanel({ result }: { result: AnalysisResult }) {
  const { message } = AntdApp.useApp();
  const [loading, setLoading] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  const explain = async () => {
    setLoading(true);
    setFailed(null);
    try {
      const response = await aiApi.explain({
        max_withdrawable_cents: result.max_withdrawable_cents,
        opening_balance_cents: result.opening_balance_cents,
        buffer_cents: result.buffer_cents,
        status: result.status,
        limiting_timestamp: result.limiting_timestamp,
        limiting_balance_cents: result.limiting_balance_cents,
        limiting_event_title: result.limiting_event_title,
        limiting_reason: result.limiting_reason,
        payment_gap_cents: result.payment_gap_cents,
        buffer_gap_cents: result.buffer_gap_cents,
        window_inflow_cents: result.window_inflow_cents,
        window_outflow_cents: result.window_outflow_cents,
        pending_inflows: result.pending_inflows_at_limit.map((item) => ({
          title: item.title,
          amount_text: item.amount_text,
          scheduled_at: item.scheduled_at,
        })),
      });
      setText(response.explanation);
    } catch (error) {
      const text2 = errorMessage(error);
      setFailed(text2 || AI_FALLBACK_MESSAGE);
      message.warning(text2 || AI_FALLBACK_MESSAGE);
    } finally {
      setLoading(false);
    }
  };

  return (
    <SectionCard
      title={
        <Space size={6}>
          <BulbOutlined aria-hidden="true" />
          <span>帮我讲清楚</span>
        </Space>
      }
      extra={
        text ? (
          <Button size="small" icon={<ReloadOutlined />} onClick={explain} loading={loading}>
            重新解释
          </Button>
        ) : null
      }
    >
      {!text && !loading && !failed ? (
        <>
          <p style={{ color: 'var(--text-secondary)', marginTop: 0 }}>
            把上面的计算结果翻译成更容易理解的说法，方便你和家人沟通。
          </p>
          <Button type="primary" onClick={explain} loading={loading}>
            帮我讲清楚
          </Button>
          <p style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 0, marginTop: 12 }}>
            只会发送结算后的结论字段，不会发送完整经营流水。
          </p>
        </>
      ) : null}

      {loading ? <Skeleton active paragraph={{ rows: 3 }} /> : null}

      {failed ? (
        <Alert
          type="warning"
          showIcon
          message="智能服务暂时不可用，请手动完成当前操作。"
          description="你仍然可以查看上方完整的计算依据，所有金额与结论都来自确定性计算。"
          action={
            <Button size="small" onClick={explain}>
              重试
            </Button>
          }
        />
      ) : null}

      {text ? (
        <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.8 }}>
          {text}
          <p style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 0, marginTop: 12 }}>
            以上说明由智能服务根据计算结果整理，金额与结论以计算结果为准。
          </p>
        </div>
      ) : null}
    </SectionCard>
  );
}
