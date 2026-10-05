# 定稿前收尾优化 · 修改前基线

本文件记录本轮（`fix/final-polish-consistency`）**动手之前**的真实状态，
作为对照与回滚依据。所有数字都是本机实跑得到的。

## 1. Git

| 项目 | 值 |
| --- | --- |
| 起始 commit | `590c932`（`main`，与 `origin/main`、生产一致） |
| 本轮分支 | `fix/final-polish-consistency` |
| remote | `https://github.com/WuChangqing1/gong-e-wendai.git`（Private） |
| 工作区 | 干净（无未提交修改、无 stash） |

## 2. 测试基线

| 层次 | 结果 |
| --- | --- |
| Backend `pytest` | **514 passed**（退出码 0） |
| Frontend `typecheck` | 通过 |
| Frontend `lint` | 通过（`--max-warnings 0`） |
| Frontend `vitest` | **43 passed** |
| Frontend `build` | 通过 |
| Playwright E2E | **43 passed / 1 skipped** |

> E2E 说明：首次基线运行时本地后端进程已退出，导致移动端用例整批失败；
> 重启后端后重跑为 43 passed / 1 skipped。其中
> 「今日决策页展示每日收支与到账分布图」在全量并发运行时偶发超时，
> 单独重跑通过，与本轮修改无关（本地 `retries: 0`）。

## 3. 生产现状（本轮开始前）

| 项目 | 值 |
| --- | --- |
| 生产 commit | `590c932` |
| 服务 | systemd user service `gong-e-wendai` = `active`，`NRestarts=0` |
| 应用监听 | `127.0.0.1:18089` |
| 对外入口 | `https://ccqspace.site/wendai/` |
| 账号 | `wangzhanggui` / `wangtaitai` / `zixunxiaoli` + 已停用的 `xitongguanli` |
| 经营留底 | **1200 元**（经营者本人在页面上确认过建议留底；数据建设脚本已不再覆盖） |
| 当前口径 | 可提用 600 元、最紧时点 1800 元；延迟 2 天为 `PAYMENT_GAP`，付款缺口 200 / 留底缺口 1400 |

## 4. 本轮要解决的已知问题（动手前已确认存在）

| 编号 | 问题 | 影响 |
| --- | --- | --- |
| S1 | `EnhancementPanel` 顶部「资金规划」固定读 `baseline`，却写「当前结论」 | 切到「到账延迟 / 共同约束」后，顶部显示缺口、下面却说资金可行 |
| S2 | `AnalysisChartsPanel` 请求 `/analysis/window-summary` 不带口径 | 「本期资金概览」与图表始终是按当前计划的数据 |
| S3 | `window-summary` 缓存 key 不含口径 | 切换模式后可能复用上一种模式的数据 |
| S4 | 8 条预置咨询按 `index % len(events)` 关联事项 | 问题问结算、事项却是采购 |
| S5 | `risk_summary` 文案里带「合计 ¥X」与「最紧张时点余额 ¥Y」 | 只勾「风险摘要」也会泄露金额 |
| S6 | 勾选「尚未到账的收入」但没有数据时生成空列表，仍可分享 | 无意义分享、空区域 |
| S7 | 分享预览底部无条件显示「最紧张时间」 | 绕过勾选白名单 |
| S8 | `_key_payments()` / `_key_payment_title()` 固定取 `scenarios[0]` | 共同约束绑定的可能是延迟情景，分享出去的却是按当前计划的关键付款 |
| S9 | `SHARED_FIELD_OUTPUTS["revision_summary"] = ()` | 更正卡号称有「事项变更摘要」，实际没有任何变化 |
| S10 | 两张预置更正卡都关联同一个结算事项 | 与卡片标题不符 |
| S11 | `/family/cards` 无角色守卫、`POST /households/join` 不校验身份 | 咨询人员也能进入家庭协同 |
| S12 | 咨询详情直接 `Object.entries(resolution_fields)` 当标签 | 界面出现 `amount_cents` 等内部字段名 |
| S13 | 预置风险卡标题写「结算延迟风险提醒」，payload 却是可行状态 | 标题与内容矛盾 |
| S14 | 预置结算记录 `channel = 平台结算`，事项 `source_label = 平台结算单` | 延期压力可能显示「该渠道已完成 0 笔」 |

## 5. 本轮绝不修改

* Cash Engine 数学算法（`cash_engine.py` 的金额/状态规则）
* 600 元留底的核心回归（1200 元只作为独立验收参数）
* 三个正式账号、生产既有业务数据
* V3 架构：无 Admin、PC/Mobile 单套代码、GLM 保留

---

## 6. 收口结果

### 6.1 测试

| 层次 | 收口前 | 收口后 |
| --- | --- | --- |
| Backend `pytest` | 514 | **581 passed** |
| Frontend `typecheck` / `lint` / `build` | 通过 | 通过 |
| Frontend `vitest` | 43 | **46 passed** |
| Playwright E2E | 43 通过 / 1 跳过 | **47 通过 / 1 跳过** |
| Secret Check | 通过 | 通过 |

### 6.2 修复对照

| 编号 | 处理 |
| --- | --- |
| S1 / S2 / S3 | `window-summary` 支持 `mode` / `delay_days` / `scenario_ids`，完全复用引擎输入（共同约束取绑定情景）；`TodayPage` 成为本页口径唯一状态源，`EnhancementPanel` 的「资金规划」改读当前口径结果；Query Key 按口径区分 |
| S4 | `CONSULTATION_SPECS` 改为 dataclass + `event_title` 语义绑定，删除按位置对应的写法；结论逐条对应事项类型 |
| S5 | `risk_summary` 只表达状态语义，永不出现金额 |
| S6 | 勾了「尚未到账的收入」但没有内容 → 预览显示「暂无」、按钮禁用并说明原因，后端 422 `NO_PENDING_INFLOW_TO_SHARE` |
| S7 | 删除预览底部无条件显示的「最紧张时间」，预览只渲染白名单 payload |
| S8 | `_binding_scenario()` 统一供关键付款 / 期末余额 / 标题取数 |
| S9 | `revision_summary` 由真实 `CashEventRevision` 生成（无差异则不生成该键） |
| S10 | 两张更正卡分别关联鲜食原料采购（1300 → 1400 元）与平台结算款（D3 → D2） |
| S11 | `/family/cards` 加 `RequireRole(['family_member'])`；`POST /households/join` 要求 family_member |
| S12 | 咨询处理结果字段中文化 + 按语义格式化；未登记字段显示「处理信息」 |
| S13 | 风险卡挂载真实生成的分析结果（延迟 2 天 = 付款缺口 / 延迟 1 天 = 低于留底） |
| S14 | 预置结算记录渠道统一为「平台结算单」 |

另外顺带修掉一个真缺陷：`HouseholdService` 读取
`analysis.pending_inflows_at_limit`，而 `AnalysisResult` 上并没有该属性，
勾选「尚未到账的收入」过去会 AttributeError → 500。

### 6.3 验收参数（与 600 元核心回归并列保留）

见 `docs/final-recording-checklist.md`：留底 1200 元时，
按时可提用 600 / 延迟可提用 0 / 付款缺口 200 / 留底缺口 1400，
且两个缺口不能相加。

