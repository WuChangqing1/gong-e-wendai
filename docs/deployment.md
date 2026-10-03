# 部署说明

目标：在服务器上以**同源单端口**形态运行，只开放 **TCP 18082**。

```
浏览器 → http://SERVER:18082/         前端页面（FastAPI 托管前端 build）
         http://SERVER:18082/api/v1/  REST 接口
```

不需要为前端单独开放端口，也不需要 Nginx 反向代理（如已有 Nginx，可把 80/443 反代到 18082）。

## 0. 服务器信息

| 项 | 值 |
| --- | --- |
| SSH | `ssh fengz` |
| 用户 | `ubuntu` |
| 主机 | `VM-0-11-ubuntu` |
| 代码目录 | `$HOME/apps/gong-e-wendai` |
| 数据目录 | `$HOME/apps/gong-e-wendai-data` |
| 监听端口 | `18082` |

代码与持久数据分离：

```
$HOME/apps/gong-e-wendai/            # Git 仓库，git pull 更新
$HOME/apps/gong-e-wendai-data/       # 运行数据，永不进入 Git
├── app.db
├── app.db-wal
├── uploads/
├── logs/
└── backups/
```

## 1. 部署前环境检查

**不要假设服务器为空。** 先执行：

```bash
ssh fengz
whoami; hostname; pwd; uname -a
df -h; free -h
python3 --version; node --version; npm --version; git --version
ss -lnt                     # 查看所有监听端口
ss -lnt | grep 18082        # 必须确认端口未被占用
ps aux | head -40
```

### 端口被占用时

如果 `18082` 已被占用：

* **不要** kill 陌生进程
* **不要** 覆盖已有服务
* **停止部署**，记录占用进程信息，并选择下一个可用端口（例如 18083）

```bash
ss -lntp | grep 18082      # 记录占用者
```

## 2. 获取私有仓库

优先使用 GitHub SSH 访问：

```bash
ssh -T git@github.com                      # 验证是否有权限
mkdir -p ~/apps
git clone git@github.com:WuChangqing1/gong-e-wendai.git ~/apps/gong-e-wendai
```

如果没有 SSH 权限：使用部署密钥或 GitHub CLI 登录，**不要把 Token 写入 shell history、脚本或源码**。

## 3. Python 环境

不要修改服务器系统 Python：

```bash
cd ~/apps/gong-e-wendai
python3 -m venv .venv
.venv/bin/pip install --upgrade pip wheel
.venv/bin/pip install -r backend/requirements.txt
```

如果服务器已有合适的 conda，也可以建立专用环境；否则 `venv` 即可。

## 4. 前端构建

```bash
cd ~/apps/gong-e-wendai/frontend
npm ci
npm run build          # 产物在 frontend/dist
```

## 5. 生产环境变量

```bash
cd ~/apps/gong-e-wendai
cp .env.example .env.production
chmod 600 .env.production
openssl rand -hex 32   # 生成 JWT_SECRET
```

`.env.production` 内容（**绝不提交 Git**）：

```env
APP_ENV=production
APP_NAME=gong-e-wendai
APP_HOST=127.0.0.1
APP_PORT=18089

DATABASE_URL=sqlite:////home/ubuntu/apps/gong-e-wendai-data/app.db
SQLITE_BUSY_TIMEOUT_MS=5000

JWT_SECRET=<openssl rand -hex 32 的输出>
JWT_ALGORITHM=HS256
JWT_ACCESS_EXPIRE_MINUTES=30
JWT_REFRESH_EXPIRE_DAYS=14
COOKIE_SECURE=false          # 站内为 HTTPS 转发，按需改为 true

CORS_ORIGINS=               # 同源部署，留空

UPLOAD_DIR=/home/ubuntu/apps/gong-e-wendai-data/uploads
MAX_UPLOAD_MB=5
LOG_LEVEL=INFO
LOG_DIR=/home/ubuntu/apps/gong-e-wendai-data/logs

# 智能服务（智谱 GLM）
AI_ENABLED=true
GLM=<智谱 API Key，通过 stdin 写入，绝不走命令行参数>
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
GLM_TEXT_MODEL=glm-4.5-air
GLM_VISION_MODEL=glm-4.6v
GLM_TIMEOUT_SECONDS=30
GLM_VISION_TIMEOUT_SECONDS=45
```

生产启动前应用会自检：`JWT_SECRET` 必须是至少 32 字符的随机值、`CORS_ORIGINS` 不允许 `*`，
不满足时**拒绝启动**。

### 5.1 安全写入 GLM 密钥

密钥只允许通过 **stdin** 传输到服务器上的安全更新脚本，不得出现在 SSH 命令行参数、
Git URL、shell 历史或临时脚本源码里：

```bash
# 本地（PowerShell）：把 Machine 作用域的 GLM 通过管道发送，不落盘、不打印
[Environment]::GetEnvironmentVariable('GLM','Machine') |
  ssh fengz "~/apps/gong-e-wendai/scripts/update_glm_env.sh"

# 脚本只输出：GLM credential configured
```

检查时只能确认变量存在，**绝不** `cat` 整个 `.env.production`。

## 6. 数据库迁移

```bash
cd ~/apps/gong-e-wendai/backend
set -a; . ../.env.production; set +a
../.venv/bin/python ../scripts/backup_db.py   # 先备份
../.venv/bin/python -m alembic upgrade head
../.venv/bin/python -m alembic check
```

**顺序很重要**：必须先完成迁移再重启服务。引擎 2.0.0 引入了 5 张新表，
旧代码不需要它们，但新代码启动后第一次写入事项就会访问 `merchant_analysis_states`。

生产首次启动只运行迁移，**不会**生成任何账户或种子数据。

咨询人员身份按需开通（密码随机生成、写入 `0600` 文件、不打印到终端）：

```bash
cd ~/apps/gong-e-wendai/backend
../.venv/bin/python ../scripts/provision_consultant.py --username zixunxiaoli --name 咨询小李
# 输出：✓ 咨询人员账户已创建：zixunxiaoli
#      凭据已写入 /home/ubuntu/consultant-credentials.txt（权限 600），未打印到终端
```

登录后立即修改密码，然后删除该凭据文件。

> V3 起本系统**没有 admin 身份**，也没有管理员后台：平台级用户管理与运维由上层
> 银行 / 商户服务 App 承担。运维只通过 `systemctl --user status gong-e-wendai`、
> `journalctl --user -u gong-e-wendai` 与 `GET /api/v1/health` 观察。

## 7. 一键部署

仓库已提供部署脚本：

```bash
cd ~/apps/gong-e-wendai
bash scripts/deploy.sh --check     # 只做环境检查
bash scripts/deploy.sh             # 完整部署（拉取 / 依赖 / 构建 / 迁移 / 重启 / 健康检查）
bash scripts/deploy.sh --no-pull   # 跳过 git pull
```

## 8. 进程管理

### 8.1 systemd user service（首选）

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/gong-e-wendai.service <<'UNIT'
[Unit]
Description=工 e 稳袋 (gong-e-wendai)
After=network.target

[Service]
Type=simple
WorkingDirectory=%h/apps/gong-e-wendai/backend
EnvironmentFile=%h/apps/gong-e-wendai/.env.production
ExecStart=%h/apps/gong-e-wendai/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 18082 --workers 1
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now gong-e-wendai
systemctl --user status gong-e-wendai
```

如需在未登录时保持运行：`sudo loginctl enable-linger ubuntu`。

### 8.2 nohup 兜底方案

若当前用户无法使用 systemd user service（也无 sudo 权限），使用仓库提供的脚本：

```bash
cd ~/apps/gong-e-wendai
bash scripts/start.sh      # 启动，写入 $DATA_DIR/app.pid
bash scripts/status.sh     # 查看状态
bash scripts/restart.sh    # 重启
bash scripts/stop.sh       # 停止
tail -f ~/apps/gong-e-wendai-data/logs/app.out.log
```

脚本会：读取 `.env.production`、单 worker 启动、记录 PID、把 stdout/stderr 写入日志目录。

## 9. 健康验证

```bash
curl -fsS http://127.0.0.1:18082/api/v1/health
# {"status":"ok","database":"ok","ai_enabled":false,"version":"1.0.0","env":"production"}

curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18082/
# 200

for path in today events analysis family consultations settings; do
  printf '%s -> ' "$path"
  curl -fsS -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:18082/$path"
done
# 全部 200（SPA 回退）

curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18082/api/v1/does-not-exist
# 404，且响应体是 JSON 而不是 index.html

ss -lnt | grep 18082
```

## 10. 备份

```bash
cd ~/apps/gong-e-wendai/backend
set -a; . ../.env.production; set +a
../.venv/bin/python ../scripts/backup_db.py --keep 14
```

备份写入 `~/apps/gong-e-wendai-data/backups/`，不进入 Git。

建议加入 crontab：

```cron
15 3 * * * cd $HOME/apps/gong-e-wendai/backend && set -a && . ../.env.production && set +a && $HOME/apps/gong-e-wendai/.venv/bin/python ../scripts/backup_db.py --keep 14 >> $HOME/apps/gong-e-wendai-data/logs/backup.log 2>&1
```

## 11. 更新发布

```bash
cd ~/apps/gong-e-wendai
bash scripts/deploy.sh             # 自动：git pull → 依赖 → 构建 → 迁移 → 重启 → 健康检查
```

数据目录独立，`git pull` / `git reset --hard` **不会**影响数据库与上传文件。

## 12. 常见问题

| 现象 | 排查方向 |
| --- | --- |
| 启动即退出 | `.env.production` 是否满足生产自检（JWT_SECRET 长度、CORS 通配符） |
| 页面 404 | `frontend/dist` 是否存在；重新执行 `npm run build` |
| `/api/*` 返回 HTML | 不应发生；确认请求路径以 `/api/` 开头 |
| 数据库锁定 | 确认只有 1 个 worker；检查 `SQLITE_BUSY_TIMEOUT_MS` |
| 写入事项报「服务处理失败」且日志出现 `no such table: merchant_analysis_states` | 迁移未执行：`alembic upgrade head` 后重启服务 |
| 智能服务不可用 | 检查 `AI_ENABLED` / `GLM` / `GLM_BASE_URL` / `GLM_TEXT_MODEL`；不影响其他功能 |
| 截图识别不可用但文字可用 | 检查 `GLM_VISION_MODEL` 与账户是否开通视觉模型额度 |
| 留底确认返回 409 | 期间数据已更新（revision 或 basis_hash 变化），重新查看建议后再确认 |
| 「历史记录还不够」 | 历史完整日不足算法要求；核心功能照常可用，补录历史后自动出现参考 |
| 端口被占用 | 不要 kill 陌生进程；报告占用情况并更换端口 |

## 13. 端口与入口现状

| 项目 | 值 |
| --- | --- |
| 对外入口 | `https://ccqspace.site/wendai/`（复用现有站点证书） |
| Nginx 监听 | `0.0.0.0:18088`，`/wendai/` 反向代理 |
| 应用监听 | `127.0.0.1:18089` |
| 服务单元 | systemd user service `gong-e-wendai` |

> 原始规格要求 TCP 18082，但该端口已被既有项目占用（烟厂制丝线物流智能监控平台）。
> 按「不得破坏既有项目」的约束，本项目改用 18088（Nginx）→ 18089（应用），
> 既有 18082 / 18085 / `/home/` 服务未受影响。详见
> `docs/https-subpath-deployment.md`。
