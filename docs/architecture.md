# 工 e 稳袋 — 系统架构

## 1. 产品目标

系统只回答一个具体问题：

> 今天到底可以从经营资金中拿多少钱用于家庭，同时不影响未来 7 天已经确认的经营付款。

围绕它形成三条链路：

```
现金流决策
├── 家庭协同决策
└── 经营咨询
```

## 2. 分层结构

```
┌─────────────────────────────────────────────────────────────┐
│  前端（React 19 + Vite 7 + TS strict + Ant Design 6）        │
│  今日决策 / 现金事件 / 情景分析 / 家庭协同 / 经营咨询 / 我的  │
└──────────────────────────┬──────────────────────────────────┘
                           │  /api/v1（同源，Cookie 认证）
┌──────────────────────────┴──────────────────────────────────┐
│  API 层（FastAPI routers）                                   │
│  仅负责参数校验、权限编排、审计写入、响应序列化               │
├─────────────────────────────────────────────────────────────┤
│  服务层（services）                                          │
│  cash_engine      纯函数计算内核（无框架、无数据库依赖）      │
│  cashflow_service 事件读取与窗口统计                         │
│  event_service    事项 CRUD、来源追溯、版本追加               │
│  analysis_service 输入装配、结果持久化、失效标记              │
│  import_service   CSV 解析、映射、校验、确认写入              │
│  household_service 家庭、成员、协同卡、反馈                   │
│  consultation_service 咨询生命周期、字段白名单、结果更正       │
│  ai_service       智能服务统一入口（Provider 抽象）           │
│  providers         ConsultationProvider / SettlementProvider  │
├─────────────────────────────────────────────────────────────┤
│  数据访问层（repositories）                                   │
├─────────────────────────────────────────────────────────────┤
│  模型层（SQLAlchemy 2.x ORM，SQLite + WAL）                    │
└─────────────────────────────────────────────────────────────┘
```

## 3. 核心计算内核

`backend/app/services/cash_engine.py` 是独立、可测试、接近纯函数的业务模块：

* 不依赖 FastAPI `Request`、数据库 `Session`、页面代码
* 输入：`EngineInput(opening_balance_cents, buffer_cents, snapshot_at, events, label)`
* 输出：`EngineResult`（结构化，可 `to_dict()` 序列化）
* 全部金额为整数分；全部时间为 aware UTC

### 3.1 推演窗口

```
[snapshot_at, snapshot_at + 7 days]
```

只有 `state = scheduled` 的事件参与未来现金变化：

| state | 含义 | 是否参与未来变化 |
| --- | --- | --- |
| `scheduled` | 未来需要纳入推演的现金移动 | 是 |
| `included_in_opening` | 已经包含在期初余额中 | 否 |
| `cancelled` | 已取消 | 否 |

### 3.2 余额推演

```
C(t) = opening_balance + Σ Δk
```

* `inflow` → `+amount`
* `outflow` → `-amount`
* 同一时刻没有明确 `sequence_index` 时：**先支出，后收入**（暴露中途缺口）
* 存在 `sequence_index` 时按序号处理

### 3.3 最大可提用金额

```
T = { snapshot_at } ∪ { 每个窗口内事件之后的时点 } ∪ { window_end }
headroom(t) = C(t) - buffer
x* = min_{t ∈ T} headroom(t)
max_withdrawable = max(0, x*)
```

**期初时点必须参与竞争。** 用户是在 `snapshot_at` 这一刻把钱拿走，因此
拿走 `x` 之后必须立刻满足 `opening - x >= buffer`。若期初点不参与，
未来才到账的收入会错误地抬高今天可以拿走的额度。

由此得到三条不可违背的性质：

* 未来收入不能提前提用（期初 600 / 留底 600 + 只有未来收入 → 可提用 0）
* 把收款往后挪不会提高上限
* 提高留底不会提高上限

未来没有事件时，期初点本身就是唯一约束，`max_withdrawable = opening - buffer`；
只有输入确实不完整时才返回 `null`。

输入不完整（缺金额、缺时间、方向/状态非法、事项编号重复、事项未确认）时：

```
max_withdrawable = null
status = INPUT_INCOMPLETE
```

### 3.4 缺口

```
payment_gap = max(0, -minimum_balance)
buffer_gap  = max(0, buffer - minimum_balance)
```

两者分别计算，**不可相加**（留底缺口本身已经包含了付款缺口）。

主回归算例：期初 3600、留底 600，进货款 −1400、结算款 +2000、房租 −1800、
退款 −600。按时最小余额 1800 → 可提用 **1200**；结算延迟 2 天后最小余额 −200 →
`PAYMENT_GAP`，付款缺口 **200**、留底缺口 **800**（不是 1000）。

### 3.5 状态优先级

```
INPUT_INCOMPLETE > PAYMENT_GAP > BELOW_BUFFER > FEASIBLE
```

`OK` 仅为历史数据的读取兼容值；引擎版本 `ENGINE_VERSION = "2.0.0"`，
旧版本结果一律标记 `stale`。

共同约束模式下，顶层 `status`、`status_label` 与两个缺口字段必须来自同一口径：
顶层缺口取所有情景中最严重的那个，文案取状态对应的文案。**不允许**出现
「状态=付款缺口、缺口=0、文案=资金安排可行」这种组合。

### 3.6 情景

| 模式 | 说明 |
| --- | --- |
| `current_plan` | 按当前计划 |
| `delayed` | 所有流入推迟 N 天 |
| `joint` | 分别计算上述两个情景，取最保守上限 |
| `scenarios` | 分别计算多个自定义情景，取最保守上限 |

情景通过 `Scenario` + `ScenarioEventOverride` 表达，**不修改真实现金事件**。

## 3A. 资金增强模块

三个模块装配在确定性账本之上，**不改变**金额计算规则：

| 模块 | 文件 | 职责 |
| --- | --- | --- |
| 日常收付预测 | `forecast_engine.py` | `seasonal_naive` / `weekday_median` / `ses` |
| 结算延期压力 | `settlement_pressure.py` | 手动延迟 + 已完成样本经验延迟 |
| 留底建议 | `reserve_advisor.py` | 7 天窗口内最大累计不利误差 → 经验分位数 |
| 装配与失效 | `enhancement_service.py` | revision、`basis_hash`、留底确认、结果留档 |

强制边界：

* 预测 `provenance=forecast`、`confirmed=false`，永远不能转换为已确认 `CashEvent`
* 预测金额不能加入期初、计划中收入，不能提高 `max_withdrawable`，不能修复 `PAYMENT_GAP`
* API 中 `forecast_affects_withdrawable` 恒为 `false`
* 留底建议不自动生效，不自动降低现有留底；确认必须由用户点击触发

失效链：

```
CashEvent 金额/日期/状态/方向变化 → ledger_revision + 1 ┐
历史数据或结算记录变化        → history_revision + 1 ┘
                              ↓
        basis_hash 变化 → EnhancementRun 标 stale
                       → 留底确认返回 409 STALE_RESERVE_ADVICE
                       → 旧 AnalysisResult 标 stale 并重算
```

## 4. 版本与来源

```
资金结论 → 限制时点事项 → CashEvent → SourceRecord
```

* 任何影响金额计算的字段（`amount_cents` / `scheduled_at` / `direction` / `state`）被修改时：
  保存 `before_json` / `after_json`、`version + 1`、旧 `AnalysisResult` 标记 `stale`、重新计算
* 历史业务记录不提供物理删除，只提供状态置为 `cancelled`
* 每个事项都建立来源记录（含原始内容与 SHA-256 内容指纹）

## 5. 认证与权限

* 密码：Argon2（`pwdlib[argon2]`）
* 令牌：Access + Refresh 双令牌，HttpOnly + SameSite=Lax Cookie
* Refresh Token 落库（`refresh_sessions`），支持轮换与撤销
* 写操作要求自定义安全标头（CSRF 防护）；Bearer 调用不受影响
* 所有资源访问在后端校验 ownership / membership / permission

角色与可见范围：

| 角色 | 可见范围 |
| --- | --- |
| `merchant` | 自己的经营数据、自己创建的家庭、自己发起的咨询 |
| `family_member` | 只可见明确分享给自己的协同卡片 |
| `consultant` | 只可见咨询事项的白名单字段 |

三种身份之间**没有等级关系**，它们只是不同业务身份。

### 5.1 为什么没有 admin

工 e 稳袋**不是**独立银行系统，而是上层银行 / 商户服务 App 中的一个业务模块：

```
银行 App
├─ 首页 / 账户 / 转账
├─ 商户服务
│   └─ 工 e 稳袋        ← 本系统
└─ 其他服务
```

因此平台级用户管理、数据库运维与系统运行面板属于**更上层系统**，本模块不提供：

* 没有 `admin` 业务身份，也没有「管理员 > 普通用户」的等级结构
* 没有 `/api/v1/admin/*` 接口；访问这些路径返回 **404 接口不存在**（而不是 403），
  表示后台确实不存在，而不是「存在但被拦住」
* 运维只通过 `GET /api/v1/health`、systemd 与服务器日志观察，不进入用户界面

咨询人员身份属于上层系统，通过 `scripts/provision_consultant.py` 开通；
未来可替换为 `UpstreamIdentityProvider` 接入上层身份体系（当前不实现 OAuth / 银行 SSO，
但身份层不写死）。

### 5.2 数据边界由后端强制

「一视同仁」不等于数据互相可见：

| 身份 | 不能访问 | 表现 |
| --- | --- | --- |
| 经营者 | 其他经营主体的任何事项 | 列表为空；按 id 直读返回 **404** 而非 403 |
| 家庭成员 | 7 个经营接口 | 一律 **403** |
| 咨询人员 | 6 个经营与家庭接口 | 一律 **403**；队列条目不含任何经营金额字段 |
| 任意身份 | `/api/v1/admin/*` | 一律 **404** |

家庭成员只能读取**已持久化的过滤结果**，不存在时列表为空——不能凭 `cash_event_id`
补全未共享字段。以上全部有测试覆盖（`tests/test_privacy_hardening.py`）。

## 6. 智能服务边界

AI 负责：理解、提取、整理、解释、表达。

确定性引擎负责：金额、时间约束、余额、最大可提用金额、付款缺口、留底缺口、状态。

默认供应商：智谱 GLM。文本任务用 `glm-4.5-air`，视觉任务用 `glm-4.6v`。
详见 `docs/glm-integration.md`。

硬性约束（在 `ai_service.py` 中实现并有测试覆盖）：

* 不得重新计算最大可提用金额或修改引擎结果
* 不得猜测未知金额或时间：文本提取出的金额若无法在原文中找到，判定为猜测并清空；
  截图提取出的金额保留为候选值但**必须**要求用户与截图核对
* 非正数金额（模型看不清时返回 0）一律清空并要求手动填写
* 不得进行信用评分、违约预测、贷款建议或收入预测
* 不得直接写入未确认的现金事件
* 解释文本中出现的、与输入不一致的新金额会被后端过滤

能力：

| 方法 | 说明 |
| --- | --- |
| `extract_cash_event_from_text` | 粘贴文字 → 结构化事项 |
| `extract_cash_event_from_image` | 上传截图 → 结构化事项 |
| `explain_analysis` | 把确定性结论讲清楚 |
| `draft_consultation` | 整理咨询描述（只用白名单字段） |

AI 关闭 / 无 Key / 超时 / HTTP 500 / 非法 JSON / 供应商异常：统一返回
`503 AI_UNAVAILABLE`，提示「智能服务暂时不可用，你仍可以手动完成当前操作」，
核心功能不受影响。图片只在内存与私有目录处理，不进入公开静态资源。

## 7. 外部系统扩展点

`backend/app/services/providers.py` 定义：

* `ConsultationProvider`：经营咨询渠道（当前为 `InternalConsultationProvider`）
* `SettlementProvider`：结算数据渠道（当前为 `InternalSettlementProvider`，暂不接入）

未来接入外部机构时新增一个 Provider 实现并注册即可，**不需要重写咨询系统**。
业务逻辑不绑定任何具体外部机构的 HTTP 地址。

## 8. 部署形态

```
用户浏览器
   │  http://SERVER:18082/
   ▼
FastAPI（单实例、单 worker）
   ├── /api/v1/*        REST 接口
   ├── /assets/*        前端静态资源
   ├── /                前端 index.html
   └── /{path}          SPA 回退（非 /api 路径返回 index.html）
                        /api/* 未命中返回 JSON 404，绝不返回 index.html
```

* SQLite（WAL + `foreign_keys` + `busy_timeout`）
* 代码目录与持久数据目录分离：`~/apps/gong-e-wendai` 与 `~/apps/gong-e-wendai-data`
* `git pull` 不会覆盖数据库
