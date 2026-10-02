# 增强 v2 改造前基线记录

本文件只记录事实，不包含评价与计划。所有数据为改造开始前在本地开发环境实测所得。

## 1. 仓库状态

| 项目 | 值 |
| --- | --- |
| 工作目录 | `D:\CodingData\Github\GongHangCup\gong-e-wendai` |
| 分支 | `main` |
| HEAD | `f0414ebe520c0bfc95beb3700d253eeec5d3954f` |
| origin/main | `f0414ebe520c0bfc95beb3700d253eeec5d3954f` |
| 与 origin/main 差异 | `0 0`（完全一致） |
| 工作区 | 干净（`git status --short` 无输出） |
| 远程 | `https://github.com/WuChangqing1/gong-e-wendai.git`（私有） |
| 安全基线 tag | `pre-enhancement-20261003` |
| 开发分支 | `feat/wendai-enhancement-v2` |

`git ls-files` 中不含 `.env`、`.env.production`、`app.db`、`uploads/*`、`logs/*`、`*.key`、`*.pem`、`secrets/`。

## 2. 工具链版本

| 工具 | 版本 |
| --- | --- |
| conda 环境 | `gonghangcup`（`D:\App\Business\Coding\Python\Miniconda\envs\gonghangcup`） |
| Python（项目环境） | 3.12.14 |
| Python（系统 PATH） | 3.13.12（与项目无关） |
| Node.js | v22.19.0（nvm4w 管理，另装有 22.14.0） |
| npm | 10.9.3 |
| pytest | 9.1.1 |
| Vue/Vite | vite 7.3.6 |

`GLM` 环境变量在 Windows **Machine** 作用域存在（仅确认存在，未读取内容）。`User`、`Process` 作用域不存在。`AI_API_KEY` 三个作用域均不存在。

## 3. 数据库与迁移

| 项目 | 值 |
| --- | --- |
| 开发库 | `data/app.db`（11,530,240 字节，含 `-wal` / `-shm`） |
| Alembic 版本文件 | `0053fb3fa9b7_initial_schema.py`、`0ee06e0f0f90_add_import_batches.py` |
| 当前 head | `0ee06e0f0f90` |

## 4. 改造前测试结果（事实）

### 4.1 后端 pytest

```
cd backend
python -m pytest -q --tb=line
```

| 项目 | 值 |
| --- | --- |
| 通过 | **326** |
| 失败 | 0 |
| 错误 | 0 |
| 跳过 | 0 |
| 退出码 | 0 |

按文件分布：

| 文件 | 通过数 |
| --- | ---: |
| `tests/test_admin.py` | 17 |
| `tests/test_ai_service.py` | 39 |
| `tests/test_analysis_api.py` | 38 |
| `tests/test_auth_permissions.py` | 29 |
| `tests/test_cash_engine.py` | 63 |
| `tests/test_cash_events.py` | 36 |
| `tests/test_consultation.py` | 30 |
| `tests/test_csv_import.py` | 32 |
| `tests/test_household.py` | 42 |

> 说明：pytest 9.1.1 在本机不输出末尾的汇总行，上表通过统计 `-rA` 逐条结果得出。

### 4.2 前端

| 命令 | 结果 |
| --- | --- |
| `npm run typecheck` | 通过，无输出 |
| `npm run lint` | 通过，`--max-warnings 0`，无输出 |
| `npm run test` | 2 个文件 / **11** 个用例通过（`tests/money.test.ts` 7、`tests/copy-and-ui.test.tsx` 4） |
| `npm run build` | 成功，产出 `index-Cah9ZDkP.css` 10.79 kB、`index-C7WSmiNk.js` 267.54 kB、`charts-BAOgsyvy.js` 554.97 kB、`antd-NPVqhvmt.js` 1,327.14 kB |

### 4.3 端到端

| 项目 | 值 |
| --- | --- |
| 命令 | `npm run test:e2e`（`E2E_BASE_URL` 默认 `http://127.0.0.1:8000`） |
| 项目数 | 2（`desktop` 1440×900、`mobile` Pixel 5） |
| 用例文件 | `auth-today.spec.ts`、`operations.spec.ts`、`collaboration.spec.ts`、`charts.spec.ts`、`responsive.spec.ts` |

## 5. 改造前计算行为（事实）

`backend/app/services/cash_engine.py` 当前实现：

| 项目 | 当前值 |
| --- | --- |
| `ENGINE_VERSION` | `1.0.0` |
| 状态枚举 | `OK` / `PAYMENT_GAP` / `BELOW_BUFFER` / `INPUT_INCOMPLETE` |
| `max_withdrawable` 限制点 | 仅 `future_points`（期初点 **未参与**） |
| 无未来事件时 | `max_withdrawable = None`（即使期初与留底均已确认） |
| `pending_settlement_cents` | 直接等于 `window_inflow_cents` |
| 缺口计算 | `payment_gap = max(0, -min_balance)`、`buffer_gap = max(0, buffer - min_balance)`，不相加 |
| 联合状态优先级 | `INPUT_INCOMPLETE > PAYMENT_GAP > BELOW_BUFFER > OK` |

既有回归算例（`backend/tests/fixtures_cash.py`）当前期望：期初 600 元、留底 600 元，结算 +2200（第 2 天）、供应商 -1000（第 3 天）、平台 +700（第 6 天），按时 `max_withdrawable = 1200.00`。

## 6. 改造前生产环境（事实）

| 项目 | 值 |
| --- | --- |
| 正式入口 | `https://ccqspace.site/wendai/` |
| 服务器 | `ssh fengz` → `ubuntu@VM-0-11-ubuntu` |
| 代码目录 | `~/apps/gong-e-wendai` |
| 数据目录 | `~/apps/gong-e-wendai-data`（`app.db`、`uploads`、`logs`、`backups`） |
| 应用进程 | `127.0.0.1:18089` |
| 服务器内 Nginx | `0.0.0.0:18088`，`/wendai/` 反向代理 |
| 服务 | systemd user service `gong-e-wendai` |
| `AI_ENABLED` | `false` |

## 7. 改造前参考增强包

`Files002/工e稳袋_预测与留底增强包_v1.zip` 已解压保存至 `reference/wendai-enhancements/wendai-enhancements-v1/`，作为计算规范、算法参考与人工标准答案来源，**不构成第二套线上金额计算源**。

包内 `package.json` 声明 `engines.node >= 24`；本机现有 Node 为 v22.19.0。
