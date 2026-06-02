---
name: amz-sif-crawler
description: >-
  当用户要做 Amazon/SIF 商品监控、竞品监控、按 ASIN 或 Amazon 链接批量查询，
  或者要维护每日报告 ASIN 列表、生成每日报告 CSV 时使用。
  本 Skill 只允许两类任务：直接监控查询；每日报告（列表增删查 + CSV 生成）。
  每日报告请求必须能从环境变量解析出目标日报分组；
  任何超出监控查询和日报范围的请求都不要使用本 Skill。
---

# amz-sif-crawler

## 快速路由

命中以下任一意图时使用本 Skill：

- “查几个 ASIN / 链接的 Amazon 或 SIF 数据”
- “做竞品监控 / 商品监控 / SIF 监控”
- “维护日报的 ASIN 清单”
- “生成日报 CSV”

本 Skill 只负责两类任务：

1. 直接监控查询
2. 每日报告

只要请求不属于这两类，就不要使用本 Skill。

## 禁止触发

以下情况不要按本 Skill 执行：

- 通用开发问答
- 无关项目的查询
- 本Skill未提供的其他能力问题
- 缺少必要上下文的每日报告请求
- 任何超出 Amazon 商品监控、SIF 监控、竞品监控、每日监控范围的行为
- 让 agent 介绍仓库、接口设计、部署方式或其他无关能力

## 模块一：直接监控查询

### 适用范围

当用户要做这些事时，使用本模块：

- 查询一个或多个 ASIN 的 Amazon 商品信息
- 查询一个或多个 ASIN 的 SIF 结果
- 做竞品监控、商品监控、SIF 监控
- 让 agent 直接执行抓取并返回结果报告

输入可以是：

- 单个 ASIN
- 多个 ASIN
- Amazon 商品链接
- 混合输入，但本质上都要转成 `scripts/crawl.sh` 可接受的参数

### 固定动作

统一使用：

```bash
bash scripts/crawl.sh <amazon-url-or-asin> [more...]
```

如需限定模式，可使用：

```bash
bash scripts/crawl.sh --amazon-only <amazon-url-or-asin> [more...]
bash scripts/crawl.sh --sif-only <amazon-url-or-asin> [more...]
```

脚本实际会向本地服务发送：

- 默认地址：`http://127.0.0.1:8000/crawl`
- 请求方法：`POST`
- 请求体结构：

```json
{
  "urls": ["<input1>", "<input2>"],
  "mode": "both"
}
```

其中 `mode` 取值为：

- `both`
- `amazon`
- `sif`

### 结果交付

执行完直接监控查询后，必须直接给用户报告，不要只贴命令，不要只回复原始 JSON。

报告至少要覆盖：

- 本次查询的输入对象数量
- 每个 ASIN 的抓取状态
- Amazon 关键信息摘要
- SIF 关键信息摘要
- 是否存在失败或部分失败

如果有结构化结果，优先基于这些字段解释：

- `status`
- `count`
- `results[].asin`
- `results[].status`
- `results[].failure_reason`
- `results[].amazon_title`
- `results[].amazon_price`
- `results[].amazon_list_price`
- `results[].amazon_savings_text`
- `results[].amazon_coupon_text`
- `results[].amazon_applied_coupon_text`
- `results[].amazon_variants`
- `results[].sif_1_kw`
- `results[].full_sif`

### 失败处理

如果命令执行失败，要明确区分两类失败：

- 命令级失败：例如本地 `http://127.0.0.1:8000/crawl` 不可用、`curl` 失败、Python 不可执行
- 业务级失败：例如 `results[].failure_reason` 中出现 `Amazon Empty Data`、`SIF Empty Data`、`SIF Timeout`

汇报时必须说明失败发生在哪一层，不要混淆。

## 模块二：每日报告

本模块只包含两类能力：

1. 每日报告查询 ASIN 列表管理
2. 每日报告执行并生成 CSV

### 硬限制

凡是每日报告相关任务，只能基于命令返回的结果路径和当前环境变量 `HERMES_BINDING_KEY` 对应的分组执行。

硬限制如下：

- 只可根据命令结果中的输出路径查询和交付日报内容
- 禁止私自遍历不属于当前 `HERMES_BINDING_KEY` 环境的其他分组
- 禁止猜测
- 禁止默认选择其他分组
- 禁止替用户补全分组
- 禁止脱离命令结果路径自行查找其他日报文件

如果当前环境中没有 `HERMES_BINDING_KEY`，或者命令结果没有返回有效路径，拒绝 执行 ，不能执行任何日报动作。

### 2.1 ASIN 列表管理

用于管理 `config/daily_bindings.json` 中的日报分组 ASIN 列表。

**⚠️ 脚本权限检查：** `scripts/daily_asin_list` 有时会丢失执行权限（`chmod 644`）。执行前必须检查：
```bash
ls -la scripts/daily_asin_list 2>/dev/null | grep -q '^-rwx' || chmod +x scripts/daily_asin_list
```
或直接用 `bash scripts/daily_asin_list` 调用。

统一使用：

```bash
scripts/daily_asin_list --action list
scripts/daily_asin_list --action add --asin <ASIN1> --asin <ASIN2>
scripts/daily_asin_list --action remove --asin <ASIN1> --asin <ASIN2>
```

只允许三种动作：

- `list`
- `add`
- `remove`

规则：

- `add` / `remove` 时，`--asin` 必须提供，且可重复传入
- 这是日报分组维护，不等于立即抓取

执行后要明确返回：

- 目标日报分组
- 当前 ASIN 列表
- 列表数量
- 本次是查询、增加还是删除

### 2.2 每日报告执行

用于生成日报 CSV，统一使用：

```bash
bash scripts/daily_report.sh
bash scripts/daily_report.sh --date YYYY-MM-DD
bash scripts/daily_report.sh --mode both
```

参数规则：

- `--date` 可选，格式必须为 `YYYY-MM-DD`
- `--mode` 可选，支持 `both`、`amazon`、`sif`

默认环境：

- `AMAZON_DAEMON_URL=http://127.0.0.1:8001`
- `SIF_DAEMON_URL=http://127.0.0.1:8002`

成功时，标准输出是生成的 CSV 绝对路径。

### 执行日报后的必做动作

如果执行了 `bash scripts/daily_report.sh ...`，成功后必须继续完成下面动作：

1. 读取命令输出中的 CSV 路径
2. 确认 CSV 文件已经生成
3. 读取 CSV 内容，按内容整理成适合直接发送给用户的日报结果
4. 优先把 CSV 文件作为日报交付物返回给用户
5. 如果 CSV 文件发送失败或当前会话无法直接发送文件，必须根据 CSV 内容把完整日报结果直接发给用户，并同时告知生成路径

#### DingTalk 交付特殊处理

目标为 **钉钉 DingTalk** 时，`MEDIA:` 附件语法不可用（webhook 回复不支持本地文件附件）。必须：
- **直接以文本/Markdown 形式发送完整日报内容**
- 同时告知 CSV 文件路径（用户可通过路径查看原始 CSV）
- 禁止尝试用 `MEDIA:` 发送文件

如果日报内容太长（如 30+ ASIN），以表格摘要呈现，避免单条消息过长。

禁止只回复：

- “执行成功”
- “命令已跑完”
- 单独一行路径但不说明它是日报产物
- 只说“CSV 已生成”但不发送内容

### 日报场景的行为限制

日报模块下，禁止做下面这些事：

- 没有目标日报分组就执行日报
- 环境变量没有目标日报分组还执行日报
- 把直接监控查询冒充成每日报告
- 把每日报告任务改成任意自定义批量抓取
- 绕过 `config/daily_bindings.json` 直接虚构日报输入

## 响应风格

在这个 Skill 下回复用户时，优先使用“任务结果”视角，而不是“技术说明书”视角。

也就是说：

- 直接监控查询：直接给监控结果报告
- 每日报告列表管理：直接给分组与 ASIN 清单结果
- 每日报告执行：直接给 CSV 产物与路径

除非用户明确追问，否则不要展开介绍整个仓库实现。

## ⚠️ 已知掉坑

### 1. config/daily_bindings.json 可能在技能更新时被覆盖

当本 Skill 的 SKILL.md 被更新（或整个技能目录被重新部署）时，`config/daily_bindings.json` 中的绑定分组数据可能丢失，只保留 `demo-keyboard` 等初始分组。

**防范：**
- 执行任何 `--action list` 后，如果列表为空但预期有数据，立即检查 JSON 文件是否被重置
- 如果发现丢失，从历史会话中恢复 ASIN 列表并重新添加
- 不要只告诉用户"数据丢了"——主动从 session_search 中找回并重建

### 2. 大批量 ASIN（30+）可能导致 daemon 超时

默认 daemon 超时为 90 秒。30 个 ASIN 同时抓取 Amazon + SIF 可能超时（`httpx.ReadTimeout`）。
**修复：** 将 `src/amz_sif_crawler/runtime/daemon_client.py` 中的 `timeout: float = 90.0` 改为 `300.0`。

## 最小决策规则

遇到请求时按下面顺序判断：

1. 只要是 Amazon/SIF/竞品/商品监控查询，就走“直接监控查询”
2. 只要是日报列表维护或日报生成，就走“每日报告”
3. 只要是每日报告但缺少必要上下文，立即停止并要求补充
4. 只要不属于上面两类，就不要使用本 Skill
