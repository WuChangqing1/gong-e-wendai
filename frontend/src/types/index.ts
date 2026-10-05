/** 全局领域类型定义。 */

/**
 * 业务身份。
 *
 * 三者之间**没有等级关系**：工 e 稳袋是上层银行 / 商户服务 App 中的一个业务模块，
 * 平台级用户与运维管理由上层系统承担，因此本系统不存在 admin 身份。
 */
export type Role = 'merchant' | 'family_member' | 'consultant';

export type Direction = 'inflow' | 'outflow';
export type CashEventState = 'scheduled' | 'included_in_opening' | 'cancelled';
export type SourceType = 'manual' | 'csv_import' | 'ai_extract' | 'consultation_update';
export type AnalysisStatus = 'FEASIBLE' | 'PAYMENT_GAP' | 'BELOW_BUFFER' | 'INPUT_INCOMPLETE';
export type AnalysisMode = 'current_plan' | 'delayed' | 'joint' | 'scenarios';

export interface PageMeta {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface Page<T> {
  items: T[];
  meta: PageMeta;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface MerchantBrief {
  id: string;
  business_name: string;
  business_type: string;
  default_currency: string;
  timezone: string;
  default_buffer_amount_cents: number;
}

export interface UserPublic {
  id: string;
  username: string;
  display_name: string;
  status: string;
  roles: Role[];
  created_at: string;
  last_login_at: string | null;
}

export interface UserMe extends UserPublic {
  phone: string | null;
  email: string | null;
  merchant: MerchantBrief | null;
  permissions: string[];
  ai_enabled: boolean;
}

export interface MerchantProfile {
  id: string;
  user_id: string;
  business_name: string;
  business_type: string;
  contact_name: string | null;
  phone_optional: string | null;
  default_currency: string;
  timezone: string;
  default_buffer_amount_cents: number;
  account_name: string;
  account_masked_no: string | null;
  created_at: string;
  updated_at: string;
}

export interface AccountSnapshot {
  id: string;
  opening_balance_cents: number;
  pending_settlement_cents: number;
  snapshot_at: string;
  currency: string;
  source_type: string;
  note: string | null;
  created_at: string;
}

export interface AccountOverview {
  opening_balance_cents: number;
  pending_settlement_cents: number;
  window_inflow_cents: number;
  window_outflow_cents: number;
  buffer_cents: number;
  currency: string;
  snapshot_at: string | null;
  snapshot_source: string | null;
  has_snapshot: boolean;
}

export interface SourceSummary {
  source_type: SourceType;
  source_label: string | null;
  file_name: string | null;
  row_number: number | null;
  created_at: string | null;
}

export interface SourceRecord {
  id: string;
  source_type: SourceType;
  file_name: string | null;
  row_number: number | null;
  raw_content: string | null;
  content_hash: string | null;
  import_batch_id: string | null;
  created_at: string;
  created_by: string | null;
}

export interface CashEvent {
  id: string;
  merchant_id: string;
  cash_key: string;
  event_type: string;
  title: string;
  amount_cents: number;
  direction: Direction;
  scheduled_at: string;
  state: CashEventState;
  source_type: SourceType;
  source_label: string | null;
  source_record_id: string | null;
  note: string | null;
  sequence_index_optional: number | null;
  confirmed: boolean;
  current_version: number;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  source: SourceSummary | null;
}

export interface CashEventDetail extends CashEvent {
  source_record: SourceRecord | null;
}

export interface CashEventStats {
  total: number;
  scheduled: number;
  included_in_opening: number;
  cancelled: number;
  inflow_cents: number;
  outflow_cents: number;
}

export interface FieldChange {
  field: string;
  label: string;
  before: unknown;
  after: unknown;
  before_text: string | null;
  after_text: string | null;
  material: boolean;
}

export interface RevisionDiff {
  version: number;
  changed_fields: string[];
  material: boolean;
  change_reason: string | null;
  changed_by_name: string | null;
  changed_at: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  changes: FieldChange[];
}

export interface RevisionWithEvent {
  revision: RevisionDiff & { id: string; cash_event_id: string };
  event_id: string;
  event_title: string;
  cash_key: string;
}

export interface BalancePoint {
  timestamp: string;
  balance_cents: number;
  balance_text: string;
  event_id: string | null;
  event_title: string;
  delta_cents: number;
  delta_text: string;
  direction: Direction | null;
  state: CashEventState | null;
  sequence_index: number | null;
  is_opening: boolean;
}

export interface PendingInflow {
  event_id: string;
  title: string;
  amount_cents: number;
  amount_text: string;
  scheduled_at: string;
}

export interface ScenarioCurve {
  label: string;
  kind: string;
  status: AnalysisStatus;
  status_label: string;
  max_withdrawable_cents: number | null;
  limiting_timestamp: string | null;
  limiting_balance_cents: number | null;
  limiting_event_id: string | null;
  limiting_event_title: string | null;
  limiting_reason: string;
  payment_gap_cents: number;
  buffer_gap_cents: number;
  end_balance_cents: number | null;
  points: BalancePoint[];
  pending_inflows_at_limit: PendingInflow[];
  window_inflow_cents: number;
  window_outflow_cents: number;
}

export interface AnalysisResult {
  id: string | null;
  merchant_id: string;
  mode: AnalysisMode;
  mode_label: string;
  status: AnalysisStatus;
  status_label: string;
  max_withdrawable_cents: number | null;
  binding_label: string | null;
  opening_balance_cents: number;
  buffer_cents: number;
  currency: string;
  snapshot_at: string;
  window_end_at: string;
  limiting_timestamp: string | null;
  limiting_balance_cents: number | null;
  limiting_event_id: string | null;
  limiting_event_title: string | null;
  limiting_reason: string;
  pending_inflows_at_limit: PendingInflow[];
  payment_gap_cents: number;
  buffer_gap_cents: number;
  minimum_balance_cents: number | null;
  balance_floor_cents: number | null;
  end_balance_cents: number | null;
  opening_covers_buffer: boolean;
  window_inflow_cents: number;
  window_outflow_cents: number;
  pending_settlement_cents: number;
  scenarios: ScenarioCurve[];
  points: BalancePoint[];
  validation_errors: { event_id?: string; field?: string; reason: string }[];
  excluded_event_ids: string[];
  engine_version: string;
  is_stale: boolean;
  generated_at: string;
}

export interface ScenarioOverride {
  cash_event_id: string;
  scheduled_at: string | null;
  amount_cents: number | null;
  direction: Direction | null;
  state: CashEventState | null;
  note: string | null;
}

export interface Scenario {
  id: string;
  merchant_id: string;
  name: string;
  kind: string;
  description: string | null;
  is_primary: boolean;
  created_at: string;
  updated_at: string;
  overrides: ScenarioOverride[];
}

export interface StaleStatus {
  is_stale: boolean;
  last_generated_at: string | null;
  stale_reason: string | null;
  max_withdrawable_cents: number | null;
}

/** 未来 7 天窗口聚合（图表数据源）。 */
export interface DailyTerm {
  day: string;
  inflow_cents: number;
  outflow_cents: number;
  net_cents: number;
  closing_balance_cents: number;
  event_count: number;
}

export interface CategoryTerm {
  event_type: string;
  label: string;
  direction: Direction;
  amount_cents: number;
  event_count: number;
  share_ratio: number;
}

export interface ArrivalTerm {
  day: string;
  amount_cents: number;
  event_count: number;
  titles: string[];
}

export interface WindowSummary {
  /** 本次聚合使用的口径，与顶部结论同一个来源 */
  mode: AnalysisMode;
  delay_days: number;
  status: AnalysisStatus | null;
  status_label: string | null;
  binding_scenario_index: number | null;
  binding_label: string | null;
  window_start: string;
  window_end: string;
  window_days: number;
  opening_balance_cents: number;
  closing_balance_cents: number;
  buffer_cents: number;
  scheduled_inflow_cents: number;
  scheduled_outflow_cents: number;
  net_change_cents: number;
  daily_terms: DailyTerm[];
  category_terms: CategoryTerm[];
  arrival_terms: ArrivalTerm[];
  event_count: number;
}

export interface HealthStatus {
  status: string;
  database: string;
  ai_enabled: boolean;
  version: string;
  env: string;
}

/** CSV 导入 */
export interface ImportIssue {
  row_number: number | null;
  field: string | null;
  code: string;
  message: string;
  severity: 'error' | 'warning';
}

export interface ImportPreviewRow {
  row_number: number;
  cash_key: string | null;
  title: string | null;
  direction: Direction | null;
  amount_cents: number | null;
  scheduled_at: string | null;
  state: CashEventState | null;
  source_label: string | null;
  note: string | null;
  valid: boolean;
  issues: ImportIssue[];
}

export interface ImportPreview {
  batch_id: string;
  file_name: string;
  file_type: 'transaction' | 'payment_plan';
  encoding: string;
  delimiter: string;
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  duplicate_rows: number;
  columns: string[];
  mapping: Record<string, string>;
  unmapped_columns: string[];
  missing_columns: string[];
  rows: ImportPreviewRow[];
  issues: ImportIssue[];
  can_commit: boolean;
}

export interface ImportCommitResult {
  batch_id: string;
  created: number;
  skipped: number;
  failed: number;
  issues: ImportIssue[];
  created_event_ids: string[];
}

/** 家庭协同 */
export type CardType = 'decision' | 'risk' | 'revision';
export type ReactionType = 'read' | 'agree' | 'discuss';

export interface HouseholdMember {
  membership_id: string;
  user_id: string;
  display_name: string;
  username: string;
  role: string;
  relation_label: string | null;
  status: 'pending' | 'active' | 'removed';
  joined_at: string | null;
}

export interface Household {
  id: string;
  name: string;
  owner_id: string;
  merchant_id: string;
  invite_code: string | null;
  invite_code_active: boolean;
  created_at: string;
  members: HouseholdMember[];
}

export interface CardRecipient {
  user_id: string;
  display_name: string;
  is_read: boolean;
  read_at: string | null;
  reaction: ReactionType | null;
  reacted_at: string | null;
}

export interface CardComment {
  id: string;
  user_id: string;
  display_name: string;
  content: string;
  created_at: string;
}

export interface HouseholdCard {
  id: string;
  household_id: string;
  card_type: CardType;
  title: string;
  summary: string | null;
  payload: Record<string, unknown>;
  shared_fields: string[];
  system_max_withdrawable_cents: number | null;
  planned_household_amount_cents: number | null;
  cash_event_id: string | null;
  created_at: string;
  updated_at: string;
  recipients: CardRecipient[];
  comments: CardComment[];
  my_reaction: ReactionType | null;
  is_read: boolean;
}

/** 经营咨询 */
export type ConsultationStatus =
  | 'draft'
  | 'submitted'
  | 'under_review'
  | 'need_more_information'
  | 'verified'
  | 'closed';

export interface ConsultationUpdateEntry {
  id: string;
  actor_name: string | null;
  actor_role: string | null;
  action: string;
  from_status: string | null;
  to_status: string | null;
  content: string | null;
  created_at: string;
}

export interface ConsultationCase {
  id: string;
  case_no: string;
  merchant_id: string;
  cash_event_id: string | null;
  cash_event_version: number | null;
  question_type: string;
  question: string;
  ai_draft: string | null;
  shared_fields: Record<string, unknown>;
  allowed_field_names: string[];
  status: ConsultationStatus;
  resolution_summary: string | null;
  resolution_fields: Record<string, unknown>;
  submitted_at: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
  updates: ConsultationUpdateEntry[];
}
