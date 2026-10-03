# 部署记录（V3 增量优化）

本文记录 **V3 增量优化**在生产服务器上的实际部署过程与验收结果。

## 1. 部署信息

| 项目 | 值 |
| --- | --- |
| 部署日期 | 2026-10-03 |
| 部署前 commit | `cf83b2d` |
| 部署后 commit | `bdfeb07`（`main`，与 GitHub Private 一致） |
| 发布 Tag | `v3-optimization-20261003` → `bf8a44a` |
| 服务器 | `ssh fengz` → `ubuntu@VM-0-11-ubuntu` |
| 代码目录 | `~/apps/gong-e-wendai` |
| 数据目录 | `~/apps/gong-e-wendai-data` |
| 解释器 | `~/apps/gong-e-wendai/.venv/bin/python`（Python 3.12.15） |
| Node | v22.23.3 / npm 10.9.9 |
| 服务 | systemd user service `gong-e-wendai`（`NRestarts=0`） |
| 应用监听 | `127.0.0.1:18089`（单 worker） |
| 对外入口 | `https://ccqspace.site/wendai/` |

## 2. 备份（部署前完成）

| 项目 | 路径 |
| --- | --- |
| 数据库备份 | `~/apps/gong-e-wendai-data/backups/app-20261003-154521.db`（7.4 MB，SQLite 在线备份 API） |
| Nginx 配置备份 | `/etc/nginx/conf.d/gong-e-wendai.conf.bak-before-v3-20261003234521` |
| Nginx 配置备份 | `/etc/nginx/conf.d/mysite.conf.bak-before-v3-20261003234521` |
| 代码目录备份 | `~/apps/gong-e-wendai-pre-v3-20261003234521.tar.gz`（3.4 MB） |
| 部署脚本自带备份 | `~/apps/gong-e-wendai-pre-v2-20261003234604.tar.gz` |

回滚点：`pre-v3-optimization-20261003`（本地 Tag，指向 `5ef53ef`）。

## 3. 更新方式

服务器**没有 GitHub 凭据**（`git fetch origin` 会失败），因此使用本地打包的 git bundle：

```bash
# 本地
git bundle create ../gew-v3.bundle main          # 0.77 MB，含完整历史
scp ../gew-v3.bundle fengz:/tmp/

# 服务器
cd ~/apps/gong-e-wendai
BUNDLE=/tmp/gew-v3.bundle BASE_PATH=/wendai/ bash scripts/deploy_v2.sh
```

bundle 校验：`git bundle verify` 通过，含 `refs/heads/main` 与完整历史。

## 4. 部署步骤与结果

| 步骤 | 结果 |
| --- | --- |
| 1 备份数据库 | 成功，`app-20261003-154521.db`（7.4 MB） |
| 2 备份 Nginx 配置 | 成功（`gong-e-wendai.conf` 与 `mysite.conf`，其他 location 未改动） |
| 3 备份代码目录 | 成功 |
| 4 更新代码 | `cf83b2d..bdfeb07`，`HEAD` 指向新 commit |
| 5 安装 Python 依赖 | `deps ok` |
| 6 数据库迁移 | `6e784eb94252` → **`7b1c4d9e2f30 (head)`** |
| 7 构建前端 | `VITE_BASE_PATH=/wendai/`，1378 模块，产物含入口 `index-B2J6VSl6.js` |
| 8 重启服务 | `active`，`NRestarts=0` |

**迁移先于重启**，与 `docs/deployment.md` 的顺序要求一致。

## 5. 迁移对既有数据的影响

在**生产真实库**上核对（只读查询）：

| 项目 | 结果 |
| --- | --- |
| `admin` 角色残留 | **0 行** |
| 角色分布 | `merchant` 299 / `family_member` 12 / `consultant` 9 |
| `wangzhanggui` | `active` + `merchant`（保留，未被误伤） |
| `wangtaitai` | `active` + `family_member` |
| `zixunxiaoli` | `active` + `consultant` |
| `xitongguanli`（原纯管理员） | `disabled` + **无角色**（未物理删号，历史与审计完整） |
| 审计留痕 | `identity.admin_role_removed`，`{"affected": 1, "kept_business_role": 0, "disabled_admin_only": 1}` |
| 迁移版本 | `7b1c4d9e2f30` |

业务数据（事项、家庭、咨询、分析结果）**全部保留**。

## 6. 生产验收（服务器本机 + 真实浏览器）

### 6.1 接口与页面

| 项目 | 结果 |
| --- | --- |
| `/api/v1/admin/overview`、`/users`、`/runtime`、`/audit-logs` | 全部 **404**（不是 401/403，说明后台确实已移除） |
| `/wendai/`、`/wendai/today`、`/wendai/events` | 200 |
| `/wendai/api/v1/health` | 200，`{"status":"ok","database":"ok","ai_enabled":true,"env":"production"}` |
| 未登录 `/api/v1/cash-events` | 401 |
| 不存在的接口 | 404（JSON，不回退到 index.html） |
| 首页引用产物 | `/wendai/assets/index-B2J6VSl6.js`（新入口） |
| 最近 10 分钟日志 ERROR | **0 行** |

### 6.2 既有项目未受影响

| 项目 | 结果 |
| --- | --- |
| 既有 18082 | 200 |
| 既有 18085 | 200 |

### 6.3 计算口径（生产真实接口）

`wangzhanggui` 登录后 `/api/v1/analysis/today` 返回：

```
status=FEASIBLE  status_label=资金安排可行
max_withdrawable_cents=0   opening_balance_cents=60000   buffer_cents=60000
```

期初 600 元、留底 600 元 → 可提用 0 且 `FEASIBLE`，与设计口径一致
（该商户尚未登记未来事项，当前资金刚好等于留底）。

### 6.4 真实浏览器验收（`https://ccqspace.site/wendai/`）

| 检查 | 结果 |
| --- | --- |
| 旧后台地址 `/wendai/admin` | 渲染「页面不存在」，无任何后台界面 |
| 七视口 × 5 路由横向溢出 | 全部 `overflow <= 1`（360/375/390/430/768/1024/1440） |
| `<= 430px` | 底部标签栏可见、左侧栏隐藏 |
| `>= 1024px` | 左侧栏可见、底部标签栏隐藏 |
| 底部导航 | 5 项：今日决策 / 现金事件 / 家庭协同 / 经营咨询 / 我的（不含情景分析） |
| 手机端现金事件 | 卡片列表，无可见表格 |
| 手机端「我的」 | 设置列表 + 二级进入，无横向页签 |
| 手机端筛选 | 底部抽屉，含「预计时间区间」等分组 |
| 图表按需加载 | 真实网络下 `.gew-chart canvas` 正常渲染 |

**公网浏览器验收 4 项全部通过。**

## 7. 部署后的服务状态

| 项目 | 值 |
| --- | --- |
| systemd | `active`，`NRestarts=0` |
| 公开入口 | `https://ccqspace.site/wendai/` 200 |
| 应用监听 | `127.0.0.1:18089`（单 worker） |
| Nginx | 配置未改动，复用既有 `/wendai/` 反代 |
| 日志 ERROR | 0 |

## 8. 已知事项

* 服务器无 `sqlite3` CLI，库检查改用 `.venv/bin/python` + 标准库只读查询
* 服务器 `node_modules` 已存在，因此本次未执行 `npm ci`（`deploy_v2.sh` 也不包含该步）
* `xitongguanli` 变为 `disabled` 且无角色：这是 V3「不存在管理员身份」的预期结果，
  如需恢复为某种业务身份，应由上层系统重新授予
