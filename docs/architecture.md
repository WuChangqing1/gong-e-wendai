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
x* = min( min_{t ∈ 未来受约束时点} C(t) ) - buffer
max_withdrawable = max(0, x*)
```

期初余额不参与 `x*` 的计算，但单独输出 `balance_floor_cents = minimum_balance_cents`，
用于判断「即使不提用也达不到留底」。

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

### 3.5 状态优先级

```
INPUT_INCOMPLETE > PAYMENT_GAP > BELOW_BUFFER > OK
```

### 3.6 情景

| 模式 | 说明 |
| --- | --- |
| `current_plan` | 按当前计划 |
| `delayed` | 所有流入推迟 N 天 |
| `joint` | 分别计算上述两个情景，取最保守上限 |
| `scenarios` | 分别计算多个自定义情景，取最保守上限 |

情景通过 `Scenario` + `ScenarioEventOverride` 表达，**不修改真实现金事件**。

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
| `admin` | 账户、审计与运行状态；**不可见**商户经营明细 |

## 6. 智能服务边界

AI 负责：理解、提取、整理、解释、表达。

确定性引擎负责：金额、时间约束、余额、最大可提用金额、付款缺口、留底缺口、状态。

硬性约束（在 `ai_service.py` 中实现并有测试覆盖）：

* 不得重新计算最大可提用金额或修改引擎结果
* 不得猜测未知金额或时间：提取出的金额若无法在原文中找到，判定为猜测并清空
* 不得进行信用评分、违约预测、贷款建议或收入预测
* 不得直接写入未确认的现金事件
* 解释文本中出现的、与输入不一致的新金额会被后端过滤

AI 关闭 / 无 Key / 超时 / HTTP 500 / 非法 JSON：统一返回 `503 AI_UNAVAILABLE`，
核心功能不受影响。

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
