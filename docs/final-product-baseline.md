# 最终产品化收口基线

本轮（`feat/final-product-polish`）**修改前**的真实状态，作为对照与回滚依据。

## 1. Git

| 项目 | 值 |
| --- | --- |
| 起始 commit | `5c26a7b`（V3 优化完成并已部署生产） |
| 分支 | `feat/final-product-polish` |
| 回滚 Tag | `v3-optimization-20261003`（→ `bf8a44a`）；更早的 `pre-v3-optimization-20261003`（→ `5ef53ef`） |
| remote | `https://github.com/WuChangqing1/gong-e-wendai.git`（Private） |
| 工作区 | 干净 |

## 2. 测试基线（全部通过）

| 层次 | 结果 |
| --- | --- |
| Backend `pytest` | **484 passed** |
| Frontend `typecheck` | 通过 |
| Frontend `lint` | 通过（0 warning） |
| Frontend `vitest` | **37 passed** |
| Frontend `build` | 通过 |
| Playwright E2E | **41 passed / 1 skipped** |
| 生产健康检查 | `{"status":"ok","database":"ok","ai_enabled":true,"env":"production"}` |

## 3. 生产数据现状（`wangzhanggui`）

部署的是**旧演示初始化数据**，本轮需要切换为完整业务数据：

| 项目 | 现状 |
| --- | --- |
| 经营主体 | 王记小吃店 / 小餐饮 / CNY / Asia/Shanghai |
| 留底 | 600 元（保留） |
| 资金时点 | 仅 1 条，期初 **600 元**（需切换为 3600） |
| 现金事项 | 3 笔 `DEMO-SETTLE-0001` / `DEMO-PAY-0001` / `DEMO-SETTLE-0002`（旧 seed，需退场） |
| 历史经营数据 | **0 天** |
| 结算记录 | **0 条** |
| 家庭 | 王家小院（1 个，成员：王掌柜 owner + 王太太 member 配偶） |
| 家庭协同卡 | **0 张** |
| 经营咨询（该商户） | **0 条** |
| 账号 | `wangzhanggui`(merchant) / `wangtaitai`(family_member) / `zixunxiaoli`(consultant) 均 active |
| 账号总量 | 321（其中 320 active，绝大多数是 E2E 残留） |

另发现 1 条 `business_name` 显示为 `?????` 的**乱码记录**（旧数据），
仅存在于测试残留的咨询记录中，不属于三个正式账号。

## 4. 修改前的三处一致性缺陷（本轮修复）

| 编号 | 缺陷 | 影响 |
| --- | --- | --- |
| C1 | `run_joint` 只比较 `max_withdrawable_cents` 取最小值 | 多个不可行情景上限都被截断为 0，实际等于随机取第一个情景 |
| C2 | `_persist` 保存 `scenarios[0]`，`_to_out` 在状态不一致时用「各情景最大缺口」重新拼装 | API 输出与数据库保存的不是同一份数据 |
| C3 | `confirm_reserve` 用默认参数重算 overview 再比对 `basis_hash` | 非默认参数（如 `delay_days=3`）下，用户看到的建议**永远无法确认**（稳定 409） |

三处都是**静默缺陷**：界面看起来正常，但结果在传递链上已经不一致或不可用。
原有的 484 项测试未覆盖「多情景都不可行」「非默认参数」这类路径，因此未能发现。

## 5. 本轮目标（顺序固定）

1. 结果一致性修复（C1 / C2 / C3）→ 见 `docs/v3-optimization.md`
2. 家庭分享字段逐项对应 → 同上
3. 留底确认参数一致性 → 同上
4. `scripts/populate_product_data.py` 建设完整业务数据
5. 清理用户可见开发/答辩文案
6. 完整自动化测试 + Secret Check
7. Commit / Push / 生产备份 / 增量部署 / 数据 apply / 三账号逐页验收
