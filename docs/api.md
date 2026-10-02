# API 参考

* 前缀：`/api/v1`
* 认证：HttpOnly Cookie（`gew_access` / `gew_refresh`）；脚本与移动端可用 `Authorization: Bearer <access_token>`
* 写操作需携带 `X-Requested-With: XMLHttpRequest`（Cookie 认证时的 CSRF 防护）
* 时间：统一 ISO-8601 UTC（`2026-10-03T09:00:00Z`）
* 金额：统一整数分（`120000` = ¥1,200.00）
* 分页：`page` / `page_size`，响应结构 `{ items, meta: { page, page_size, total, total_pages } }`

## 错误格式

所有错误（含 4xx / 5xx）统一返回：

```json
{ "code": "DUPLICATE_CASH_KEY", "message": "该收付款事项已经存在", "details": { "field": "cash_key" } }
```

常用业务错误码：

| code | HTTP | 说明 |
| --- | --- | --- |
| `VALIDATION_FAILED` | 422 | 请求数据不符合要求 |
| `UNAUTHENTICATED` | 401 | 未登录或凭证失效 |
| `FORBIDDEN` | 403 | 无权限 |
| `NOT_FOUND` | 404 | 资源不存在或不属于当前账户 |
| `USERNAME_TAKEN` | 409 | 账户名已存在 |
| `DUPLICATE_CASH_KEY` | 409 | 事项编号重复 |
| `ALREADY_CANCELLED` | 422 | 事项已取消 |
| `CSRF_HEADER_MISSING` | 403 | 缺少安全标头 |
| `IMPORT_HAS_ERRORS` | 422 | 导入文件存在必须处理的问题 |
| `INVALID_STATUS_TRANSITION` | 409 | 咨询状态流转不合法 |
| `AI_UNAVAILABLE` | 503 | 智能服务不可用（不影响核心功能） |
| `NOT_SUPPORTED` / `HARD_DELETE_NOT_SUPPORTED` | 200 | 明确拒绝物理删除 |

## 系统

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查：`{status, database, ai_enabled, version, env}`，不返回任何敏感信息 |

## 认证

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/auth/register` | 注册（经营主体 / 家庭成员 / 咨询人员），返回令牌并写入 HttpOnly Cookie |
| POST | `/auth/login` | 登录 |
| POST | `/auth/refresh` | 刷新会话（刷新令牌轮换） |
| POST | `/auth/logout` | 退出并撤销刷新令牌 |

`admin` 角色不支持自助注册，通过 `scripts/create_admin.py` 创建。

## 当前用户

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/me` | 当前用户、角色、权限、经营档案摘要、智能服务状态 |
| PATCH | `/me` | 更新称呼 / 手机 / 邮箱 |
| POST | `/me/password` | 修改密码（成功后所有会话失效） |
| GET | `/me/sessions` | 当前有效会话数 |

## 经营档案与账户

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/merchant/profile` | 读取经营档案 |
| PATCH | `/merchant/profile` | 更新经营档案与默认留底 |
| GET | `/account/overview` | 首页资金概览（当前可用 / 待结算 / 未来 7 天收入 / 支出分开返回） |
| GET | `/account/snapshots` | 资金时点列表 |
| POST | `/account/snapshots` | 登记当前经营资金 |
| GET | `/account/snapshots/latest` | 最近一次资金时点 |

## 现金事件（收付款事项）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/cash-events` | 列表；支持 `search` / `direction` / `state` / `event_type` / `source_type` / `start` / `end` / 分页 |
| GET | `/cash-events/stats` | 统计（总数、各状态数、计划中收入与支出） |
| GET | `/cash-events/revisions` | 全部修改记录 |
| GET | `/cash-events/{id}` | 详情 |
| POST | `/cash-events` | 新增（`cash_key` 留空自动生成） |
| PATCH | `/cash-events/{id}` | 修改（影响金额计算的字段会生成新版本） |
| POST | `/cash-events/{id}/cancel` | 取消事项（不物理删除） |
| GET | `/cash-events/{id}/revisions` | 版本历史与逐字段对比 |
| GET | `/cash-events/{id}/source` | 来源抽屉数据（含 SourceRecord） |

## CSV 导入

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/imports/csv/preview` | 上传并解析（**不入库**），返回编码、分隔符、映射、校验问题与预览行 |
| POST | `/imports/csv/remap` | 调整字段映射并重新校验 |
| POST | `/imports/csv/commit` | 用户确认后写入事项（建立来源记录） |
| GET | `/imports/csv/templates` | 模板与示例 |
| GET | `/imports/batches` | 导入批次列表 |

## 资金分析与情景

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/analysis/run` | 运行分析（`mode`: `current_plan` / `delayed` / `joint` / `scenarios`） |
| GET | `/analysis/today` | 今日决策（默认不落库，`persist=true` 时落库） |
| GET | `/analysis/stale` | 结果是否已失效 |
| GET | `/analysis/history` | 历史分析记录 |
| GET | `/scenarios` | 情景列表 |
| POST | `/scenarios` | 新建情景 |
| PATCH | `/scenarios/{id}` | 修改情景 |
| DELETE | `/scenarios/{id}` | 删除情景 |
| POST | `/scenarios/compare` | 多情景共同约束比较 |

`AnalysisResult` 关键字段：`status` / `status_label` / `max_withdrawable_cents` /
`limiting_timestamp` / `limiting_balance_cents` / `limiting_event_id` / `limiting_reason` /
`pending_inflows_at_limit` / `payment_gap_cents` / `buffer_gap_cents` / `points`（完整曲线）/
`scenarios`（多情景）/ `engine_version`。

## 家庭协同

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/households/current` | 我的家庭（仅创建者可见邀请码） |
| POST | `/households` | 创建家庭 |
| POST | `/households/invite-code/rotate` | 轮换邀请码 |
| POST | `/households/join` | 使用邀请码申请加入 |
| GET | `/households/members` | 成员列表 |
| POST | `/households/members/{id}/approve` | 确认成员加入 |
| POST | `/households/members/{id}/remove` | 移除成员 |
| GET | `/households/memberships/mine` | 我加入的家庭 |
| GET | `/households/shareable-fields` | 可分享字段白名单与默认关闭的敏感字段 |
| POST | `/household-cards/preview` | 分享预览（所见即所得） |
| POST | `/household-cards` | 分享给家庭 |
| GET | `/household-cards` | 卡片列表（商户看自己分享的，成员看分享给自己的） |
| GET | `/household-cards/{id}` | 卡片详情 |
| PATCH | `/household-cards/{id}` | 更新计划家庭提用金额 |
| DELETE | `/household-cards/{id}` | 撤回卡片 |
| POST | `/household-cards/{id}/read` | 标记已读 |
| POST | `/household-cards/{id}/react` | 同意 / 需要商量 |
| POST | `/household-cards/{id}/comments` | 发表评论 |

## 经营咨询

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/consultations/allowed-fields` | 咨询字段白名单与预览 |
| GET | `/consultations/meta` | 状态与问题类型字典 |
| GET | `/consultations` | 商户的咨询列表 |
| POST | `/consultations` | 发起咨询（`status`: `draft` / `submitted`） |
| GET | `/consultations/{id}` | 详情（含处理时间线） |
| POST | `/consultations/{id}/submit` | 提交咨询 |
| POST | `/consultations/{id}/apply-update` | 商户确认后根据结果更正事项（生成新版本并重算） |
| GET | `/consultations/queue` | 咨询人员队列（`bucket` 过滤） |
| POST | `/consultations/{id}/start` | 受理 / 从待补充回到处理中 |
| POST | `/consultations/{id}/request-info` | 要求补充资料 |
| POST | `/consultations/{id}/verify` | 填写核实结果 |
| POST | `/consultations/{id}/close` | 完成事项 |

## 智能服务

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/ai/status` | 智能服务状态（不返回密钥） |
| POST | `/ai/extract-cash-event` | 智能录入：自然语言 → 结构化事项（**需用户确认后才写入**） |
| POST | `/ai/explain-analysis` | 帮我讲清楚：只接收结构化结论字段 |
| POST | `/ai/draft-consultation` | 咨询描述整理：只使用白名单字段 |

失败统一返回：

```json
{ "code": "AI_UNAVAILABLE", "message": "智能服务暂时不可用，请手动完成当前操作", "details": {} }
```

## 系统管理

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/admin/overview` | 账户 / 商户 / 事项 / 咨询 / 家庭 / 分析结果计数 |
| GET | `/admin/users` | 用户列表（支持搜索与分页） |
| POST | `/admin/users/{id}/status` | 启用 / 停用账户 |
| GET | `/admin/runtime` | 运行状态（数据库可用性、体积、WAL、待重算数、近期异常） |
| GET | `/admin/audit-logs` | 审计日志 |
