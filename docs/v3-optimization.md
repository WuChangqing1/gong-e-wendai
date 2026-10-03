# V3 增量优化记录

本文记录 V3 优化在本地分支上的实施内容、验证结果与尚未完成的部分。

- 分支：`feat/wendai-v3-optimization`（已推送）
- 合并点：`bf8a44a`（`main`，已推送 origin）
- 发布 Tag：`v3-optimization-20261003`
- 基线：`docs/v3-baseline.md`（`5ef53ef`，安全 Tag `pre-v3-optimization-20261003`）
- 状态：**已完成并部署到生产**（见 `docs/deployment-record-v3.md`）

---

## 1. 业务身份模型（本次最重要的口径变更）

工 e 稳袋定位为**上层银行 / 商户服务 App 中的一个业务模块**，因此本模块不承担
平台级用户管理与系统运维。

| 项目 | 变更前 | 变更后 |
| --- | --- | --- |
| 业务身份 | `merchant` / `family_member` / `consultant` / `admin` | `merchant` / `family_member` / `consultant` |
| 等级关系 | 存在「管理员 > 普通用户」 | **不存在等级**，三种身份只是不同业务身份 |
| 管理后台 | 3 个页面（概览 / 用户 / 运行状态） | **已删除** |
| 管理接口 | `/api/v1/admin/*` 6 个端点 | **已删除**（访问返回 404） |
| 咨询人员开通 | 管理员在网页创建 | `scripts/provision_consultant.py`（交互式、随机密码） |
| 运维观察 | 网页运行状态面板 | `GET /api/v1/health` + systemd + 日志 |

### 数据迁移 `7b1c4d9e2f30`

只清理角色数据，**不删除任何用户与业务数据**：

1. `merchant` / `family_member` / `consultant` 兼有 `admin` 的账户 → 保留业务身份，仅移除 `admin` 行
2. **仅**有 `admin` 的账户 → 移除角色并把状态置为 `disabled`
   （`get_current_user` 会拒绝非 active 账户登录，因此无法进入系统，但审计与历史数据完整）
3. 写入一条 `audit_logs`（`identity.admin_role_removed`），记录受影响数量

本地真实库实测：`admin_demo` → `disabled` 且角色清空；`merchant_demo` / `family_demo` /
`consultant_demo` 完全不变。迁移 `downgrade` 为**空操作**——把身份悄悄加回去比不加更危险。

---

## 2. 响应式架构

详见 `docs/mobile-design.md`。核心变化：

* `hooks/useResponsive.ts` 成为**唯一断点来源**（mobile `<=767` / tablet `768-1023` /
  desktop `>=1024`），由 `matchMedia` + `useSyncExternalStore` 驱动
* 移除三套并存的断点语义：CSS 767px、Ant Design `Sider breakpoint="lg"`（约 992px）、
  Zustand `isMobile`
* 新增 `ResponsiveDataView`（表格 / 卡片一键切换）与 `ResponsiveDrawer`
  （桌面设计宽度、手机 100vw + 底部固定操作区）
* `store.isMobile` 与 `Sider onBreakpoint` 已删除，侧栏显隐改由 `hasRoomForSidebar` 决定

---

## 3. 移动端体验

| 底部标签栏 | 5 项；情景分析改由首页入口进入（`/analysis` 地址不变） |
| 5 处宽表 | 手机端改为卡片列表（事项 / 咨询 / 咨询工作台 / 家庭成员 / 历史数据） |
| 首页信息顺序 | 结论 → 当前资金 → 最紧张时点 → 图表 → 缺口与家庭协同 → 参考 → 解读 |
| 「我的」 | 手机端为纵向设置列表 + 二级进入（含返回条），桌面端仍为平铺页签 |
| 事件筛选 | 手机端为底部抽屉（时间 / 方向 / 状态 / 类型 + 重置 / 确认），顶部只留搜索与「筛选 (N)」 |
| 图表 | 手机端 `10/3` 短标签、图例移到下方、字号提升、tooltip 支持点击 |
| 延期天数 | 步进器 + 快捷天数（1/2/3/4/7） |
| 表单键盘 | 金额 `inputMode="decimal"`、序号 `numeric`、手机号 `tel`（共 11 处） |
| 细节 | iOS 输入字号 16px（关闭聚焦放大）、安全区适配、底部操作区 >= 44px |

---

## 4. 安全与隐私

* **不存在**管理员身份，因此不存在「用管理接口绕过数据边界」的路径
* 三种身份边界全部由后端强制，并有测试覆盖：
  * 经营者读其他商户事项 → 列表为空、按 id 直读 **404**（不是 403，避免泄露资源存在性）
  * 家庭成员访问 7 个经营接口 → 一律 **403**；无分享时卡片列表为空
  * 咨询人员访问 6 个经营与家庭接口 → 一律 **403**；队列不含任何经营金额字段
  * 任意身份访问 `/api/v1/admin/*` → 一律 **404**
* 家庭分享与咨询使用**两套独立白名单**（不复用 DTO），未勾选字段在持久化结果中不存在
* AI 密钥：`SecretStr` 保存、只存在后端环境变量、不下发前端、不进日志与响应

### AI 密钥定位修复

Windows 的**机器级**环境变量位于
`HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment`，
而 `verify_glm.py` 与 `check_secrets.py` 原先读取 `HKLM\Environment`（XP 时代遗留位置，
现代系统为空）。结果是「系统环境变量已设置 GLM」却被判为不存在：
`verify_glm.py` 直接退出，`check_secrets.py` 退化成只做文件路径检查。

修复后按「进程 → 机器级 → 用户级」顺序查找。真实验证结果：

| 项目 | 结果 |
| --- | --- |
| `glm-4.5-air` 文本提取 | 成功：结算款 1288.00 元 → `amount_cents=128800`、`event_type=settlement` |
| `glm-4.6v` 图片识别 | 成功：接受生成的测试图，返回 5 条 `warnings`，未编造金额 |
| `check_secrets.py` | 恢复完整能力后通过：密钥未出现在任何已跟踪或已暂存文件 |

---

## 5. 首屏包体（实测优化）

手机网络比桌面更慢，因此这里用**真实浏览器统计每个页面实际下载的 js 字节数**，
而不是凭感觉拆包。测量方式：记录 `**/assets/*.js` 响应的实际体积。

| 页面 | 优化前 | 优化后 | 变化 |
| --- | --- | --- | --- |
| 今日决策 | 2192 KB / 4 chunk | 2198 KB / 4 chunk | 持平（图表页本来就需要图表） |
| 现金事件 | 2192 KB / 4 chunk | **1630 KB / 1 chunk** | −562 KB（gzip −174 KB） |
| 家庭协同 | 2192 KB / 4 chunk | **1630 KB / 1 chunk** | −562 KB |
| 我的 | 2192 KB / 4 chunk | **1630 KB / 1 chunk** | −562 KB |

### 根因

`vite.config.ts` 曾把 `echarts` 写进 `manualChunks`。这让它变成**静态 chunk**，
`index.html` 随之对它做 `modulepreload` —— 结果是完全不含图表的页面
（家庭协同、经营咨询、我的）也要下载 542 KB 的 ECharts。

### 处理

* 新增 `components/charts/lazy.tsx`：用 `lazy()` + 动态 `import()` 加载两个图表入口，
  并用 `Suspense` + 固定高度占位（`ChartLoading`）避免布局抖动
* 图表都在「分析结果已就绪」的分支里渲染，因此不会产生多余的数据请求
* 移除 `manualChunks` 中对 `echarts` 与共享依赖的强制分组

### 一个被实测否掉的方案

顺手试了「把 `rc-picker` / `dayjs` 单独成块」，结果**更差**：下载字节数不变
（1630 KB），却多出 1–2 个请求。原因是 antd 被应用外壳（布局、按钮、表单）直接引用，
本来就是每个入口都必然加载的共享依赖，手动切块只带来重复引用与额外往返。

| 方案 | 每个页面下载 | 请求数 |
| --- | --- | --- |
| 固定 antd + react | 1628 KB | 3 |
| 再加 rc-picker / dayjs 单独成块 | 1630 KB | 2~5 |
| **交给 Rollup 自动合并（现方案）** | **1630 KB** | **1** |

结论已写进 `vite.config.ts` 的注释，避免以后重复试错。

---

## 6. 验证结果（全部实跑）

| 项目 | 结果 |
| --- | --- |
| 后端 pytest | **484 项全部通过** |
| 前端 Vitest | **37 项通过**（新增 10 项） |
| 前端 typecheck | 通过 |
| 前端 lint | 通过（`--max-warnings 0`） |
| 前端 build | 通过（入口 chunk 299KB → 291KB） |
| Playwright E2E | **41 通过 / 1 跳过** |
| 视口验收 | 360/375/390/430/768/1024/1440 × 6 路由：零页面级横向滚动、断点行为正确 |
| Alembic | `7b1c4d9e2f30` 在真实数据上迁移成功 |
| Secret check | 通过 |

基线对比：后端 492 → 484（删除 `test_admin.py` 26 项，新增 Admin 已移除 5 项、
跨身份隔离 5 项，净 -8 是测试重组的结果）；前端 27 → 37。

---

## 7. 尚未完成

| 项目 | 说明 |
| --- | --- |
| 生产部署 | **已完成**：`cf83b2d` → `bdfeb07`，迁移至 `7b1c4d9e2f30`，服务 `active` |
| 公网验收 | **已通过**：接口 / 七视口 / 手机端形态 / 图表按需加载，见部署记录第 6 节 |
| 真机验收 | **用户已确认**手机访问正常；公网浏览器验收 4 项通过 |
| 本地 AI 开启 | 本机 `.env` 保持 `AI_ENABLED=false`，避免日常调试消耗额度；密钥连通性已单独验证 |
| 情景对比明细 | 手机端仍为表格（列数少、本身是对比矩阵），保留容器内横向滚动 + 遮罩提示 |
| 剩余首屏体积 | 非图表页面已降至 1630 KB（gzip 533 KB），其中 antd 占约 1307 KB，是当前唯一的大头；进一步下降需要替换重组件（如 DatePicker）或整体换 UI 库，不属于本次范围 |

---

## 8. 提交记录

| 提交 | 内容 |
| --- | --- |
| `dd22c91` | 删除 Admin 产品体系与业务身份等级 |
| `12024c6` | 统一响应式架构 |
| `b71dd03` | 移动端信息重排 |
| `0677caa` | 三身份边界与跨商户隔离测试 |
| `8971e62` | 修复 Windows 机器级环境变量读取（AI 密钥定位） |
| `297245b` | V3 身份模型、响应式规范与验收文档 |
| `3af52e5` | 手机端「我的」列表项与事件筛选底部抽屉 |
| `8122165` | 数值与电话输入的手机键盘提示（`inputMode`） |
| `8437b43` | 断言「视口变化不重复请求接口」 |
