---
name: amz-sif-crawler
description: >-
  当用户要做 Amazon/SIF 商品监控、竞品监控、按 ASIN 或 Amazon 链接批量查询，
  或者要维护 bindKey 绑定的每日报告 ASIN 列表、按 bindKey 生成日报 CSV 时使用。
  本 Skill 只允许两类任务：直接监控查询；每日报告（列表增删查 + CSV 生成）。
  每日报告请求必须能从 HERMES_BINDING_KEY 环境变量解析出 bindKey；
  任何超出监控查询和日报范围的请求都不要使用本 Skill。
---

# amz-sif-crawler

## 快速路由

命中以下任一意图时使用本 Skill：

- “查几个 ASIN / 链接的 Amazon 或 SIF 数据”
- “做竞品监控 / 商品监控 / SIF 监控”
- “维护某个 `bindKey` 的日报 ASIN 清单”
- “按某个 `bindKey` 生成日报 CSV”

本 Skill 只负责两类任务：

1. 直接监控查询
2. 每日报告

只要请求不属于这两类，就不要使用本 Skill。

## 禁止触发

以下情况不要按本 Skill 执行：

- 通用开发问答
- 无关项目的查询
- 无法从环境变量解析出 `bindKey` 的每日报告请求
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

凡是每日报告相关任务，必须能从环境变量解析到 `bindKey`。

`bindKey` 的来源按下面顺序解析：

1. 环境变量 `HERMES_BINDING_KEY`

无法解析 `bindKey` 时：

- 禁止执行
- 禁止猜测
- 禁止默认选择某个分组
- 禁止替用户补全

如果环境变量里没有 `bindKey`，只能要求用户补充，不能执行任何日报动作。

### 2.1 ASIN 列表管理

用于管理 `config/daily_bindings.json` 中的：

```text
bindKey -> ASIN 列表
```

统一使用：

```bash
HERMES_BINDING_KEY=<bindKey> scripts/daily_asin_list --action list
HERMES_BINDING_KEY=<bindKey> scripts/daily_asin_list --action add --asin <ASIN1> --asin <ASIN2>
HERMES_BINDING_KEY=<bindKey> scripts/daily_asin_list --action remove --asin <ASIN1> --asin <ASIN2>
```

只允许三种动作：

- `list`
- `add`
- `remove`

规则：

- `bindKey` 必须可解析，只能来自 `HERMES_BINDING_KEY`
- `add` / `remove` 时，`--asin` 必须提供，且可重复传入
- 这是日报分组维护，不等于立即抓取

执行后要明确返回：

- `bindKey`
- 当前 ASIN 列表
- 列表数量
- 本次是查询、增加还是删除

### 2.2 每日报告执行

用于按 `bindKey` 生成日报 CSV，统一使用：

```bash
HERMES_BINDING_KEY=<bindKey> bash scripts/daily_report.sh
HERMES_BINDING_KEY=<bindKey> bash scripts/daily_report.sh --date YYYY-MM-DD
HERMES_BINDING_KEY=<bindKey> bash scripts/daily_report.sh --mode both
```

参数规则：

- `bindKey` 必须可解析，只能来自 `HERMES_BINDING_KEY`
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
3. 把该 CSV 作为日报交付物返回给用户，或者至少明确告知生成路径

禁止只回复：

- “执行成功”
- “命令已跑完”
- 单独一行路径但不说明它是日报产物

### 日报场景的行为限制

日报模块下，禁止做下面这些事：

- 没有 `bindKey` 就执行日报
- 环境变量没有 `bindKey` 还执行日报
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

## 最小决策规则

遇到请求时按下面顺序判断：

1. 只要是 Amazon/SIF/竞品/商品监控查询，就走“直接监控查询”
2. 只要是日报列表维护或日报生成，就走“每日报告”
3. 只要是每日报告但无法从环境变量解析 `bindKey`，立即停止并要求补充
4. 只要不属于上面两类，就不要使用本 Skill
