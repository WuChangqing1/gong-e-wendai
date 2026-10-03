# 智能服务接入（智谱 GLM）

本文只描述**变量名与行为**，不包含任何密钥。

---

## 1. 配置项

配置来自环境变量。生产环境通过 systemd `EnvironmentFile` 读取
`~/apps/gong-e-wendai/.env.production`（`chmod 600`）。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AI_ENABLED` | `false` | 关闭时全部智能能力降级，核心功能不受影响 |
| `GLM` | 空 | 智谱 API Key。**最高敏感信息** |
| `GLM_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4/` | 接口地址 |
| `GLM_TEXT_MODEL` | `glm-4.5-air` | 文本任务模型 |
| `GLM_VISION_MODEL` | `glm-4.6v` | 视觉任务模型 |
| `GLM_TIMEOUT_SECONDS` | `30` | 文本请求超时 |
| `GLM_VISION_TIMEOUT_SECONDS` | `45` | 视觉请求超时 |
| `GLM_MAX_TOKENS` | `1024` | 单次最大输出 token |
| `GLM_MAX_RETRIES` | `2` | 重试次数（只对 429/5xx） |

兼容旧的 `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL` / `AI_TIMEOUT_SECONDS`，
但 **`GLM_*` 优先**。`.env.example` 中 `GLM=` 保持为空，绝不放真实值。

在 Pydantic 中密钥使用 `SecretStr`，因此不会出现在 `repr`、日志或 Traceback 里。

---

## 2. 模型分工

| 任务 | 模型 | 说明 |
| --- | --- | --- |
| 自然语言收付款提取 | `glm-4.5-air` | 粘贴文字 → 结构化事项 |
| 决策说明 | `glm-4.5-air` | 把确定性结论讲得更容易理解 |
| 经营咨询整理 | `glm-4.5-air` | 把口语化疑问整理成规范描述 |
| 结算 / 付款通知截图 | `glm-4.6v` | 读取截图中的金额与时间 |
| 收付款凭证图片 | `glm-4.6v` | 同上 |

不自动使用其他模型额度。调用失败时**不会**随意切换到其他供应商。

---

## 3. 请求参数

| 参数 | 取值 | 原因 |
| --- | --- | --- |
| `temperature` | 文本提取 `0.1`；说明与咨询整理 `0.2` | 智谱**不支持 0**；低随机业务任务使用最小合理非零值 |
| `thinking` | `{"type": "disabled"}` | 这些任务不需要复杂推理，避免浪费额度 |
| `max_tokens` | `GLM_MAX_TOKENS` | 限制单次消耗 |
| `stream` | `false` | 一次性返回，便于严格校验 |

---

## 4. 能力边界（硬性约束）

### 允许

* 从文本或截图中**提取**已经明确出现的信息
* 把确定性计算结果**表达**得更通俗
* 把用户口语化疑问**整理**成规范描述

### 禁止

* 重新计算任何金额、给出与输入不一致的新金额
* 猜测未来到账情况、承诺「一定会到账」
* 做信用评分、违约预测、贷款或授信建议
* 直接写入未经用户确认的 `CashEvent`

### 金额一致性防护

* **文本提取**：提取出的金额必须能在用户原文中找到，否则清空该金额并写入
  `warnings`，同时在响应的 `filtered_amounts` 中列出被移除的金额
* **截图提取**：图片没有可比对的文本，因此保留候选金额，但**必须**在
  `warnings` 中要求用户与截图核对
* 非正数金额（模型看不清时返回 0）一律清空并提示手动填写
* **说明文本**中出现的金额必须来自结构化输入；出现未知金额时会被剥离并追加
  「说明中与计算结果不一致的金额已被系统移除，请以页面上的计算结果为准。」

---

## 5. 图片隐私

* 支持的格式：PNG、JPEG、WEBP
* 单张最大 5MB，一次只允许 1 张
* 图片**只在内存中处理**，不写入 `public/`、前端静态资源或任何公开 URL
* 上传过程分块读取，超过上限立即拒绝，不会把超大文件读进内存
* 需要留存时，由用户确认后转为受权限保护的来源记录附件
* 家庭成员与咨询人员没有授权不能读取原图

---

## 6. 资源控制与错误分类

| 状态码 | 处理 |
| --- | --- |
| `429` / `500` / `502` / `503` / `504` | 指数退避重试，最多 `GLM_MAX_RETRIES` 次 |
| `400` / `401` / `403` / `404` / `422` | **不重试**（参数或鉴权问题重试不会变好） |
| 超时 | 重试后仍失败 → `AIServiceError` |
| 网络异常 | 重试后仍失败 → `AIServiceError` |
| 返回非 JSON / 结构不符 | → `AIServiceError` |
| 任何未预期的供应商异常 | 统一转换为 `AIServiceError`，绝不冒泡成 500 |

输入长度上限 `AI_TEXT_MAX_CHARS`（默认 4000 字）。

---

## 7. 用户可见的错误文案

面向用户的提示统一为：

> 智能服务暂时不可用，你仍可以手动完成当前操作

绝不向普通用户显示 HTTP 500、Provider exception、JSON parse failed、
model timeout 等技术细节。核心功能（登录、事项管理、CSV、现金流计算、
家庭协同、经营咨询）在任何 AI 故障下都正常工作。

---

## 8. 调用审计

审计记录（`audit_logs` 与结构化日志）只包含：

* `user_id`、`feature`、`model`、`status`、`latency`、token 用量、时间戳

**不记录**：API Key、完整 prompt、完整 response、Authorization 头、
用户完整金融文本。

---

## 9. 状态接口

`GET /api/v1/ai/status` 只返回：

```json
{
  "enabled": true,
  "configured": true,
  "available": true,
  "provider": "zhipu-glm",
  "text_model": "glm-4.5-air",
  "vision_model": "glm-4.6v"
}
```

**不返回**：Key、Key 前缀、Key 后缀、Key 长度、Authorization 头。
`GET /api/v1/health` 同样不包含密钥。

---

## 10. 密钥的允许位置

只允许出现在：

1. 本机进程内存
2. 用户自己的服务器 `~/apps/gong-e-wendai/.env.production`（`chmod 600`）
3. 发往智谱官方接口的 `Authorization` 头

禁止出现在：GitHub、其他代码托管、日志平台、错误追踪平台、第三方测试平台、
公开粘贴服务、任何其他模型服务、SSH 命令行参数、Git URL、shell 历史。

服务器写入方式：通过 **stdin** 传输到服务器上的安全更新脚本，脚本只输出
`GLM credential configured`，不输出密钥。

---

## 11. 工具

| 脚本 | 用途 |
| --- | --- |
| `scripts/verify_glm.py` | 验证文本与视觉模型连通性，只输出结论 |
| `scripts/check_secrets.py` | 推送前自检：密钥是否出现在受版本控制的文件中 |

两者都**不会**打印密钥、前缀、后缀或长度。

每次 push 之前执行：

```bash
python scripts/check_secrets.py
```
