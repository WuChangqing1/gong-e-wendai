# 部署记录（增强 v2）

本文记录**增强 v2**（引擎 2.0.0）在生产服务器上的实际部署过程与结果。

## 1. 部署信息

| 项目 | 值 |
| --- | --- |
| 部署日期 | 2026-10-03 |
| 发布提交 | `fab18e2`（部署时）→ 后续 `dcf906e`、`9aea287` |
| 服务器 | `ssh fengz` → `ubuntu@VM-0-11-ubuntu` |
| 代码目录 | `~/apps/gong-e-wendai` |
| 数据目录 | `~/apps/gong-e-wendai-data` |
| 解释器 | `~/apps/gong-e-wendai/.venv/bin/python`（Python 3.12.15） |
| 服务 | systemd user service `gong-e-wendai` |
| 应用监听 | `127.0.0.1:18089`（单 worker） |
| 对外入口 | `https://ccqspace.site/wendai/` |

## 2. 备份（部署前完成）

| 项目 | 路径 |
| --- | --- |
| 数据库备份 | `~/apps/gong-e-wendai-data/backups/app-20261003-034638.db`（3,334,144 字节） |
| Nginx 配置备份 | `/etc/nginx/conf.d/mysite.conf.bak-before-v2-20261003114638` |
| 代码目录备份 | `~/apps/gong-e-wendai-pre-v2-20261003114638.tar.gz`（321,980 字节） |

备份脚本使用 SQLite 在线备份 API（`Connection.backup`），在 WAL 模式下也能得到一致快照。

## 3. 更新方式

服务器可以访问公网（`api.github.com` 返回 200），但**没有 GitHub 凭据**，
因此 `git fetch origin` 会失败。部署改用本地打包的 git bundle：

```bash
# 本地
git bundle create gew-main.bundle main
scp gew-main.bundle fengz:/tmp/

# 服务器
cd ~/apps/gong-e-wendai
BUNDLE=/tmp/gew-main.bundle bash scripts/deploy_v2.sh
```

bundle 方式保留完整历史，服务器仓库最终指向与 GitHub 完全一致的提交。

## 4. 部署步骤与结果

| 步骤 | 结果 |
| --- | --- |
| 1 备份数据库 | 成功，生成 `app-20261003-034638.db` |
| 2 备份 Nginx 配置 | 成功，其他 location 未改动 |
| 3 备份代码目录 | 成功，生成 tar 包 |
| 4 更新代码 | `d8dc6e8..fab18e2`，工作树与 origin/main 一致 |
| 5 安装 Python 依赖 | `deps ok` |
| 6 数据库迁移 | `0ee06e0f0f90` → `6e784eb94252 (head)` |
| 7 构建前端 | `VITE_BASE_PATH=/wendai/`，产物含 `index-swFOtJQA.js` |
| 8 重启服务 | `active`，`NRestarts=0` |

**迁移先于重启**：新代码启动后第一次写入事项就会访问 `merchant_analysis_states`。
本地开发时曾因顺序错误（先重启后迁移）导致 `no such table: merchant_analysis_states`，
该教训已写入 `docs/deployment.md` 的常见问题。

## 5. 迁移对既有数据的影响

| 项目 | 结果 |
| --- | --- |
| 分析结果总数 | 保留，无删除 |
| 旧引擎结果 | 标记 `is_stale`，`stale_reason` 说明「计算引擎已升级到 2.0.0」 |
| 商户分析状态 | 为每个既有商户回填一行（两个 revision 从 1 开始） |
| 旧用户 / 旧 CashEvent / 旧家庭卡 / 旧咨询 | 全部保留 |

## 6. 智能服务配置

通过 stdin 写入 `.env.production`，文件权限 `600`：

```
AI_ENABLED=true
GLM=<通过 stdin 写入，未出现在任何命令行、日志或终端输出中>
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
GLM_TEXT_MODEL=glm-4.5-air
GLM_VISION_MODEL=glm-4.6v
GLM_TIMEOUT_SECONDS=30
GLM_VISION_TIMEOUT_SECONDS=45
```

### 6.1 一个真实踩坑：管道引入了 UTF-8 BOM

从 Windows PowerShell 通过管道传输密钥时，开头被加上了 U+FEFF（BOM），
密钥长度从 49 变成 50，`Authorization` 头在 ascii 编码时抛 `UnicodeEncodeError`，
对外表现为「智能服务暂时不可用」，而智谱接口实际是可达的（无 Key 时返回 401）。

修复：`scripts/setup_ai_env.sh` 会剥离 BOM 与零宽字符，并**拒绝**任何包含
非 ASCII 字节的值——让损坏在写入时就大声失败，而不是在请求时静默降级。

### 6.2 连通性验证（服务器本机执行）

| 项目 | 结果 |
| --- | --- |
| DNS + HTTPS 到 `open.bigmodel.cn` | 可达（无 Key 返回 401） |
| `glm-4.5-air` 文本提取 | 成功：结算款 1288.00 元 → `amount_cents=128800`、`settlement` |
| `glm-4.6v` 截图识别 | 成功：接受生成的无敏感信息测试图，返回 5 条 `warnings`，未编造金额 |
| 密钥输出 | 全程未打印任何密钥或片段 |

## 7. 管理员账户整改

部署前生产库中 `wangzhanggui` 是**唯一**管理员，同时又是经营者账号。

按「不能删除最后一个管理员」的约束，采用先建后删的顺序：

1. 审计确认管理员只有 `wangzhanggui`
2. 用 `scripts/provision_admin.py` 创建独立管理员 `xitongguanli`，
   密码随机生成、写入 `~/admin-credentials.txt`（`0600`）、**不打印到终端**
3. 复核确认存在两个管理员
4. 安全脚本检查「至少有 2 个管理员」且「`xitongguanli` 存在」后才执行移除
5. 移除 `wangzhanggui` 的 `admin` 角色，保留其 `merchant` 角色，并写入审计日志
6. 复核：管理员只剩 `xitongguanli`，`merchant+admin` 为空

结果：

| 账户 | 角色 |
| --- | --- |
| `xitongguanli` | `admin`（独立管理员） |
| `wangzhanggui` | `merchant`（不再拥有管理员权限） |
| `wangtaitai` | `family_member` |
| `zixunxiaoli` | `consultant` |

管理员凭据位于服务器 `~/admin-credentials.txt`，请登录后立即修改密码并删除该文件。

## 8. 生产验收

### 8.1 端到端验收（服务器本机，`scripts/_prod_acceptance.sh`）

全部通过，共 40 项，涵盖：

* 健康检查（`status=ok`、`database=ok`、`ai_enabled=true`、`env=production`）
* 9 个页面路由全部 200
* API 404 返回 JSON 而不是 `index.html`
* 5 个受保护接口未登录返回 401
* 咨询人员与管理员自助注册均被拒
* 计算口径：按时 **1200.00 / FEASIBLE**；延迟 **0 / PAYMENT_GAP / 200 / 800**；
  共同约束 **PAYMENT_GAP** 且顶层缺口与状态一致（200）
* 增强接口：`forecast_affects_withdrawable=false`、`basis_hash`、`ledger_revision`、
  历史不足时友好降级、总览与引擎口径一致
* 留底确认依据不匹配返回 **409 STALE_RESERVE_ADVICE**
* `/ai/status` 返回 `available=true`、两个模型名，且无凭据痕迹
* 最近日志 0 行 ERROR
* 既有 **18082 / 18085 仍为 200**

### 8.2 三角色权限验收（服务器本机，`scripts/_prod_roles.sh`）

全部通过，共 31 项：

* 管理员：可读系统概览与账户列表；**不能**读取商户经营数据（403）
* 商户：可读 6 个经营接口；不能访问管理接口（403）；不再拥有 admin 角色
* 家庭成员：经营与增强接口全部 403；可读自己的家庭
* 咨询人员：可读咨询队列；7 个经营与家庭接口全部 403；咨询队列不含任何经营金额

### 8.3 浏览器端到端

| 环境 | 结果 |
| --- | --- |
| 本地同源生产形态 | **36 通过 / 2 跳过** |
| 公网 `https://ccqspace.site/wendai` | 见下方说明 |

公网运行在网络抖动时会出现超时失败，而同时：

* 服务端日志 **0 行 ERROR**
* 服务器负载约 0.09（4 核）
* 内存可用 2.6 GB
* 单独重跑失败用例**全部通过**

因此这是测试链路（公网往返 + 每个用例新建账号触发的 Argon2 哈希）与默认超时的
匹配问题，不是产品或服务器缺陷。`playwright.config.ts` 已按目标地址自动放宽超时
（远程目标 ×2）并启用 1 次重试，本机目标保持原有严格超时。

## 9. 部署后的服务状态

| 项目 | 值 |
| --- | --- |
| systemd | `active`，`enabled`，`Restart=always`，`NRestarts=0` |
| Nginx | 配置语法 OK，`/wendai/` 反代 `127.0.0.1:18089` |
| 公开入口 | `https://ccqspace.site/wendai/` 200 |
| 既有 18082 | 200（未受影响） |
| 既有 18085 | 200（未受影响） |
| 日志 ERROR | 0 |

## 10. 端口说明

原始规格要求 TCP 18082，但该端口已被既有项目（烟厂制丝线物流智能监控平台）占用。
按「不得破坏既有项目」的约束，本项目使用：

```
公网 HTTPS  https://ccqspace.site/wendai/
   ↓  Nginx 0.0.0.0:18088  location /wendai/
   ↓  反向代理
应用  127.0.0.1:18089（单 worker）
```

既有 18082 / 18085 / `/home/` 服务全部保持正常。未申请、未开放、未迁移任何新公网端口。
