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

---

## 9. 最终产品化收口（2026-10-04）

### 9.1 两次增量部署

| 项目 | 值 |
| --- | --- |
| 分支 | `feat/final-product-polish` → merge 进 `main` |
| 第一次部署 | `5c26a7b` → `bbc20a4`（合并提交） |
| 第二次部署 | `bbc20a4` → `d385c42`（复核修复） |
| 发布 Tag | `final-product-polish-20261004`、`final-product-hardening-20261004` |
| 更新方式 | `git bundle`（服务器无 GitHub 凭据） |
| 备份 | `~/apps/gong-e-wendai-data/backups/`、`~/apps/gong-e-wendai-pre-v2-20261004104257.tar.gz`、`...-20261004105318.tar.gz` |
| 迁移 | 无新增迁移，仍为 `7b1c4d9e2f30 (head)` |
| 服务 | `active`，`NRestarts=0`，`/api/v1/health` = `ok / database ok / ai_enabled true` |
| 既有项目 | 18082 / 18088 均 200 |
| 部署后文档提交 | `1569618`、`39122c5`（`git diff d385c42..39122c5` 仅 docs/，运行时代码与生产一致） |

### 9.2 正式业务数据（`scripts/populate_product_data.py --apply`）

| 项目 | 结果 |
| --- | --- |
| 资金时点 | 新增 3600 元时点，历史 600 元时点保留（共 2 条） |
| 旧初始化事项 | 3 笔取消（版本 3），并规范化为 `SET-20261003-001` / `PAY-20261004-001` / `SET-20261007-001`（版本 4，`changed_fields=["cash_key"]`，`material=false`） |
| 未来事项 | 10 笔 scheduled（1400/2000/1800/600/900/500/1200/400/300/600 元） |
| 版本历史 | 4 笔事项各 3 个版本（共 30 条修订记录） |
| 历史经营数据 | 84 天连续完整 |
| 结算记录 | 20 条（completed 17 / open 3） |
| 家庭 | 王家小院（owner 王掌柜 + member 王太太，均 active） |
| 家庭协同卡 | 8 张（decision 4 / risk 2 / revision 2），收件人均为王太太，已读 5 / 未读 3，表态 同意 1 / 商量 1，评论 4 条 |
| 家庭邀请码 | 旧演示邀请码已换为随机 8 位码 |
| 经营咨询 | 8 条（submitted 2 / under_review 2 / verified 2 / need_more_information 1 / closed 1），处理时间线 83 条 |
| 第二次 `--dry-run` | 「无待执行动作（数据已就绪，幂等）」 |

### 9.3 计算口径复核（生产真实接口）

`wangzhanggui`（期初 3600 / 留底 600 / 10 笔未来事项）：

| 调用 | 结果 |
| --- | --- |
| `/analysis/today` | `FEASIBLE` 资金安排可行，可提用 **120000**，最紧时点余额 **180000** |
| `/analysis/run {mode: delayed, delay_days: 2}` | `PAYMENT_GAP`，付款缺口 **20000**，留底缺口 **80000**，最紧时点 **-20000** |
| `/analysis/run {mode: joint, delay_days: 2}` | `PAYMENT_GAP`，`binding_scenario_index=1`（到账延迟），付款缺口 20000 / 留底缺口 80000 |
| `/enhancements/overview` | 预测 `available=true`（84 天），留底建议 `SUGGESTION`：当前 600 → 建议 1200 |

### 9.4 三账号逐页验收（公网 `https://ccqspace.site/wendai/`，真实浏览器）

11 个页面全部通过：无开发口径文案、无内部字段名、无乱码、无横向溢出。

| 账号 | 页面 |
| --- | --- |
| `wangzhanggui`（经营者） | 今日决策 / 现金事件 / 情景分析 / 家庭协同 / 经营咨询 / 我的 |
| `wangtaitai`（家庭成员） | 家庭协同（8 张卡片，字段标签齐全）/ 我的 |
| `zixunxiaoli`（咨询人员） | 咨询工作台 / 事项记录 / 我的 |

其它复核：

| 项目 | 结果 |
| --- | --- |
| 旧后台地址 `/wendai/admin` | 渲染「页面不存在」 |
| 家庭协同卡渲染 | 已共享字段全部显示为中文标签（含最紧时点余额、付款缺口、留底缺口），未登记键显示「其他信息」 |
| 现金事件编号 | 不再出现 `DEMO` 字样 |
| 家庭邀请码 | 不再出现 `DEVDEMO1` |
| 最近日志 ERROR | 0 行 |

### 9.5 收口阶段新发现并修复的问题

| 编号 | 现象 | 处理 |
| --- | --- | --- |
| C4 | 「未来 7 天收付趋势」在无历史数据时把同一句话显示两遍 | 前端只在后端提示未覆盖时补充 |
| C5 | 窄屏情形对比表首列被挤成一字一行 | 容器内横向滚动 |
| C6 | 「待补充的资料」显示 `amount_cents` 等内部字段名 | 只显示业务化原因 |
| C7 | 脚本在 GBK 控制台打印「✓」时 `UnicodeEncodeError`，账户已开通却报错退出 | 标准输出切 UTF-8 |
| C8 | 接收端协同卡漏渲染已共享字段，且显示 `revision_summary` | 统一渲染模型 + 标签补全 |
| C9 | 事项编号无法更正 | 允许更正（唯一性校验 + 版本留痕） |
| C10 | 旧演示编号与邀请码仍对用户可见 | 规范化编号 + 随机邀请码 |

以上 C4–C7 由视觉巡检发现，C8–C10 由公网三账号逐页验收发现；
完整清单与提交对应关系见 `docs/final-product-baseline.md` 第 6 节。

### 9.6 未处理事项（需业务确认，本次未动）

生产库里除三个正式账号外，还有大量**端到端测试残留账号**：

| 项目 | 现状 |
| --- | --- |
| 账号总数 | 321（active 320 / disabled 1） |
| 显示名分布 | 端到端掌柜 270、界面注册掌柜 15、家庭成员小王 11、咨询小李 9、空数据掌柜 7、`????` 4、验收掌柜 1 |
| 经营名称分布 | 端到端小吃店 270、登录测试店 8、空数据小店 7、界面注册小吃店 7、`?????` 3、验收小铺 1、王记小吃店 1 |

这些都是历史验收轮次在公网入口上跑 E2E 产生的（`端到端掌柜 / 端到端小吃店`
正是测试夹具写死的名称），**不是**真实经营者。

本次**没有**做任何停用或删除：

* 停用 300+ 个账号属于生产数据变更，需要业务方确认后才执行；
* 库中还有 `????` / `?????` 这类乱码记录，来源不明，不能靠猜测处置；
* 保留现状对产品功能与三个正式账号的验收结果没有任何影响。

建议的处置方式（非破坏性，与「绝不物理删除」一致）：按
`display_name + business_name + created_at 落在验收时间窗 + 无审计业务痕迹`
四重证据筛选后 `status=disabled`，保留账号、角色与全部历史。


