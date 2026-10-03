/**
 * 资金增强接口：结算延期压力、日常收付参考、留底建议、历史数据与结算记录。
 *
 * 重要边界：历史参考（预测）**不计入**今天可提用金额。
 * 后端字段 ``forecast_affects_withdrawable`` 恒为 false，前端只做展示。
 */

import { get, post } from '@/api/client';

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------
export interface ForecastDaily {
  day: string;
  inflow_cents: number;
  outflow_cents: number;
  net_cents: number;
  inflow_text: string;
  outflow_text: string;
  net_text: string;
  inflow_method: string;
  inflow_method_label: string;
  outflow_method: string;
  outflow_method_label: string;
  training_end: string;
  provenance: string;
  confirmed: boolean;
}

export interface ForecastInfo {
  available: boolean;
  reason_code: string | null;
  message: string | null;
  history_days: number;
  required_days: number;
  training_end: string | null;
  needs_review: boolean;
  needs_review_reason: string | null;
  method_inflow: string | null;
  method_inflow_label: string | null;
  method_outflow: string | null;
  method_outflow_label: string | null;
  daily: ForecastDaily[];
  summary: Record<string, number>;
  diagnostics: Record<string, unknown>;
  disclosure: string;
  /** 校验口径说明（技术细节，只在「查看计算依据」里展示） */
  validation_disclosure?: string;
  forecast_affects_withdrawable: boolean;
}

export interface SettlementScenario {
  id: string;
  label: string;
  delay_days: number;
  basis: string;
  status: string;
  status_label: string;
  max_withdrawable_cents: number | null;
  payment_gap_cents: number;
  buffer_gap_cents: number;
  minimum_balance_cents: number | null;
  end_balance_cents: number | null;
  limiting_timestamp: string | null;
  limiting_event_title: string | null;
  points: Record<string, unknown>[];
  source_refs: string[];
}

export interface SettlementStats {
  channel: string;
  completed_count: number;
  open_count: number;
  overdue_open_count: number;
  status: string;
  quantile: number;
  min_samples: number;
  empirical_delay_days: number | null;
  has_sample: boolean;
  headline: string;
  source_refs: string[];
  open_source_refs: string[];
  disclosure: string;
}

export interface SettlementPressure {
  available: boolean;
  message: string | null;
  manual_delay_days: number;
  targets: {
    id: string;
    title: string;
    amount_cents: number;
    amount_text: string;
    scheduled_at: string | null;
    channel: string;
    source_label: string | null;
    event_type_label: string;
  }[];
  scenarios: SettlementScenario[];
  stats: SettlementStats | null;
  disclosure: string;
}

export interface ReserveAdvice {
  status: string;
  current_reserve_cents: number;
  suggested_reserve_cents: number;
  extra_cents: number;
  empirical_error_cents: number | null;
  block_count: number;
  quantile: number;
  rounding_cents: number;
  min_blocks: number;
  basis_hash: string;
  basis: Record<string, unknown>;
  source_refs: string[];
  disclosure: string;
  requires_confirmation: boolean;
  has_suggestion: boolean;
  suggests_increase: boolean;
  headline: string;
}

export interface EnhancementBaseline {
  status: string;
  status_label: string;
  max_withdrawable_cents: number | null;
  payment_gap_cents: number;
  buffer_gap_cents: number;
  minimum_balance_cents: number | null;
  end_balance_cents: number | null;
  limiting_timestamp: string | null;
  limiting_event_title: string | null;
  buffer_cents: number;
  opening_balance_cents: number;
}

export interface HistorySummary {
  complete_days: number;
  first_day: string | null;
  last_day: string | null;
  total_inflow_cents: number;
  total_outflow_cents: number;
  missing_days: string[];
  continuity_warning: string | null;
  required_days: number;
}

export interface EnhancementOverview {
  merchant_id: string;
  ledger_revision: number;
  history_revision: number;
  basis_hash: string;
  generated_at: string;
  needs_review: boolean;
  baseline: EnhancementBaseline;
  settlement_pressure: SettlementPressure;
  forecast: ForecastInfo;
  reserve_advice: ReserveAdvice;
  history: HistorySummary;
  reserve_changed_since_advice: boolean;
  run_id: string | null;
  forecast_affects_withdrawable: boolean;
}

export interface DailyHistoryRow {
  id: string;
  day: string;
  complete: boolean;
  inflow_cents: number;
  outflow_cents: number;
  net_cents: number;
  source_refs: string[];
  completeness_confirmed: boolean;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface DailyHistoryList {
  items: DailyHistoryRow[];
  complete_days: number;
  first_day: string | null;
  last_day: string | null;
  total_inflow_cents: number;
  total_outflow_cents: number;
  missing_days: string[];
  continuity_warning: string | null;
}

export interface SettlementRecordRow {
  id: string;
  external_key: string;
  channel: string;
  scheduled_at: string;
  actual_at: string | null;
  known_at: string;
  status: string;
  source_ref: string;
  note: string | null;
  delay_days: number | null;
  created_at: string;
}

export interface SettlementRecordList {
  items: SettlementRecordRow[];
  open_count: number;
  completed_count: number;
  cancelled_count: number;
  channels: string[];
}

export interface HistoryImportRow {
  day: string;
  inflow_cents: number;
  outflow_cents: number;
  source_label: string;
  complete: boolean;
}

export interface HistoryImportPreview {
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  duplicate_rows: number;
  missing_row_count: number;
  range_start: string | null;
  range_end: string | null;
  completeness_confirmed: boolean;
  can_confirm: boolean;
  requires_completeness_confirmation: boolean;
  issues: { row: number | null; field: string | null; reason: string; level: string }[];
  sample: HistoryImportRow[];
  preview_token: string;
}

export interface HistoryImportResult {
  created: number;
  updated: number;
  skipped: number;
  filled_zero_days: number;
  history_revision: number;
  message: string;
}

export interface ReserveConfirmResult {
  confirmed_reserve_cents: number;
  previous_reserve_cents: number;
  history_revision: number;
  ledger_revision: number;
  stale_analysis_results: number;
  message: string;
  analysis: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// 客户端
// ---------------------------------------------------------------------------
export const enhancementApi = {
  overview: (params: { delay_days?: number; settlement_channel?: string } = {}) =>
    get<EnhancementOverview>('/enhancements/overview', { params }),

  confirmReserve: (payload: {
    suggested_reserve_cents: number;
    basis_hash: string;
    ledger_revision: number;
    history_revision: number;
    /** 生成该建议的增强运行 id；服务端据此读回原计算参数后复算校验。 */
    run_id?: string | null;
  }) => post<ReserveConfirmResult>('/enhancements/reserve/confirm', payload),

  dailyHistory: () => get<DailyHistoryList>('/history/daily'),

  previewHistory: (payload: {
    rows: HistoryImportRow[];
    completeness_confirmed: boolean;
    fill_missing_days?: boolean;
    file_name?: string;
  }) => post<HistoryImportPreview>('/history/import/preview', payload),

  confirmHistory: (payload: {
    preview_token: string;
    rows: HistoryImportRow[];
    completeness_confirmed: boolean;
    fill_missing_days?: boolean;
  }) => post<HistoryImportResult>('/history/import/confirm', payload),

  settlementRecords: () => get<SettlementRecordList>('/settlement-records'),

  previewSettlementRecords: (payload: { rows: Record<string, unknown>[] }) =>
    post<{
      total_rows: number;
      valid_rows: number;
      invalid_rows: number;
      issues: { row: number; field: string; reason: string }[];
      can_confirm: boolean;
      preview_token: string;
      open_count: number;
      completed_count: number;
    }>('/settlement-records/import/preview', payload),

  confirmSettlementRecords: (payload: {
    preview_token: string;
    rows: Record<string, unknown>[];
  }) =>
    post<{ created: number; updated: number; skipped: number; message: string }>(
      '/settlement-records/import/confirm',
      payload,
    ),
};
