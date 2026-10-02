# 安全设计

## 1. 密码

* 哈希算法：**Argon2**（`pwdlib[argon2]`，`time_cost=3, memory_cost=64MiB, parallelism=4`）
* 数据库中只保存哈希，任何位置（日志、审计、接口响应）都不出现明文
* 注册与修改密码时要求：至少 8 位，且同时包含字母和数字
* 登录失败不区分「账户不存在」与「密码错误」，统一返回「用户名或密码不正确」
* 修改密码后撤销该账户的**全部**刷新会话

## 2. 会话与令牌

| 项 | 取值 |
| --- | --- |
| Access Token | HS256 JWT，默认 30 分钟 |
| Refresh Token | HS256 JWT，默认 14 天，落库并可撤销 |
| 存储 | HttpOnly + SameSite=Lax Cookie（`gew_access` / `gew_refresh`） |
| `Secure` | `COOKIE_SECURE=true`（启用 HTTPS 时） |
| 刷新 | 轮换：旧 refresh 立即撤销，签发新的 |
| 退出 | 撤销当前 refresh 会话并清除 Cookie |

脚本与移动端可使用 `Authorization: Bearer <access_token>`，此时不要求 CSRF 标头。

## 3. CSRF 防护

Cookie 认证下，任何写操作（POST / PUT / PATCH / DELETE）必须携带：

```
X-Requested-With: XMLHttpRequest
```

跨站表单无法伪造自定义标头；缺失时返回 `403 CSRF_HEADER_MISSING`。

## 4. 越权防护

**所有资源访问都在后端校验，不依赖前端隐藏按钮。**

| 场景 | 实现 |
| --- | --- |
| 商户 A 访问商户 B 的事项 / 分析 / 情景 / 导入批次 / 咨询 | 资源查询强制带 `merchant_id`，不存在即 404 |
| 家庭成员访问收付款事项 / 经营账户 / 分析 / 导入 / 咨询 | 路由依赖要求 `merchant` 角色，返回 403 |
| 家庭成员读取未分享的协同卡 | `household_card_recipients` 校验，返回 403 |
| 咨询人员读取家庭内容 / 经营余额 / 可提用金额 | 咨询字段白名单，响应中不含这些字段 |
| 咨询人员修改商户收付款事项 | 路由要求 `merchant` 角色，返回 403 |
| 普通用户访问管理员接口 | 路由要求 `admin` 角色，返回 403 |
| 未登录访问业务接口 | 返回 401 |
| 通过修改 URL 中的 ID 访问他人数据 | 全部走 ownership 校验，返回 404 |

管理员**不是**经营主体，同样不能读取商户经营明细。

## 5. 输入校验

* 请求体使用 Pydantic v2 严格校验，非法数据返回 422 且带字段级原因
* 金额：整数分，非负，上限 10^13，禁止浮点
* 时间：ISO-8601 解析，无时区按 Asia/Shanghai 解释
* 长度上限：名称 128、备注 2000、咨询问题 2000、评论 500
* 枚举：`direction` / `state` / `event_type` / `question_type` / `card_type` / `reaction` 全部白名单

## 6. 上传安全

* 扩展名白名单：`.csv` / `.txt` / `.tsv`
* 文件大小上限（`MAX_UPLOAD_MB`，默认 5 MB）
* 磁盘使用随机文件名（`secrets.token_hex(16)`），**不使用客户端文件名作为路径**
* 上传目录不在 Git 中，也不由静态文件服务直接暴露
* 解析失败（编码无法识别、缺表头、内容为空）返回明确错误

## 7. 数据保护

* 数据库中时间统一 UTC，金额统一整数分
* 历史业务记录**不提供物理删除**，只通过 `state = cancelled` 撤销
* 版本历史追加写，`before_json` / `after_json` 保留完整变更前后快照
* 每个事项都有来源记录与内容指纹

## 8. 审计日志

记录重要操作，**禁止记录**以下内容（写入前统一走 `redact()`）：

```
password / new_password / old_password / current_password
token / access_token / refresh_token / jwt / jwt_secret
api_key / authorization / cookie / secret
```

## 9. 错误与日志

* 所有错误响应统一为 `{code, message, details}`，不返回堆栈
* 未处理异常记录完整堆栈到服务端日志，对客户端只返回「服务处理失败，请稍后重试」
* 生产环境（`APP_ENV=production`）自动关闭 `/api/docs` 与 `/api/openapi.json`
* 生产启动前校验：`JWT_SECRET` 必须是至少 32 字符的随机值；`CORS_ORIGINS` 不允许 `*`；
  不满足时**拒绝启动**
* 安全响应头：`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、
  `Referrer-Policy: strict-origin-when-cross-origin`

## 10. CORS

* 开发：允许 `http://localhost:5173` 与 `http://127.0.0.1:5173`
* 生产：同源部署，`CORS_ORIGINS` 留空，不下发 CORS 头
* 未使用 `allow_origins=["*"]`

## 11. 智能服务密钥

* `AI_API_KEY` 只存在于后端环境变量（`.env` / 服务单元），**从不下发前端**
* `/api/v1/ai/status` 只返回 `enabled` / `configured` / `available` / `model`，不返回密钥
* 前端没有任何界面可以查看或获取 API Key
* 智能服务日志只记录状态码与失败原因，不记录请求体中的原文

## 12. 前端不泄露开发信息

页面不出现：

```
localhost / 开发服务器地址 / Python Traceback / SQL 错误
内部文件路径 / 模型 API Key / 数据库路径
```

智能服务不可用时只显示：「智能服务暂时不可用，请手动完成当前操作。」

## 13. Git 与凭据

`.gitignore` 排除：

```
.env / .env.*（保留 .env.example）
*.db / *.db-wal / *.db-shm / *.sqlite
uploads/ / logs/ / backups/ / data/
node_modules/ / .venv/ / __pycache__/
```

`.env.production`、JWT Secret、AI Key、数据库与上传文件均不进入 Git。

## 14. 生产运行

* 单应用实例、单 worker（配合 SQLite）
* SQLite 启用 `WAL`、`foreign_keys`、`busy_timeout`、`synchronous=NORMAL`
* 只开放 TCP 18082；前端与 API 同源，不需要额外端口
* 定期备份：`scripts/backup_db.py`
