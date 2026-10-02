# HTTPS 访问与子路径部署

## 1. 结论（当前生产实际形态）

```
https://ccqspace.site/wendai/          应用入口（HTTPS，证书由 ccqspace.site 复用）
https://ccqspace.site/wendai/api/v1/   REST 接口（同源，无需 CORS）
```

* 只新增一个 Nginx `location` 块，**未改动**既有站点其他内容
* 应用进程仍只监听 `127.0.0.1:18089`
* 独立端口 `18088` 依然保留可用（服务器本机直连）

## 2. 为什么需要 HTTPS

浏览器对部分站点会自动把 `http://` 升级为 `https://`。如果只提供明文 HTTP 端口，
浏览器发起 TLS 握手会失败，表现为：

```
502 Bad Gateway - The remote server does not speak TLS
```

因此对外入口必须提供**证书有效的 HTTPS**。

## 3. 子路径部署必须同时处理两件事

应用挂在父站点子路径（`/wendai/`）时，有两处路径必须一起改成命名空间路径，
否则会与父站点上其他应用冲突或直接 404/502：

| 项 | 错误做法 | 现象 | 正确做法 |
| --- | --- | --- | --- |
| 静态资源 | 仍用 `/assets/*` | 被父站点其他应用接走，返回 404 `{"detail":"Not Found"}` | 用 `/wendai/assets/*` |
| 接口地址 | 仍用 `/api/v1/*` | 被父站点其他接口接走，返回 502 | 用 `/wendai/api/v1/*` |
| 前端路由 | 未配 `basename` | `/wendai/login` 被当成未知路由，渲染「页面不存在」 | `BrowserRouter basename="/wendai"` |

代码中的对应实现：

* `frontend/vite.config.ts`：`base` 由 `VITE_BASE_PATH` 控制
* `frontend/src/main.tsx`：`routerBasename()` 从 `import.meta.env.BASE_URL` 推导
* `frontend/src/api/client.ts`：`resolveApiBaseUrl()` 从 `BASE_URL` 推导 API 前缀
* `backend/app/main.py`：`_detect_asset_prefix()` 读取构建产物自动挂载静态资源，
  SPA 回退同时支持根路径与子路径；`/api/*` 与 `/wendai/api/*` 未命中一律返回 JSON 404

## 4. 构建

```bash
# 根路径部署（本地开发、独立端口）
cd frontend && npm run build

# 父站点子路径部署
cd ~/apps/gong-e-wendai
BASE_PATH=/wendai/ bash scripts/build_frontend_subpath.sh
```

脚本会校验产物里不再出现根路径 `/assets/` 引用。

## 5. Nginx 配置（已生效）

```nginx
# /etc/nginx/conf.d/mysite.conf 内，插入在 location / 之前
location = /wendai { return 301 /wendai/; }
location /wendai/ {
    proxy_pass http://127.0.0.1:18089/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 60s;
    client_max_body_size 8m;
}
```

修改前的备份位置：`/etc/nginx/conf.d/mysite.conf.bak-before-wendai-<时间戳>`。

## 6. 独立端口形态（可选）

`docker/nginx-gong-e-wendai.conf` 提供另一套独立 server 块（监听 18088，
不依赖父站点）。两种形态可同时存在：

```bash
sudo cp docker/nginx-gong-e-wendai.conf /etc/nginx/conf.d/gong-e-wendai.conf
sudo nginx -t && sudo systemctl reload nginx
```

## 7. 想要独立域名时

申请一个子域并添加 DNS 记录：

```
类型  主机记录   记录值
A     wendai     110.42.236.65
```

生效后即可签发独立证书：

```bash
sudo certbot certonly --nginx -d wendai.ccqspace.site
```

访问地址变为 `https://wendai.ccqspace.site/`，此时构建基路径改回 `/`：

```bash
cd ~/apps/gong-e-wendai && BASE_PATH=/ bash scripts/build_frontend_subpath.sh
```

## 8. 端到端测试

同一套用例同时支持两种形态，通过 `E2E_BASE_URL` 切换：

```bash
cd frontend

# 本地根路径
E2E_BASE_URL=http://127.0.0.1:8000 npx playwright test

# 生产 HTTPS 子路径
E2E_BASE_URL=https://ccqspace.site/wendai npx playwright test
```

实测结果：两种形态各 30 项全部通过（桌面 23 + 移动 7）。

## 9. 排查清单

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 页面能开但一片空白 | 静态资源 404 | 确认用 `VITE_BASE_PATH` 重新构建，并检查 `dist/index.html` 里的资源前缀 |
| 页面提示「页面不存在」 | 缺少路由 `basename` | 确认 `main.tsx` 的 `routerBasename()` 与构建基路径一致 |
| 登录/注册无反应、控制台 502 | API 仍打到根路径 `/api/v1` | 确认 `client.ts` 的 `resolveApiBaseUrl()` 生效并重新构建 |
| `502 ... does not speak TLS` | 访问入口只有明文 HTTP | 使用 HTTPS 入口，或让入口具备有效证书 |
