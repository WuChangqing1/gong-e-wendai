# CSV 格式说明

## 1. 支持的两类文件

### A. 交易流水（file_type = `transaction`）

```csv
cash_key,title,direction,amount,event_time,state,source_label,note
TX-2025-0001,门店销售收款,inflow,1234.50,2025-10-01 18:30,scheduled,收银系统,当日营业款
TX-2025-0002,供应商货款,outflow,860.00,2025-10-02 10:00,scheduled,采购单 PO-8891,
```

时间列使用 `event_time`。

### B. 未来付款计划（file_type = `payment_plan`）

```csv
cash_key,title,direction,amount,scheduled_at,state,source_label,note
PLAN-0001,门店租金,outflow,4500.00,2025-10-05 09:00,scheduled,租赁合同,季度付款
PLAN-0002,商户结算款,inflow,2358.60,2025-10-03 15:00,scheduled,结算通知 8821,
```

时间列使用 `scheduled_at`。

> 导入时两者都会写入 `cash_events.scheduled_at`；`event_time` 与 `scheduled_at`
> 哪个有值就用哪个（优先 `scheduled_at`）。

## 2. 编码与分隔符

自动识别，无需手工选择：

| 项 | 支持范围 |
| --- | --- |
| 编码 | UTF-8、UTF-8 BOM、GB18030、GBK、Big5、Latin-1 |
| 分隔符 | `,` / Tab / `;` / `|` |
| 扩展名 | `.csv` / `.txt` / `.tsv` |
| 文件大小 | 默认不超过 5 MB（`MAX_UPLOAD_MB`） |

Excel 另存为「CSV UTF-8」或「CSV（逗号分隔）」都可以直接导入。

## 3. 字段说明

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `cash_key` | 否 | 事项编号。留空则按 `CSV-{批次}-{行号}` 生成 |
| `title` | **是** | 事项名称，最长 128 字 |
| `direction` | **是** | `inflow` / `outflow`，或中文「收入 / 支出 / 收 / 付 / 进账 / 出账 / 借 / 贷」 |
| `amount` | **是** | 金额，单位元。支持 `1234.50`、`1,234.50`、`￥88.00`、全角数字 |
| `event_time` / `scheduled_at` | **是** | 时间，见下方格式 |
| `state` | 否 | 默认 `scheduled`；也接受「计划中 / 已计入期初 / 已取消」 |
| `source_label` | 否 | 来源说明，最长 128 字 |
| `note` | 否 | 备注，最长 2000 字 |

### 时间格式

```
2025-10-03 18:30
2025-10-03 18:30:00
2025-10-03T18:30:00+08:00
2025/10/03 18:30
2025-10-03
20251003183000
20251003
```

无时区的时间按 **Asia/Shanghai** 解释。

### 金额规则

* 只接受数字，不接受 `1e3`、`12.3.4`、`12元` 之类
* 最多两位小数，第三位四舍五入（`0.005` → `0.01`）
* 不允许负数；正负由 `direction` 表达
* 系统内部统一换算为整数分

### 方向与状态别名

| 输入 | 归一化结果 |
| --- | --- |
| `inflow` / `in` / `收入` / `收` / `进账` / `入账` / `贷` | `inflow` |
| `outflow` / `out` / `支出` / `付` / `出账` / `借` | `outflow` |
| `scheduled` / `计划中` / `计划` / `待处理` | `scheduled` |
| `included_in_opening` / `已计入期初` / `已入期初` | `included_in_opening` |
| `cancelled` / `已取消` / `取消` | `cancelled` |

空值与 `-` / `--` / `N/A` / `无` / `未知` 视为未填写。

## 4. 中文表头自动映射

无需改造文件，系统会按常见表头自动映射，并允许在界面上手工调整：

| 目标字段 | 可识别的表头 |
| --- | --- |
| `cash_key` | 编号、流水号、交易号、订单号、id |
| `title` | 名称、摘要、交易名称、描述、memo |
| `direction` | 收支方向、方向、借贷、收付、类型 |
| `amount` | 金额、交易金额、发生额、金额(元) |
| `event_time` | 交易时间、发生时间、时间、time |
| `scheduled_at` | 预计时间、计划时间、到期日、付款日期、应付款日 |
| `state` | 状态、status |
| `source_label` | 来源说明、来源、渠道、source |
| `note` | 备注、说明、remark、comment |

## 5. 导入流程（绝不直接入库）

```
上传
 ↓ 解析（编码 / 分隔符 / 表头）
 ↓ 字段映射（自动 + 手工调整）
 ↓ 数据校验
 ↓ 问题提示
 ↓ 预览（逐行标注可导入 / 存在问题）
 ↓ 用户确认
 ↓ 写入 CashEvent（建立来源记录）
```

## 6. 校验规则

| code | 严重级别 | 触发条件 |
| --- | --- | --- |
| `MISSING_COLUMN` | error | 缺少必要列（名称 / 方向 / 金额 / 时间） |
| `MISSING_TITLE` | error | 事项名称为空 |
| `MISSING_AMOUNT` | error | 金额为空 |
| `INVALID_AMOUNT` | error | 金额格式不正确 |
| `NEGATIVE_AMOUNT` | error | 金额为负数 |
| `MISSING_TIME` | error | 时间为空 |
| `INVALID_TIME` | error | 无法识别的时间格式 |
| `INVALID_STATE` | error | 无法识别的状态 |
| `INVALID_DIRECTION` | error | 无法识别的收支方向 |
| `DUPLICATE_IN_FILE` | error | 同一文件内事项编号重复 |
| `DUPLICATE_CASH_KEY` | warning | 编号已存在于系统中，该行将被跳过（不覆盖已有数据） |

**只要存在 error，`can_commit` 为 `false`，提交会被拒绝（`IMPORT_HAS_ERRORS`）。**
warning 不影响提交，但重复行会被跳过并计入 `skipped`。

## 7. 追溯

每个成功导入的行都会生成一条独立的 `SourceRecord`：

* 来源类型 `csv_import`
* 原始文件名、磁盘随机文件名
* 原始行号
* 原始行内容（`列名=值 | 列名=值`）
* 内容指纹（SHA-256）
* 导入批次号

可以在「现金事件 → 来源」中查看完整原始行。

## 8. 安全

* 扩展名白名单，拒绝其他类型
* 文件大小上限
* 磁盘上使用随机文件名写入，**不使用客户端提供的文件名作为路径**，防止 `../` 路径穿越
* 上传的原始文件保存在 `uploads/`，该目录不进入 Git
