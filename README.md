# 工 e 稳袋（gong-e-wendai）

面向小餐饮、小零售、夫妻店、个体工商户等小微经营者的**经营资金与家庭协同决策系统**。

它回答一个具体问题：

> **今天到底可以从经营资金中拿多少钱用于家庭，同时不影响未来 7 天已经确认的经营付款？**

系统不接受“拍脑袋”的答案：所有金额、时间、余额、可提用金额、缺口都由**确定性计算引擎**给出，
智能服务只负责理解、提取、整理与表达，**不参与任何金额计算**，也不得覆盖计算结果。

> 队友想看**精简版介绍、四个角色能用的功能、演示账号密码**：直接读
> [`项目介绍与使用指南.md`](项目介绍与使用指南.md)。

---

## 1. 核心链路

```
经营数据
  ↓ 现金事件标准化（金额一律转为整数分）
  ↓ 未来 7 天资金时点推演（逐事件扫描余额曲线，含期初时点）
  ↓ 计算当前最大可提用金额
  ↓ 定位最紧张资金时点与限制原因
  ↓ 需要更正时进入版本系统（不覆盖历史）
  ↓ 全部重新计算
```

**期初时点参与约束**（引擎 2.0.0 的核心修正）：

```
T = { 期初时点 } ∪ { 每个事件之后的时点 } ∪ { 窗口结束时点 }
max_withdrawable = max(0, min_{t∈T}(余额(t) − 留底))
```

用户是在“现在”把钱拿走，所以 `opening − x ≥ buffer` 必须立刻成立。
由此保证：**未来收入不能提前提用**，把收款往后挪或提高留底都不会提高上限。

主回归算例：期初 3600、留底 600，进货款 −1400、结算款 +2000、房租 −1800、
退款 −600 → 按时可提用 **1200**；结算延迟 2 天 → `PAYMENT_GAP`，
付款缺口 **200**、留底缺口 **800**（两者绝不相加）。

在现金流决策之上有三条增强链与两条协同链：

| 模块 | 解决的问题 |
| --- | --- |
| **日常收付预测** | 接下来 7 个自然日的日常到账与采购参考（不计入今天可提用金额） |
| **结算延期压力** | 如果这笔结算再晚几天，付款与留底会怎样 |
| **经营留底建议** | 按历史经验误差给出留底建议，由商户确认后生效 |
| **家庭协同** | 经营资金与家庭资金高度关联，但家庭共同决策者不一定同时在现场 |
| **经营咨询** | 某笔结算/到账/经营资金事项不明确时，商户整理最小必要信息发起咨询，收到结果后更正现金事件并重算 |

详细口径见 [`docs/enhancement-v2.md`](docs/enhancement-v2.md)。

> **V3 起本系统没有「管理员」这一业务身份。**
> 工 e 稳袋定位为上层银行 / 商户服务 App 中的一个业务模块，平台级用户管理与系统运维
> 由上层系统承担：没有管理后台、没有 `/api/v1/admin/*` 接口、也没有运行状态面板。
> 业务身份只有 **经营者 / 家庭成员 / 咨询人员** 三种，它们之间没有等级关系。
> 详见 [`docs/architecture.md`](docs/architecture.md) 与
> [`docs/v3-optimization.md`](docs/v3-optimization.md)。

---

## 2. 技术栈

**前端**：React 19 · Vite 7 · TypeScript（strict）· React Router 7 · TanStack Query 5 ·
Zustand 5 · ECharts 5 · React Hook Form + Zod · Ant Design 6（完整主题定制，不使用默认蓝色体系）

**后端**：Python 3.12 · FastAPI · SQLAlchemy 2.x · Pydantic 2.x · Alembic · SQLite（WAL）·
PyJWT · pwdlib + Argon2 · httpx · pytest · uvicorn

**约束**：
- 系统内部金额一律为整数分（`int` cents），**禁止浮点**
- 数据库时间一律存 UTC，前端按 `Asia/Shanghai` 显示
- 生产 SQLite 启用 `WAL` / `foreign_keys` / `busy_timeout`，单应用实例、单 worker

---

## 3. 项目结构

```
gong-e-wendai/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI 装配：中间件 / 异常 / 路由 / SPA 挂载
│   │   ├── api/v1/                 # 路由层（仅做参数校验与权限编排）
│   │   ├── core/                   # config / database / security / errors / logging
│   │   ├── models/                 # SQLAlchemy ORM（20 张表）
│   │   ├── schemas/                # Pydantic 请求与响应结构
│   │   ├── repositories/           # 数据访问
│   │   ├── services/               # 业务服务（cash_engine 为纯函数计算内核）
│   │   └── utils/                  # money / timeutil
│   ├── alembic/                    # 数据库迁移
│   ├── tests/                      # pytest 测试与回归算例
│   ├── requirements.txt
│   └── pyproject.toml
├── frontend/
│   ├── src/{api,components,features,hooks,layouts,pages,routes,store,styles,types,utils}
│   ├── tests/                      # Vitest + React Testing Library
│   ├── e2e/                        # Playwright
│   └── package.json
├── scripts/                        # init_db / seed_dev_data / build_production / backup_db / deploy.sh
├── docs/                           # architecture / api / database / csv-format / deployment / security / acceptance-tests / mobile-design / v3-optimization
├── data/                           # 运行数据（不入 Git）
└── uploads/                        # 上传文件（不入 Git）
```

---

## 4. Windows 开发环境

### 4.1 环境要求

| 组件 | 版本 |
| --- | --- |
| Python | 3.12（conda 环境 `gonghangcup`） |
| Node.js | ≥ 20.19 |
| Git | 任意近期版本 |

### 4.2 激活 conda 环境

```powershell
conda activate gonghangcup
python --version           # Python 3.12.x
where.exe python           # 应指向 ...\envs\gonghangcup\python.exe
```

### 4.3 安装后端依赖

```powershell
conda activate gonghangcup
cd D:\CodingData\Github\GongHangCup\gong-e-wendai\backend
python -m pip install -r requirements.txt
```

> 国内网络可使用镜像：`-i https://pypi.tuna.tsinghua.edu.cn/simple`

### 4.4 配置环境变量

```powershell
cd D:\CodingData\Github\GongHangCup\gong-e-wendai
Copy-Item .env.example .env
```

`backend/app/core/config.py` 会自动读取项目根目录的 `.env`，**开发环境可以完全使用默认值**。

### 4.5 数据库迁移

```powershell
conda activate gonghangcup
cd D:\CodingData\Github\GongHangCup\gong-e-wendai\backend
python -m alembic upgrade head

# 或者使用脚本（会打印数据库位置与迁移结果）
cd ..
python scripts\init_db.py
```

生产环境首次启动只运行 **Migration**，不会自动生成任何账户或种子数据。

### 4.6 启动后端

```powershell
conda activate gonghangcup
cd D:\CodingData\Github\GongHangCup\gong-e-wendai\backend
uvicorn app.main:app --reload --port 8000
```

- 健康检查：<http://127.0.0.1:8000/api/v1/health>
- 接口文档（仅非生产环境）：<http://127.0.0.1:8000/api/docs>

### 4.7 启动前端

```powershell
cd D:\CodingData\Github\GongHangCup\gong-e-wendai\frontend
npm install
npm run dev
```

访问 <http://127.0.0.1:5173>。Vite 已配置 `/api` 代理到 `http://127.0.0.1:8000`。

### 4.8 开发种子数据（可选，仅开发环境）

```powershell
conda activate gonghangcup
cd D:\CodingData\Github\GongHangCup\gong-e-wendai
python scripts\seed_dev_data.py
```

生产环境**不会**、也**不应该**执行该脚本。

---

## 5. 测试

### 5.1 后端

```powershell
conda activate gonghangcup
cd D:\CodingData\Github\GongHangCup\gong-e-wendai\backend
python -m pytest
```

共 **484 项**，覆盖现金引擎、分析接口、现金事件、CSV 导入、家庭协同、
经营咨询、业务身份边界与跨主体隔离、智能服务、增强模块、参考一致性。

主回归算例（`backend/tests/fixtures_cash.py`）锁定产品口径：
期初 3600 元、留底 600 元，进货款 −1400、结算款 +2000、房租 −1800、退款 −600。

| 口径 | 状态 | 可提用金额 | 最紧时点余额 | 付款 / 留底缺口 |
| --- | --- | --- | --- | --- |
| 按当前计划 | `FEASIBLE` | 1200.00 元 | 1800.00 元 | 0 / 0 |
| 结算延迟 2 天 | `PAYMENT_GAP` | 0.00 元 | −200.00 元 | 200 / 800 |
| 共同约束 | `PAYMENT_GAP` | 0.00 元 | 取最保守上限 | 200 / 800 |
| 期初 600 / 留底 600 + 只有收入 | `FEASIBLE` | 0.00 元 | 600.00 元 | 0 / 0 |
| 期初 500 / 留底 600 + 只有收入 | `BELOW_BUFFER` | 0.00 元 | 500.00 元 | 0 / 100 |

`backend/tests/test_reference_parity.py` 将 Python 引擎与
`reference/wendai-enhancements/` 的参考内核逐分对照（54 项），
参考包只作为计算规范与人工标准答案来源，**不是**第二套线上金额计算源。

### 5.2 前端

```powershell
cd D:\CodingData\Github\GongHangCup\gong-e-wendai\frontend
npm run test          # Vitest（37 项）
npm run lint          # ESLint（--max-warnings 0）
npm run typecheck     # TypeScript strict
npm run test:e2e      # Playwright（39 项，需先启动前后端）
```

端到端测试默认指向 `http://127.0.0.1:8000`（同源单端口生产形态），
也可指向子路径部署：

```powershell
$env:E2E_BASE_URL="https://ccqspace.site/wendai"
npm run test:e2e
```

咨询工作台界面用例会通过 `scripts/provision_consultant.py` 现场开通一个咨询人员身份
（与生产开通路径一致，不再依赖管理员凭据）。脚本需要 Python：
默认使用 PATH 中的 `python`，可用 `E2E_PYTHON` 指定解释器。

### 5.3 提交前守卫（每个克隆执行一次）

`powershell
git config core.hooksPath .githooks
`

启用后每次 `git commit` 都会自动扫描暂存内容，阻止密钥与禁止入库的文件进入历史。
GitHub 的 secret scanning 不认识智谱密钥形态，因此这道本地守卫是主要防线。

需要手动检查全部历史时：

`powershell
python scripts\scan_history.py        # 遍历所有提交
python scripts\audit_key_exposure.py  # 遍历所有仓库 + 线上资源（需 GLM 环境变量）
`

### 5.4 推送前必须执行

```powershell
conda activate gonghangcup
python scripts\check_secrets.py
```

发现密钥或禁止入库的文件时**只输出路径**，并以非零退出码失败。

---

## 6. 构建

```powershell
cd D:\CodingData\Github\GongHangCup\gong-e-wendai\frontend
npm ci
npm run build         # 产物位于 frontend/dist
```

生产为**同源单端口**部署：FastAPI 提供 `/api/*` 并挂载 `frontend/dist`，
未命中的非 API 路由回退到 `index.html`（SPA 刷新不 404），
`/api/*` 未命中时正常返回 API 404（不会返回 index.html）。

---

## 7. 服务器部署

目标：**只开放 TCP 18082**，用户访问 `http://SERVER:18082/`。

```bash
# 1. 服务器环境检查（不要假设服务器为空）
ssh fengz
whoami; hostname; pwd; uname -a
df -h; free -h
python3 --version; node --version; npm --version; git --version
ss -lnt | grep 18082        # 必须确认端口未被占用

# 2. 拉取私有仓库
git clone git@github.com:<ACCOUNT>/gong-e-wendai.git ~/apps/gong-e-wendai

# 3. Python 环境（不修改系统 Python）
cd ~/apps/gong-e-wendai/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4. 前端构建
cd ~/apps/gong-e-wendai/frontend
npm ci && npm run build

# 5. 生产环境变量（不提交 Git）
cd ~/apps/gong-e-wendai
cp .env.example .env.production
# 必须修改：APP_ENV=production, DATABASE_URL, JWT_SECRET

# 6. 迁移 + 启动
cd backend
set -a; . ../.env.production; set +a
.venv/bin/alembic upgrade head
```

代码与持久数据分离：

```
$HOME/apps/gong-e-wendai/          # 代码（git pull 更新）
$HOME/apps/gong-e-wendai-data/     # 运行数据
├── app.db
├── uploads/
├── logs/
└── backups/
```

完整步骤见 [`docs/deployment.md`](docs/deployment.md)。

### 7.1 生产环境变量示例

```env
APP_ENV=production
APP_HOST=0.0.0.0
APP_PORT=18082

DATABASE_URL=sqlite:////home/ubuntu/apps/gong-e-wendai-data/app.db

JWT_SECRET=            # openssl rand -hex 32
JWT_ACCESS_EXPIRE_MINUTES=30
JWT_REFRESH_EXPIRE_DAYS=14
COOKIE_SECURE=false    # 启用 HTTPS 后改为 true

AI_ENABLED=false
AI_API_KEY=
AI_BASE_URL=
AI_MODEL=
AI_TIMEOUT_SECONDS=30
```

> `.env` / `.env.production` / API Key / JWT Secret / SQLite 数据库 / 上传文件 / 日志
> **绝不进入 Git**（见 `.gitignore`）。

### 7.2 启动命令

```bash
uvicorn app.main:app --host 0.0.0.0 --port 18082 --workers 1
```

SQLite 模式保持 **1 worker**，避免高并发写入冲突。

---

## 8. 智能服务配置（智谱 GLM）

智能服务是**辅助能力**，不是核心链路的一部分。

```env
AI_ENABLED=true
GLM=<智谱 API Key>
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
GLM_TEXT_MODEL=glm-4.5-air
GLM_VISION_MODEL=glm-4.6v
GLM_TIMEOUT_SECONDS=30
GLM_VISION_TIMEOUT_SECONDS=45
```

- 能力：`extract_cash_event_from_text` / `extract_cash_event_from_image` /
  `explain_analysis` / `draft_consultation`；业务代码只依赖 `AIService`，
  Provider 配置集中管理，不出现厂商 SDK
- 密钥使用 `SecretStr` 保存，**只存在后端环境变量**，绝不下发前端，
  也不出现在日志、Traceback、`/ai/status` 或 `/health` 中
- 推送前必须执行 `python scripts/check_secrets.py`
- AI 关闭 / 无 Key / 超时 / HTTP 500 / 返回非法 JSON / 供应商异常时，
  登录、事件管理、CSV、现金流计算、家庭协同、经营咨询**全部不受影响**，
  前端提示「智能服务暂时不可用，你仍可以手动完成当前操作」
- AI 提取结果**不能直接入库**，必须经用户核对金额与时间后确认
- 图片只在内存与私有目录处理，不写入公开静态资源

详见 [`docs/glm-integration.md`](docs/glm-integration.md)。

---

## 9. Git 工作流

按功能阶段持续提交，不允许"全部写完一次性提交"：

```
开发 → 测试通过 → git diff 检查 → commit → push → 下一阶段
```

```powershell
cd D:\CodingData\Github\GongHangCup\gong-e-wendai
git status
git add -A
git commit -m "feat: ..."
git push origin main
```

提交信息使用 Conventional Commits 前缀：`chore` / `feat` / `fix` / `test` / `docs` / `refactor`。
