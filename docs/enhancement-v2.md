# 增强 v2：现金计算修正、三大资金增强模块与权限加固

本文记录增强 v2 的全部改动、口径与验证方式。所有金额一律为整数分（`int` cents），
时间一律为 aware UTC。

---

## 1. 核心修正：期初时点必须参与约束

### 问题

增强前的 `max_withdrawable` **只**在未来现金流时点上取值：

```python
candidate = minimum_future_balance - buffer_cents   # 错误：期初点没参与
```

这会让**还没到账的收入**抬高今天可以拿走的钱。例如期初 600 元、留底 600 元、
未来只有一笔收款时，旧逻辑会给出「今天可提用 1200 元」——这 1200 元里有一部分
来自还没到账的钱。

### 正确约束

用户是在 `snapshot_at` 这一刻把钱拿走，因此拿走 `x` 之后必须**立刻**满足
`opening - x >= buffer`。约束时点集合为：

```
T = { snapshot_at } ∪ { 每个窗口内事件之后的时点 } ∪ { 窗口结束时点 }

headroom(t) = C(t) - buffer
max_withdrawable = max(0, min_{t ∈ T} headroom(t))
```

由此得到三条不可违背的性质，并且都有回归测试：

| 性质 | 说明 |
| --- | --- |
| 未来收入不能提前提用 | 期初 600 / 留底 600 + 只有未来收入 → 可提用 **0** |
| 收入后移不提高上限 | 把收款往后挪，任一时点余额都不会变高 |
| 提高留底不提高上限 | 留底 600 → 800 时，上限从 1200 降到 1000 |

### 空窗口不再是 `null`

期初与留底都已确认时，**没有未来事项本身就是一个合法约束点**：
`max_withdrawable = opening - buffer`。只有资料确实不完整
（金额缺失、时间为空、方向不合法、事项未确认）才返回 `null`。

---

## 2. 四种分析状态

正式状态统一为：

| 状态 | 含义 | 首屏文案 |
| --- | --- | --- |
| `FEASIBLE` | 付款与留底都能满足 | 今日最多可提用 ¥X |
| `PAYMENT_GAP` | 某时点余额为负 | 当前存在付款缺口 ¥X；暂不建议提用家庭资金 |
| `BELOW_BUFFER` | 能付款但低于留底 | 低于经营留底，还差 ¥X |
| `INPUT_INCOMPLETE` | 资料未确认 | 暂不能计算 |

`OK` 仅作为**历史数据的读取兼容值**保留：`AnalysisStatus.coerce()` 会把旧值
归一为 `FEASIBLE`，历史记录一律保留、不删除。

引擎版本升级为 `ENGINE_VERSION = "2.0.0"`。迁移脚本把旧版本产生的
`analysis_results` 标记为 `is_stale`，不再作为当前决策依据。
`/analysis/history` 会额外返回 `engine_version_current`。

### 共同约束模式的修正

修正前，共同约束的顶层 `status` 来自联合结果，但 `status_label` 与两个缺口字段
来自**第一个情景**。这会产生自相矛盾的响应：

```
status = PAYMENT_GAP
payment_gap_cents = 0
status_label = 资金安排可行      ← 违反规格
```

现在顶层缺口取所有情景中最严重的那个，文案取状态对应的文案，并且联合结果不可行时
明确说明「可提用 0 元只表示没有安全金额，并不代表资金安排已经可行」。

---

## 3. 待结算资金的统计口径

修正前 `pending_settlement_cents == window_inflow_cents`，把所有未来收入都称作
「待结算」，销售收款与转入也被自动归类成结算款。

现在拆成两个独立口径：

| 字段 | 含义 |
| --- | --- |
| `window_inflow_cents` | 未来 7 天**全部**计划收入 |
| `pending_settlement_cents` | 未来处于 `scheduled` 且 `direction=inflow` 且 `event_type=settlement` 的收入 |

---

## 4. 数据模型新增

只做增量迁移，不删除任何既有表与数据。

| 表 | 用途 |
| --- | --- |
| `merchant_analysis_states` | `ledger_revision` / `history_revision` |
| `daily_cash_history` | 已确认完整的自然日，`merchant_id + day` 唯一 |
| `settlement_records` | 预计 / 实际到账配对，`open` / `completed` / `cancelled` |
| `enhancement_runs` | 一次增强计算的参数、依据哈希、结果与失效标记 |
| `reserve_advice_confirmations` | 谁在哪个依据版本上确认了哪个留底 |

### 版本与失效

* 任何正式 `CashEvent` 的金额 / 日期 / 状态 / 方向变化 → `ledger_revision + 1`
* 任何历史数据或结算记录变化 → `history_revision + 1`
* 任一版本或 `basis_hash` 变化 → 既有 `EnhancementRun` 标为 `stale`

### 缺失日期

`daily_cash_history` **只保存已确认完整**的自然日。没有记录的日期代表
「不清楚」，**不代表金额为 0**。只有商户在导入时明确确认
「该日期范围内的数据完整；没有记录的日期代表当天确实没有对应收付」之后，
缺失交易的完整日期才允许聚合为 0。

---

## 5. 三大资金增强模块

### 5.1 日常现金收付预测（`forecast_engine.py`）

三种方法，金额全程整数分：

| 方法 | 定义 | 最少完整日 |
| --- | --- | --- |
| `seasonal_naive` | 上周同一天 | 7 |
| `weekday_median` | 近四周同星期中位数 | 28 |
| `ses` | 简单指数平滑（默认 α = 0.3） | 7 |

* 每步 SES 更新用整数四舍五入到分，与参考内核逐分一致
  （α = 0.5 时：100 → 150 → 175 → 187.50 → 193.75 → 196.88 → 198.44）
* 只处理历史已到账结算收入与历史日常采购；房租、税款、退款等固定义务继续由
  `CashEvent` 进入确定性账本，**不重复计入**
* 方法选择只用前 56 个完整日；留出段**绝不**参与重新选择
* 选中方法在留出段比朴素基线更差时 `needs_review = true`，如实展示，
  不删除「不好看」的样本
* MAE / RMSE 属于诊断指标，默认折叠在「查看计算依据」里
* **不计算 MAPE**（净现金流跨零）

### 5.2 结算延期压力（`settlement_pressure.py`）

* 允许指定「这笔结算再晚 X 天」，默认 2 天，范围 0–30
* 计算「按当前计划」与「手动延迟」两个情景
* 有足够同商户同渠道历史时增加「历史经验延迟」情景
* 只统计 `completed` 样本；`open` **单独计数**，绝不当成 0 天延迟
* 最少 12 笔已完成样本，不足时不生成任何历史经验延迟值，页面只提示
  「历史结算记录还不够，目前只提供手动延期压力分析」
* 经验延迟取已完成样本的 q = 0.9 最近秩分位数，只能称
  「历史已完成结算记录中的经验延迟参考」
* **禁止**表述为「90% 会到账」「90% 安全」「未来概率」「银行 T+1」「未来到账保证」

### 5.3 经营留底建议（`reserve_advisor.py`）

* 计算**每个 7 天窗口内最大累计不利误差**，而不是只看第 7 天终值：
  第 1 天 +200、第 2 天 −200 → 累计 `[200, 0]` → 风险压力 **200**（不是 0）
* `suggested_reserve = max(当前留底, 经验 q=0.9 向上取整到 100 元)`
* **不得自动降低**当前经营留底
* 建议不自动生效，`requires_confirmation` 恒为 `True`
* 描述为透明的经验启发式，**不是** Miller–Orr、置信区间或 VaR/CVaR

---

## 6. 留底确认流程

```
用户点击「确认采用」 → POST /enhancements/reserve/confirm
  → 后端重新校验 ledger_revision / history_revision / basis_hash / 建议值
     ├─ 任一不一致 → 409 STALE_RESERVE_ADVICE
     └─ 一致 → 更新 default_buffer_amount_cents
               + 保存 ReserveAdviceConfirmation
               + revision + 1
               + 旧 AnalysisResult 标 stale
               + EnhancementRun 标 stale
               + 重新运行今日分析
               + 返回最新结果
```

必须在**用户点击事件**中执行；`useEffect`、页面加载与数据刷新都不会触发。

固定回归：留底 600 时上限 1200；确认 800 后上限 1000；延迟情景仍为
`PAYMENT_GAP`，提高留底**不会**消灭资金缺口。

---

## 7. 预测的强制边界

* `ForecastResult.provenance` 恒为 `forecast`，`confirmed` 恒为 `false`
* 预测永远不能转换为已确认的 `CashEvent`
* 预测金额不能加入 `opening_balance`、计划中收入，不能提高 `max_withdrawable`，
  不能修复 `PAYMENT_GAP`
* API 响应中 `forecast_affects_withdrawable` 恒为 `false`
* 回归测试把预测历史放大 10 倍，确定性账本与 `max_withdrawable` 逐分不变

---

## 8. 权限与隐私加固

### 家庭分享白名单

* 分享数据包由**服务端**白名单生成，只有勾选的字段才会出现
* 事项级字段（`event_title` / `event_amount_cents` / `event_scheduled_at` /
  `event_version`）必须显式勾选 `key_payments` 或 `revision_summary` 才生成
* 未勾选 `key_payments` 时，`limiting_event_title` 也不生成
* 未勾选 `limiting_point` 时，`end_balance_cents` 不生成
* 事项变更卡的标题在未共享事项字段时使用通用标题
* 接收端只能读取**持久化后的过滤 payload**，不能根据 `cash_event_id` 补全
* 这是后端 JSON 层面的事实，不是前端隐藏

### 咨询白名单

允许：事项编号、事项名称、事项类型、金额、预计时间、状态、来源摘要、用户问题、版本。

禁止：家庭信息、家庭评论、完整经营余额、经营留底、今日可提用金额、完整现金流、
预测历史、预测结果、留底算法依据、其他无关流水。

### 角色收紧

* 公开注册**只允许** `merchant` 与 `family_member`
* `consultant` 由安全脚本 `scripts/provision_consultant.py` 开通
* 本系统不存在 `admin` 身份（V3 起已移除 Admin 产品模块）

---

## 9. 接口清单

| 方法与路径 | 说明 |
| --- | --- |
| `GET /enhancements/overview` | baseline、结算压力、预测、留底建议、两个 revision、basis_hash |
| `POST /enhancements/reserve/confirm` | 确认留底（仅用户点击触发） |
| `GET /history/daily` | 已确认完整的历史经营数据 |
| `POST /history/import/preview` | 导入预览（含完整性确认检查） |
| `POST /history/import/confirm` | 确认导入 |
| `GET /settlement-records` | 结算记录 |
| `POST /settlement-records/import/preview` | 结算记录导入预览 |
| `POST /settlement-records/import/confirm` | 确认导入 |
| `POST /ai/extract-cash-event-from-image` | 截图智能录入 |

权限：`history`、`enhancements`、留底建议**只允许 merchant**。
家庭成员与咨询人员一律 403；所有查询都以调用者自己的 `merchant_id` 为根。

---

## 10. 验证

| 项目 | 结果 |
| --- | --- |
| 后端 pytest | 见 `docs/acceptance-tests.md` |
| 参考一致性（`test_reference_parity.py`） | 54 项，逐分一致 |
| 增强模块（`test_enhancement.py`） | 53 项 |
| 隐私加固（`test_privacy_hardening.py`） | 14 项 |
| GLM 接入（`test_glm_integration.py`） | 26 项 |

参考增强包保存在 `reference/wendai-enhancements/`，只作为**计算规范、算法参考与
人工标准答案来源**，不构成第二套线上金额计算源。
