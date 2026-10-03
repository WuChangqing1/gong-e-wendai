# V3 优化基线（修改前现状）

本文件记录 **V3 增量优化开始前**的真实状态，作为后续对照与回滚依据。

## 1. Git 状态

| 项目 | 值 |
| --- | --- |
| 起始 commit | `5ef53ef`（security: add a pre-commit guard and a full key-exposure audit） |
| 起始分支 | `main` |
| 安全 Tag | `pre-v3-optimization-20261003` |
| 开发分支 | `feat/wendai-v3-optimization` |
| remote | `https://github.com/WuChangqing1/gong-e-wendai.git`（Private） |
| 工作区 | 干净（仅有本次会话尚未提交的移动端首批改动） |

## 2. 测试基线（全部通过）

| 层次 | 命令 | 结果 |
| --- | --- | --- |
| Backend | `python -m pytest` | **492 passed**，90.89s |
| Frontend 类型 | `npm run typecheck` | 通过（`tsc -b --noEmit`） |
| Frontend Lint | `npm run lint` | 通过（`eslint . --max-warnings 0`，0 warning） |
| Frontend 单元 | `npm run test` | **27 passed**（4 个文件） |
| Frontend 构建 | `npm run build` | 成功，产物 `frontend/dist` |
| E2E | `npm run test:e2e` | 本次未执行（需先启动前后端；留待移动端改造后统一回归） |

构建产物体积（基线）：

```
dist/index.html                     0.97 kB
dist/assets/index-*.css            13.57 kB
dist/assets/react-*.js             51.53 kB
dist/assets/index-*.js            299.38 kB
dist/assets/charts-*.js           554.97 kB
dist/assets/antd-*.js           1,328.67 kB
```

> `antd` + `charts` 两个 chunk 合计约 1.9MB（gzip 约 611KB），是手机端首屏的主要负担，
> 已列入 V3「网络环境」优化观察项。

## 3. 核心计算现状审查（对照 V3 第 12–20 节）

审查对象：`backend/app/services/cash_engine.py`（`ENGINE_VERSION = "2.0.0"`）。

| V3 要求 | 现状 | 结论 |
| --- | --- | --- |
| 期初时点参与约束 | `points` 首元素为 `is_opening=True`；`minimum_balance = min(所有点含期初)`；`headroom_minimum = minimum_balance - buffer` | **已达标** |
| 600 / 留底 600 → `FEASIBLE`、可提用 0 | 有回归用例 | **已达标** |
| 500 / 留底 600 → `BELOW_BUFFER`、可提用 0 | 有回归用例 | **已达标** |
| 3600 核心算例 → 可提用 1200 | 有回归用例 | **已达标** |
| 结算延期 → `PAYMENT_GAP`、缺口 200 / 800 | 有回归用例 | **已达标** |
| 两缺口绝不相加 | `payment_gap = max(0, -min)`；`buffer_gap = max(0, buffer - min)`，展示层显式说明 | **已达标** |
| 共同约束取最保守上限 | 联合结果取各情景最严重缺口 | **已达标** |
| 统一四态（无 `OK`） | 状态机 `INPUT_INCOMPLETE > PAYMENT_GAP > BELOW_BUFFER > FEASIBLE`；旧 `OK` 仅作历史读取兼容 | **已达标** |
| 待结算 ≠ 未来 7 天收入 | `pending_settlement_cents` 只统计 `direction=inflow & state=scheduled & event_type=settlement` | **已达标** |
| 前端不重算金额 | 前端只展示后端返回值，无金额计算逻辑 | **已达标** |

结论：**V3 第 12–20 节的核心计算要求在本分支起点已经满足**，本次优化不再改动计算口径，
只做验证与回归保护（避免后续重构破坏）。

## 4. 当前生产结构

| 项目 | 值 |
| --- | --- |
| 公开入口 | `https://ccqspace.site/wendai/` |
| Nginx | `0.0.0.0:18088`，`location /wendai/` 反代 |
| 应用 | `127.0.0.1:18089`（单 worker） |
| 服务 | systemd user service `gong-e-wendai` |
| 代码目录 | `~/apps/gong-e-wendai` |
| 数据目录 | `~/apps/gong-e-wendai-data`（`app.db` / `uploads` / `logs` / `backups`） |
| 数据库 | SQLite（WAL） |
| 迁移 head | `6e784eb94252` |

既有 `18082` / `18085` 为其他项目占用，**本项目不得改动**。

## 5. 已知问题（本次 V3 待解决）

按 V3 章节归类：

| 编号 | 问题 | 章节 |
| --- | --- | --- |
| P1 | 存在完整的 Admin 产品模块：`admin` 角色、3 个管理页面、`/api/v1/admin/*`、`ADMIN_NAV`、`require_admin`、管理员创建账户能力 | 6 / 7 |
| P2 | 前端路由守卫把 `admin` 放进商户页与咨询页白名单，管理员手敲 `/today` 会渲染出报错空壳页 | 106 |
| P3 | 断点不统一：CSS `767px`、Ant Design `Sider breakpoint="lg"`（约 992px）、Zustand `isMobile` 三套语义 | 57 / 58 |
| P4 | 手机端 8 处表格写死桌面宽度（860/980/1000px），在 360px 视口必须横向拖动 | 66 / 84 |
| P5 | 「我的」页 5 个页签总宽 408px，最后一个被折叠进 `...` | 80 |
| P6 | 页面大标题与顶部栏标题重复，手机首屏被吃掉约 150px | 61 |
| P7 | 图表按桌面字号写死（11–12px），窄屏标签与图例拥挤；交互依赖 hover | 73 / 83 |
| P8 | 手机端输入框字号 13px，iOS Safari 聚焦会强制放大整页 | 92 |
| P9 | 筛选区在手机端纵向堆叠约 230px，未折叠 | 68 |
| P10 | 待确认：`GLM` 环境变量是否存在于本机（只检查存在性，绝不打印值） | 43–47 |

## 6. 未执行项说明

- **E2E 未跑**：`npm run test:e2e` 需要前后端同时在线，且会向数据库写入大量临时账号。
  基线阶段先跳过，在移动端改造完成后统一回归（V3 第 111 节要求）。
- **生产服务器未连接**：本轮所有改动先在本地完成并通过全部测试，部署阶段再按 V3 第 116–123 节执行。
