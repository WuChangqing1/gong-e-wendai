/** 中文标签与状态映射。集中管理，避免各页面文案不一致。 */

import type {
  AnalysisStatus,
  CashEventState,
  ConsultationStatus,
  Direction,
  ReactionType,
  Role,
  SourceType,
} from '@/types';

export const ROLE_LABELS: Record<Role, string> = {
  merchant: '经营者',
  family_member: '家庭成员',
  consultant: '咨询人员',
  admin: '管理员',
};

export const DIRECTION_LABELS: Record<Direction, string> = {
  inflow: '收入',
  outflow: '支出',
};

export const STATE_LABELS: Record<CashEventState, string> = {
  scheduled: '计划中',
  included_in_opening: '已计入期初',
  cancelled: '已取消',
};

export const STATE_HINTS: Record<CashEventState, string> = {
  scheduled: '将纳入未来 7 天资金推演',
  included_in_opening: '该金额已经包含在当前期初余额中，不重复计入未来变化',
  cancelled: '不参与未来计算',
};

export const SOURCE_LABELS: Record<SourceType, string> = {
  manual: '手工录入',
  csv_import: 'CSV 导入',
  ai_extract: '智能录入',
  consultation_update: '咨询更正',
};

export const EVENT_TYPE_LABELS: Record<string, string> = {
  settlement: '结算款',
  sale_receipt: '销售收款',
  supplier_payment: '进货款',
  rent: '房租',
  refund: '退款',
  payroll: '工资',
  utility: '水电',
  tax: '税款',
  loan_repayment: '还款',
  platform_fee: '平台费用',
  transfer_in: '转入',
  transfer_out: '转出',
  other_inflow: '其他收入',
  other_outflow: '其他支出',
};

export const STATUS_LABELS: Record<AnalysisStatus, string> = {
  FEASIBLE: '资金安排可行',
  PAYMENT_GAP: '存在付款缺口',
  BELOW_BUFFER: '低于经营留底',
  INPUT_INCOMPLETE: '资料不完整',
};

export const STATUS_TONE: Record<AnalysisStatus, 'ok' | 'warning' | 'danger' | 'info'> = {
  FEASIBLE: 'ok',
  PAYMENT_GAP: 'danger',
  BELOW_BUFFER: 'warning',
  INPUT_INCOMPLETE: 'info',
};

/**
 * 首屏结论文案：按状态分支，绝不用「可提用金额 0」当作可行证明。
 *
 * PAYMENT_GAP 与 BELOW_BUFFER 下即使金额为 0，也必须明确说明「即使不提用也不足」。
 */
export interface DecisionCopy {
  /** 首屏标题，例如「今日最多可提用」 */
  headline: string;
  /** 首屏金额，缺资料时为 null（页面显示文字而不是 ¥0.00） */
  amountKind: 'withdrawable' | 'payment_gap' | 'buffer_gap' | 'none';
  /** 一句话结论说明 */
  detail: string;
  /** 是否属于「不建议提用」的负面结论 */
  negative: boolean;
}

export function decisionCopy(status: AnalysisStatus): DecisionCopy {
  switch (status) {
    case 'FEASIBLE':
      return {
        headline: '今日最多可提用',
        amountKind: 'withdrawable',
        detail: '按目前已确认的收付款安排，提用该金额后仍能满足经营付款和经营留底要求。',
        negative: false,
      };
    case 'PAYMENT_GAP':
      return {
        headline: '当前存在付款缺口',
        amountKind: 'payment_gap',
        detail: '当前安排下，某个时点的可用经营资金不足以覆盖已确认付款。即使不提用家庭资金，也仍然存在缺口。',
        negative: true,
      };
    case 'BELOW_BUFFER':
      return {
        headline: '低于经营留底',
        amountKind: 'buffer_gap',
        detail: '预计仍能覆盖已确认付款，但某个时点会低于你设置的经营留底。',
        negative: true,
      };
    default:
      return {
        headline: '暂不能计算',
        amountKind: 'none',
        detail: '部分关键金额、时间或状态尚未确认，请先补充资料。',
        negative: false,
      };
  }
}

/** 可提用金额为 0 且状态可行时的专用文案。 */
export const FEASIBLE_ZERO_DETAIL =
  '目前经营付款和留底仍能满足，但没有额外资金适合用于家庭提用。';

export const CONSULTATION_STATUS_LABELS: Record<ConsultationStatus, string> = {
  draft: '草稿',
  submitted: '已提交',
  under_review: '处理中',
  need_more_information: '待补充资料',
  verified: '已核实',
  closed: '已完成',
};

export const CONSULTATION_STATUS_TONE: Record<
  ConsultationStatus,
  'neutral' | 'info' | 'warning' | 'ok'
> = {
  draft: 'neutral',
  submitted: 'info',
  under_review: 'info',
  need_more_information: 'warning',
  verified: 'ok',
  closed: 'ok',
};

export const QUESTION_TYPE_LABELS: Record<string, string> = {
  settlement_time: '到账/结算时间不明确',
  amount_mismatch: '金额与预期不一致',
  missing_arrival: '款项未到账',
  fee_unknown: '手续费/费用不清楚',
  other: '其他经营资金事项',
};

export const REACTION_LABELS: Record<ReactionType, string> = {
  read: '已读',
  agree: '同意',
  discuss: '需要商量',
};

export const CARD_TYPE_LABELS: Record<string, string> = {
  decision: '家庭决策卡',
  risk: '风险提醒卡',
  revision: '更正通知卡',
};

export const SHARE_FIELD_LABELS: Record<string, string> = {
  max_withdrawable: '今日可提用金额',
  planned_amount: '计划家庭提用金额',
  limiting_point: '最紧张时间',
  key_payments: '关键经营付款',
  risk_summary: '风险摘要',
  pending_inflows: '尚未到账的收入',
};

/** 默认不勾选（敏感字段） */
export const SHARE_FIELD_SENSITIVE = new Set([
  'opening_balance',
  'all_transactions',
  'full_csv',
  'consultations',
]);
