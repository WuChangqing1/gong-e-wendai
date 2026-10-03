# 移动端设计规范

本文记录「工 e 稳袋」响应式与移动端的设计约定。**桌面与手机是同一个项目、同一套 URL、
同一套接口**，不存在独立的 `/m` 路由或第二套页面。

- 正式地址：`https://ccqspace.site/wendai/`
- 手机与桌面访问**同一个地址**，由视口宽度决定展示形态

---

## 1. 断点

| 名称 | 宽度 | 布局 |
| --- | --- | --- |
| mobile | `<= 767px` | 隐藏左侧栏；底部标签栏承担导航；宽表格改卡片列表 |
| tablet | `768 - 1023px` | 保留侧栏形态，内容按较窄宽度排布 |
| desktop | `>= 1024px` | 左侧栏 + 顶部栏 + 高信息密度表格与图表 |

**唯一来源**：`frontend/src/hooks/useResponsive.ts`

```ts
export const MOBILE_MAX = 767;          // 与 CSS @media (max-width: 767px) 一致
export const TABLET_MAX = 1023;
export const SIDEBAR_MIN_WIDTH = 1024;  // 与 CSS @media (max-width: 1023px) 一致

useResponsive() // → { isMobile, isTablet, isDesktop, hasRoomForSidebar }
```

约束：

* 页面组件**不得**自行判断 `window.innerWidth`，也不得写 767 / 992 / 1024 这类魔法数字
* 判定由 `window.matchMedia` + `useSyncExternalStore` 驱动：只有**跨越断点**才重新渲染，
  逐像素 resize 不会触发，因此也不会连带重复请求接口
* CSS 里的媒体查询与 TS 常量必须同步修改，否则会出现「CSS 认为不是手机、JS 认为是手机」

---

## 2. 导航

### 底部标签栏（mobile）

最多 5 项，由 `navigation.tsx` 中的 `mobile: true` 标记决定：

| 经营者 | 家庭成员 | 咨询人员 |
| --- | --- | --- |
| 今日决策 | 家庭协同 | 咨询工作台 |
| 现金事件 | 我的 | 事项记录 |
| 家庭协同 | | 我的 |
| 经营咨询 | | |
| 我的 | | |

* **情景分析不占底部导航位**，由「今日决策」页的入口进入；`/analysis` 地址与桌面一致
* 底部导航固定在视口底部，内容区预留 `var(--mobile-tabbar-height)` + 24px，
  最后一项不会被遮挡
* 触控区高度不低于 44px，标签 12px、允许两行

### 顶部栏（mobile）

* 只显示品牌标识与账户菜单，**不再重复显示页面标题**
* 页面标题 `h1` 仍留在 DOM 中（视觉隐藏），保证屏幕阅读器与自动化用例可定位
* 当前页面名由底部导航的高亮项表达

### 侧栏（tablet / desktop）

`>= 1024px` 才出现；`collapsed` 状态由用户控制，平板与手机上强制收起。

---

## 3. 数据展示

### ResponsiveDataView

```tsx
<ResponsiveDataView desktopTable={<Table … />} mobileCards={<MobileXxxList … />} />
```

桌面渲染高信息密度表格，手机渲染卡片列表。两个视图都必须是**纯展示**：
数据、分页与查询状态由调用方持有，切换视口不会触发任何接口请求。

### 已卡片化的列表

| 位置 | 桌面 | 手机 | 关键信息 |
| --- | --- | --- | --- |
| 现金事件 | 980px 表格 | `MobileEventList` | 名称、编号·类型、时间、方向、金额、状态、版本、来源、4 个操作 |
| 经营咨询 | 800px 表格 | `MobileConsultationList` | 事项编号、状态、问题类型、问题摘要、提交时间、详情入口 |
| 咨询工作台 | 980px 表格 | `MobileConsultationList`（复用） | 同上 |
| 家庭成员 | 表格 | `MobileMemberList` | 称呼、账户、关系、状态、加入时间、通过/移除 |
| 历史经营数据 | 560px 表格 | `MobileHistoryCards` | 日期、日常到账、日常采购、净变化、完整性 |

仍在手机端保留表格的（列数少或本身就是对比矩阵）：情景对比明细保留表格
并靠容器内横向滚动 + 渐隐遮罩提示。

### 我的（设置）

| | 桌面 | 手机 |
| --- | --- | --- |
| 形态 | 5 个平铺页签 | 纵向设置列表，每项含图标、分组名、说明与进入箭头 |
| 进入分组 | 切换页签 | 二级页面 + 顶部返回条（「← 返回」） |
| 分组定义 | 单一来源 `settingsSections`，两种布局共用 |

### 筛选

手机端默认只显示搜索框与「筛选 (N)」按钮（N 为生效条件数），
点按后弹出**底部抽屉**：预计时间区间 / 收支方向 / 状态 / 事项类型，
底部固定「重置」与「确认」（高度 >= 44px）。

桌面端保持平铺筛选行。抽屉与平铺共用同一份 `filterControls` 节点，
避免两套实现随时间漂移出不一致的选项。

---

## 4. 图表

| 项 | 桌面 | 手机 |
| --- | --- | --- |
| X 轴标签 | `10月3日` + 换行 + `周六` | `10/3`（单行） |
| 标签字号 | 11px | 12px，`interval: auto` + `hideOverlap` |
| 图例 | 右上角 | 由容器 footer 承担，位于图表下方 |
| 网格 | `top: 32, bottom: 8` | `top: 24, bottom: 44`（给底部图例留位） |
| 交互 | hover | **tap**（`triggerOn: 'mousemove\|click'`） |

* 图表宽度一律 100%，高度按内容给定（260–320px），CSS 不再固定覆盖高度
* **不依赖 hover**：所有 tooltip、菜单、来源入口都必须可点击触发

---

## 5. 抽屉与表单

| 项 | 桌面 | 手机 |
| --- | --- | --- |
| 宽度 | 480–860px（按业务） | `100vw` |
| 底部操作区 | 常规 | 固定，按钮高度 >= 44px，跟随 `env(safe-area-inset-bottom)` |
| 键盘 | — | 金额 `inputMode="decimal"`、数字 `inputMode="numeric"`、手机号 `tel` |

统一入口：`components/ResponsiveDrawer.tsx`（`useDrawerWidth()` 仍供既有抽屉使用）。

**iOS 自动放大**：Safari 在字体小于 16px 的输入框聚焦时会放大整页，
因此手机端表单控件统一 `font-size: 16px`。

---

## 6. 视觉

| 项 | 值 |
| --- | --- |
| 页面背景 | `#F6F7F9` |
| 卡片 | 白色，圆角 10–12px，内边距 14–16px，卡片间距 10–12px |
| 主数字 | 28–34px（首页主结论 42px） |
| 正文 / 辅助 | 14–16px / 12–13px |
| 品牌色 | 接近 `#D90000`，不大面积使用 |

不使用超大金额、复杂渐变、发光、玻璃拟态或霓虹效果。

---

## 7. 视口验收

验收宽度：**360 / 375 / 390 / 430 / 768 / 1024 / 1440**，覆盖 6 个经营者页面。

断言内容：

| 检查 | 期望 |
| --- | --- |
| 页面级横向滚动 | `documentElement.scrollWidth - clientWidth <= 1` |
| `<= 430px` | 底部标签栏可见、左侧栏隐藏、宽表格被卡片列表替代 |
| `>= 1024px` | 左侧栏可见、底部标签栏隐藏 |
| 「我的」页签 | 不出现 `...` 折叠菜单，可横向滑动 |
| 底部导航 | 项数 <= 5，不含「情景分析」，标签完整 |
| 抽屉 | 手机端宽度不超过视口 |

自动化落点：`frontend/e2e/responsive.spec.ts`（mobile 项目，Pixel 5）+
`frontend/tests/responsive.test.tsx`（断点判定与 `ResponsiveDataView` 三视口）。
