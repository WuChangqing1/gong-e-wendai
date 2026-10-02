# 生产部署实例记录（实测）

本文件记录 **2026-10-02** 在真实服务器上的部署结果与实测数据。

## 1. 服务器环境（实测）

| 项 | 实测值 |
| --- | --- |
| SSH | `ssh fengz` |
| 用户 / 主机 | `ubuntu` / `VM-0-11-ubuntu` |
| 系统 | Linux 5.15.0-171-generic x86_64 |
| 磁盘 | 40G，已用 9.7G，可用 28G |
| 内存 | 3.6 GiB |
| 系统 Python | 3.10.12（**低于项目要求**） |
| Node / npm | v22.23.3 / 10.9.9 |
| git | 2.34.1 |
| sudo | 可用且免密 |
| systemd user | 可用（`systemctl --user`） |
| linger | 已启用（未登录也保持运行） |
| conda / gh / docker | 均未安装 |
| pypi.org | **不可达**（超时） |
| pypi.tuna.tsinghua.edu.cn | 可达 |
| registry.npmmirror.com | 可达 |
| repo.anaconda.com / 清华 anaconda 镜像 | 可达 |
| GitHub SSH（`git@github.com`） | **未授权**（Permission denied publickey） |

## 2. 端口占用情况（实测）

| 端口 | 状态 | 说明 |
| --- | --- | --- |
| 80 / 443 | 占用 | 既有 fitness 站点（Nginx） |
| 18080 | 占用 | 仅 127.0.0.1，既有服务后端 |
| 18081 | 占用 | 仅 127.0.0.1，既有服务后端 |
| **18082** | **占用** | 既有「烟厂制丝线物流智能监控平台」（`smoking-monitoring-system-api`） |
| 18083 | 占用 | 既有服务 |
| 18084 | 占用 | 仅 127.0.0.1，既有服务后端 |
| 18085 | 占用 | 既有服务 |
| 18086 | 占用 | 仅 127.0.0.1，既有服务后端 |
| 8000 | 占用 | 仅 127.0.0.1（llm-api-platform） |

### 与规格的偏差（重要）

任务规格要求使用 **TCP 18082**，但该端口已被既有项目占用。
按照任务约束「不允许 kill 陌生进程、不允许覆盖已有服务」，
**本项目未使用 18082**，改用 **18088**，并按既有服务的做法：

```
公网 :18088  →  Nginx（独立 server 块，未改动任何已有配置）
                  ↓
              应用进程 127.0.0.1:18089
```

已确认既有服务未受影响：18082、18085、80 均保持原有响应。

## 3. 部署布局（实测）

```
/home/ubuntu/apps/gong-e-wendai/                 代码（Git 仓库）
├── .venv/                                       Python 3.12 虚拟环境
├── .env.production                              生产变量（chmod 600，不入 Git）
├── backend/  frontend/  docs/  scripts/  docker/
└── frontend/dist/                               前端构建产物

/home/ubuntu/apps/gong-e-wendai-data/            持久数据（不入 Git）
├── app.db  (+ -wal / -shm)
├── uploads/
├── logs/  (app.log, service.log)
└── backups/
```

## 4. 实际使用的命令

### 4.1 Python 3.12 环境（系统 Python 为 3.10，未改动系统环境）

```bash
curl -fsSL -o /tmp/miniconda.sh \
  https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash /tmp/miniconda.sh -b -p "$HOME/miniconda3"

# Anaconda 官方频道需要接受 ToS，改用清华镜像频道
conda create -y -n gonghangcup --override-channels \
  -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main \
  -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/free \
  python=3.12 pip

cd ~/apps/gong-e-wendai
conda run -n gonghangcup python -m venv .venv
./.venv/bin/pip install -r backend/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

实测结果：`.venv/bin/python --version` → **Python 3.12.15**，
`fastapi 0.142.2 / sqlalchemy 2.1.2 / pydantic 2.13.5 / PyJWT 2.15.1`。

### 4.2 私有仓库拉取

服务器没有 GitHub SSH 授权，也没有 `gh`，因此使用 **HTTPS + 一次性凭据**：

```bash
printf '#!/bin/sh\nif [ "$1" = "username" ]; then echo x-access-token; else echo "$GEW_TOKEN"; fi\n' > /tmp/askpass.sh
chmod 700 /tmp/askpass.sh
GIT_ASKPASS=/tmp/askpass.sh GEW_TOKEN="$TOKEN" git clone \
  https://github.com/WuChangqing1/gong-e-wendai.git ~/apps/gong-e-wendai
rm -f /tmp/askpass.sh
```

* Token 只通过环境变量传入，**未写入任何文件、脚本或 Git 配置**
* 克隆后 `git remote set-url` 改回不含凭据的 HTTPS 地址
* 输出经 `sed` 过滤，日志中不出现 Token

### 4.3 前端构建

```bash
npm config set registry https://registry.npmmirror.com
cd ~/apps/gong-e-wendai/frontend
npm ci --no-audit --no-fund
npm run build
```

### 4.4 数据库迁移

```bash
cd ~/apps/gong-e-wendai/backend
set -a; . ~/apps/gong-e-wendai/.env.production; set +a
../.venv/bin/alembic upgrade head     # 0053fb3fa9b7 -> 0ee06e0f0f90
../.venv/bin/alembic check            # No new upgrade operations detected.
```

实测：数据库 22 张表（含 `alembic_version`）。

### 4.5 服务启动（systemd user service）

```bash
cp docker/gong-e-wendai.service ~/.config/systemd/user/gong-e-wendai.service
systemctl --user daemon-reload
systemctl --user enable --now gong-e-wendai
sudo loginctl enable-linger ubuntu
```

实测：`systemctl --user is-active` → `active`，`NRestarts=0`，
`Restart=always`，日志写入 `~/apps/gong-e-wendai-data/logs/service.log`。

### 4.6 Nginx（独立 server 块）

```bash
sudo cp docker/nginx-gong-e-wendai.conf /etc/nginx/conf.d/gong-e-wendai.conf
sudo nginx -t          # syntax is ok / test is successful
sudo systemctl reload nginx
```

未修改 `/etc/nginx/sites-enabled/fitness` 与任何已有 `conf.d` 文件。

## 5. 生产验证结果（实测）

以下全部在服务器上通过 `curl` 实测（应用经 Nginx，即 `http://127.0.0.1:18088`）：

### 5.1 健康与路由

| 检查 | 结果 |
| --- | --- |
| `GET /api/v1/health` | `{"status":"ok","database":"ok","ai_enabled":false,"version":"1.0.0","env":"production"}` |
| `GET /` | 200 `text/html` |
| `GET /today` | 200 |
| `GET /events` | 200 |
| `GET /analysis` | 200 |
| `GET /family` | 200 |
| `GET /consultations` | 200 |
| `GET /settings` | 200 |
| `GET /api/v1/does-not-exist` | 404 `application/json`（不是 index.html） |
| `ss -lnt` | 应用 127.0.0.1:18089；Nginx 0.0.0.0:18088 |
| 日志 ERROR 计数 | 0 |

### 5.2 业务链路（固定算例，期初 600 元 / 留底 600 元）

| 检查 | 结果 |
| --- | --- |
| 按当前计划 | 状态 `OK`，今日可提用 **1200.00 元**，最紧时点余额 1800.00 元，限制事项「供应商货款」 |
| 到账延迟（8 天） | 状态 `PAYMENT_GAP`，可提用 **0.00 元**，付款缺口 400.00 元，留底缺口 1000.00 元 |
| 共同约束 | 可提用 **0.00 元**，绑定情景「到账延迟」，返回 2 条情景曲线 |
| 修改金额 | 版本 1 → 2，金额 1000.00 → 1500.00 |
| 版本对比 | 版本数 2，变更字段 `['amount_cents']`，`¥1000.00 -> ¥1500.00`，`material = True` |
| 来源追溯 | 来源类型 `manual`，有来源记录，来源说明「采购合同 HT-2025-018」 |
| 修改后重算 | 可提用 700.00 元，状态 `OK`，最紧时点 1300.00 元 |
| 结果失效标记 | `is_stale = True`，原因「收付款事项发生变更」 |
| 重复事项编号 | `DUPLICATE_CASH_KEY` |
| 咨询创建 | 编号 `ZX202610020001`，状态 `submitted`，共享字段 9 项 |
| 智能服务降级 | `AI_UNAVAILABLE` /「智能服务暂时不可用，请手动完成当前操作」 |
| 降级后核心功能 | `/me` 200，`/cash-events` 200 |
| 越权：他人事项详情 | 404 |
| 越权：他人事项修改 | 404 |
| 未登录访问 | 401 |

### 5.3 配置自检

| 检查 | 结果 |
| --- | --- |
| `.env.production` 权限 | 600 |
| `JWT_SECRET` 长度 | 64（`openssl rand -hex 32`） |
| `settings.validate_production()` | `[]`（无问题） |
| `APP_ENV` | `production`（`/api/docs` 与 `/openapi.json` 自动关闭） |
| `CORS_ORIGINS` | 空（同源部署） |

## 6. 未完成项（需要用户处理）

### 6.1 公网访问 18088 需要放行安全组

服务器内部一切正常，但从公网访问 `http://110.42.236.65:18088/` 返回 **502**，
而同样部署方式的既有服务（18082、18085）公网可访问。

原因：**云厂商（腾讯云）安全组未放行 18088**。服务器本地防火墙（ufw 未启用、
iptables INPUT 策略为 ACCEPT）没有拦截，Nginx 配置也已通过 `nginx -t` 并正常服务。

需要用户在云控制台放行：

```
入站规则：TCP 18088  来源 0.0.0.0/0  策略 允许
```

放行后访问地址为：

```
http://110.42.236.65:18088/
```

### 6.2 GitHub 持续推送

服务器无 GitHub SSH 授权，因此当前**不支持在服务器上执行 `git push`**；
本地已完成全部推送（`origin/main` 为最新）。

如需服务器也能推送，可在服务器生成部署密钥并加入仓库 Deploy Keys（只读即可满足部署需求）。

## 7. 运维命令速查

```bash
# 状态
systemctl --user status gong-e-wendai
curl -fsS http://127.0.0.1:18088/api/v1/health

# 日志
tail -f ~/apps/gong-e-wendai-data/logs/service.log
tail -f ~/apps/gong-e-wendai-data/logs/app.log

# 重启
systemctl --user restart gong-e-wendai

# 更新发布
cd ~/apps/gong-e-wendai && git pull && \
  frontend/../.venv/bin/pip install -r backend/requirements.txt && \
  (cd frontend && npm ci && npm run build) && \
  (cd backend && set -a && . ../.env.production && set +a && ../.venv/bin/alembic upgrade head) && \
  systemctl --user restart gong-e-wendai

# 备份
cd ~/apps/gong-e-wendai/backend && set -a && . ../.env.production && set +a && \
  ../.venv/bin/python ../scripts/backup_db.py --keep 14

# 创建管理员
cd ~/apps/gong-e-wendai/backend && \
  ../.venv/bin/python ../scripts/create_admin.py --username admin --password 'YourPass123'
```
