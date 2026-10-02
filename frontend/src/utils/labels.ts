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
  supplier_payment: '供应商付款',
  rent: '房租',
  payroll: '工资',
  utility: '水电',
  tax: '税费',
  loan_repayment: '还款',
  platform_fee: '平台费用',
  transfer_in: '转入',
  transfer_out: '转出',
  other_inflow: '其他收入',
  other_outflow: '其他支出',
};

export const STATUS_LABELS: Record<AnalysisStatus, string> = {
  OK: '资金安排可行',
  PAYMENT_GAP: '存在付款缺口',
  BELOW_BUFFER: '低于经营留底',
  INPUT_INCOMPLETE: '资料不完整',
};

export const STATUS_TONE: Record<AnalysisStatus, 'ok' | 'warning' | 'danger' | 'info'> = {
  OK: 'ok',
  PAYMENT_GAP: 'danger',
  BELOW_BUFFER: 'warning',
  INPUT_INCOMPLETE: 'info',
};

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
