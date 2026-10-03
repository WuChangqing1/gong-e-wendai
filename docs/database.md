# 数据库设计

* 数据库：SQLite（生产启用 WAL）
* 时间：全部以 **UTC** 存储（`UTCDateTime` 类型装饰器），接口序列化为带 `Z` 的 ISO-8601
* 金额：全部为 `INTEGER` 分
* 主键：UUID 字符串（`String(36)`）
* 所有业务表带 `created_at` / `updated_at`，需要时带 `created_by`

## 表清单（21 张）

### 用户与认证

| 表 | 说明 | 关键字段 |
| --- | --- | --- |
| `users` | 账户 | `username`（唯一）、`display_name`、`password_hash`（Argon2）、`status`、`last_login_at` |
| `user_roles` | 多角色 | `user_id` + `role`（`merchant` / `family_member` / `consultant` / `admin`），`(user_id, role)` 唯一 |
| `refresh_sessions` | 刷新令牌会话 | `token_hash`（唯一，SHA-256）、`expires_at`、`revoked_at`、`rotated_to` |

### 商户与经营账户

| 表 | 说明 | 关键字段 |
| --- | --- | --- |
| `merchant_profiles` | 经营档案 | `user_id`（唯一）、`business_name`、`business_type`、`default_currency=CNY`、`timezone=Asia/Shanghai`、`default_buffer_amount_cents`、`account_name` |
| `business_account_snapshots` | 经营资金时点 | `opening_balance_cents`、`pending_settlement_cents`、`snapshot_at`、`source_type` |

### 现金事件与追溯

| 表 | 说明 | 关键字段 |
| --- | --- | --- |
| `source_records` | 来源记录 | `source_type`、`file_name`、`stored_file_name`、`row_number`、`raw_content`、`content_hash`、`import_batch_id` |
| `cash_events` | 现金事件 | `cash_key`、`title`、`amount_cents`、`direction`、`scheduled_at`、`state`、`source_record_id`、`current_version`、`confirmed` |
| `cash_event_revisions` | 版本历史（追加写） | `version`、`before_json`、`after_json`、`changed_fields`、`material`、`change_reason`、`changed_by_name`、`changed_at` |
| `import_batches` | CSV 导入批次 | `file_name`、`content_hash`、`encoding`、`delimiter`、`mapping`、`parsed_rows`、`issues`、`status`、`committed_at` |

唯一约束：

* `cash_events`：`(merchant_id, cash_key)` 唯一 —— 同一商户范围内事项编号不允许重复
* `cash_event_revisions`：`(cash_event_id, version)` 唯一

### 情景与分析

| 表 | 说明 | 关键字段 |
| --- | --- | --- |
| `scenarios` | 情景 | `name`、`kind`、`description` |
| `scenario_event_overrides` | 情景对事项的假设覆盖 | `scenario_id` + `cash_event_id` 唯一，`scheduled_at_override`、`amount_cents_override`、`direction_override`、`state_override` |
| `analysis_results` | 分析结果（可追溯） | `status`、`max_withdrawable_cents`、`opening_balance_cents`、`buffer_cents`、`snapshot_at`、`window_end_at`、`limiting_*`、`payment_gap_cents`、`buffer_gap_cents`、`payload`（完整曲线）、`events_version_hash`、`is_stale`、`stale_reason`、`engine_version` |

### 资金增强（引擎 2.0.0 新增）

| 表 | 说明 | 关键字段 |
| --- | --- | --- |
| `merchant_analysis_states` | 每个商户的分析状态版本 | `merchant_id`（主键）、`ledger_revision`、`history_revision` |
| `daily_cash_history` | 已确认完整的自然日 | `day`（`YYYY-MM-DD`，北京时间）、`complete`、`inflow_cents`、`outflow_cents`、`source_refs`、`import_batch_id`、`completeness_confirmed` |
| `settlement_records` | 结算到账配对 | `external_key`、`channel`、`scheduled_at`、`actual_at`、`known_at`、`status`（`open` / `completed` / `cancelled`）、`source_ref` |
| `enhancement_runs` | 一次增强计算的留档 | `ledger_revision`、`history_revision`、`basis_hash`、`parameters_json`、`result_json`、`is_stale`、`stale_reason` |
| `reserve_advice_confirmations` | 留底建议确认记录 | `previous_reserve_cents`、`suggested_reserve_cents`、`confirmed_reserve_cents`、`basis_hash`、两个 revision、`basis_json`、`confirmed_by`、`confirmed_at` |

唯一约束：

* `daily_cash_history`：`(merchant_id, day)` 唯一
* `settlement_records`：`(merchant_id, external_key)` 唯一

**缺失日期约定**：`daily_cash_history` 只保存已确认完整的自然日。
没有记录的日期**不代表金额为 0**，而是「不清楚」。只有 `completeness_confirmed`
为真（商户在导入时明确确认该日期范围数据完整）之后，缺失交易的完整日期才允许
聚合为 0。

**版本与失效**：任何正式 `CashEvent` 的金额 / 日期 / 状态 / 方向变化会让
`ledger_revision` + 1；任何历史数据或结算记录变化会让 `history_revision` + 1。
任一版本或 `basis_hash` 变化都会让既有 `EnhancementRun` 标为 `stale`，
留底确认会返回 409 `STALE_RESERVE_ADVICE`。

### 家庭协同

| 表 | 说明 | 关键字段 |
| --- | --- | --- |
| `households` | 家庭 | `name`、`owner_id`、`merchant_id`、`invite_code`（唯一）、`invite_code_active` |
| `household_memberships` | 成员关系 | `(household_id, user_id)` 唯一，`role`、`status`（`pending` / `active` / `removed`）、`joined_at` |
| `household_cards` | 协同卡 | `card_type`（`decision` / `risk` / `revision`）、`payload`、`shared_fields`、`system_max_withdrawable_cents`、`planned_household_amount_cents` |
| `household_card_recipients` | 收件人 | `(card_id, user_id)` 唯一，`is_read`、`reaction`（`read` / `agree` / `discuss`） |
| `household_card_reactions` | 反馈历史（追加写） | `reaction` |
| `household_card_comments` | 卡片评论 | `content`（仅属于具体卡片，不做实时聊天） |

### 经营咨询

| 表 | 说明 | 关键字段 |
| --- | --- | --- |
| `consultation_cases` | 咨询事项 | `case_no`（唯一）、`cash_event_id`、`cash_event_version`、`question_type`、`question`、`shared_fields`（白名单字段）、`status`、`resolution_summary`、`resolution_fields`、`provider_key` |
| `consultation_updates` | 处理时间线 | `action`、`from_status`、`to_status`、`content`、`actor_name`、`actor_role` |

状态机：

```
draft → submitted → under_review → need_more_information ⇄ under_review
                                 → verified → closed
```

### 审计

| 表 | 说明 | 关键字段 |
| --- | --- | --- |
| `audit_logs` | 重要操作记录 | `actor_id`、`actor_name`、`action`、`resource_type`、`resource_id`、`merchant_id`、`metadata_json`、`ip_address`、`created_at` |

记录范围：登录 / 登录失败 / 注册 / 刷新 / 退出 / 改密 / 登记资金 / 创建与修改与取消事项 /
CSV 预览与导入 / 运行分析 / 创建情景 / 创建家庭 / 成员申请与确认与移除 / 分享卡片 /
创建与提交咨询 / 咨询状态变更 / 更正事项 / 管理动作 / 智能服务调用。

**禁止记录**：密码、Refresh Token、完整 API Key（写入前统一走 `redact()` 脱敏）。

## 索引策略

* 高频查询走组合索引：`cash_events(merchant_id, scheduled_at)`、
  `analysis_results(merchant_id, created_at)`、`consultation_cases(status, updated_at)`
* 唯一约束：`cash_events(merchant_id, cash_key)`、`households(invite_code)`、
  `consultation_cases(case_no)`、`users(username)`
* 外键全部启用（`PRAGMA foreign_keys=ON`），删除策略以 `CASCADE` 为主，
  引用型关系使用 `SET NULL` 保留历史

## 迁移

```bash
cd backend
python -m alembic upgrade head      # 应用迁移
python -m alembic check             # 校验模型与数据库是否一致
python -m alembic revision --autogenerate -m "描述"
```

* 初始迁移 `0053fb3fa9b7_initial_schema.py`（20 张表）
* `0ee06e0f0f90_add_import_batches.py`（导入批次表）
* `6e784eb94252_add_enhancement_history_and_settlement_.py`（增强 v2：5 张新表 +
  回填每个商户的分析状态行 + 把 2.0.0 之前的分析结果标为 `is_stale`）
* `alembic check` 必须无差异
* 生产首次启动只运行迁移，**不生成任何种子数据**

### 升级到引擎 2.0.0 的迁移行为

```bash
# 迁移前必须备份
python scripts/backup_db.py
python -m alembic upgrade head
```

该迁移**只做增量新增**，不删除、不重建任何既有表与数据：

1. 新增 5 张表
2. 为每个已有商户回填一行 `merchant_analysis_states`（两个 revision 从 1 开始）
3. 把旧引擎版本产生的 `analysis_results` 标记为 `is_stale`，`stale_reason`
   写明「计算引擎已升级到 2.0.0：期初余额已纳入提用上限约束，请重新计算」

旧结果全部保留，只是不再作为当前决策依据。旧用户、旧 `CashEvent`、旧家庭卡、
旧咨询记录均不受影响。

## 备份

```bash
python scripts/backup_db.py            # 生成一致性快照到 data/backups/
python scripts/backup_db.py --keep 14  # 同时清理 14 天前的备份
```

备份使用 SQLite 在线备份 API（`Connection.backup`），在 WAL 模式下也能得到一致快照。
