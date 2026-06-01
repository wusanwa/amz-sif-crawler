---
name: amz-sif-crawler
description: >-
  当需要执行固定命令 `bash scripts/crawl.sh B0CDX5XGLK B0CDX5XGLK` 并理解其返回结果时使用。
  同时覆盖 `scripts/daily_asin_list` 和 `scripts/daily_report.sh` 的日常报表操作说明。
---

# amz-sif-crawler

## 何时使用

仅在用户要你说明、执行或解读下面这条命令时使用：

```bash
bash scripts/crawl.sh B0CDX5XGLK B0CDX5XGLK
```

如果用户问的是项目无关内容，这个 skill 不适用。

## 你要做的事

1. 把这条命令视为唯一目标，不要扩展成通用说明。
2. 明确说明两个参数都是 `B0CDX5XGLK`，所以这是对同一个 ASIN 提交两次抓取。
3. 明确说明脚本会向本地服务 `http://127.0.0.1:8000/crawl` 发送请求。
4. 明确说明请求体等价于：

```json
{
  "urls": ["B0CDX5XGLK", "B0CDX5XGLK"],
  "mode": "both"
}
```

5. 解释返回时，只围绕这条命令的标准 JSON 结构说明，不要展开到项目整体架构。

## 日报相关命令

当用户要维护日报 ASIN 列表或生成日报时，也使用这个 skill。

### `scripts/daily_asin_list`

用于管理 `config/daily_bindings.json` 里的 `bindKey -> ASIN 列表`。

常用写法：

```bash
scripts/daily_asin_list --bindkey demo-keyboard --action list
scripts/daily_asin_list --bindkey demo-keyboard --action add --asin B0CDX5XGLK --asin B0TEST0001
scripts/daily_asin_list --bindkey demo-keyboard --action remove --asin B0TEST0001
```

解释时要明确：

- `--bindkey` 是日报分组名。
- `--action` 只处理 `list`、`add`、`remove` 这三种动作。
- `add` 和 `remove` 可以重复传多个 `--asin`。
- 这是本地列表维护，不是立即触发抓取。

### `scripts/daily_report.sh`

用于按 `bindKey` 生成当日报表 CSV。

常用写法：

```bash
bash scripts/daily_report.sh --bindkey demo-keyboard
bash scripts/daily_report.sh --bindkey demo-keyboard --date 2026-06-01
bash scripts/daily_report.sh --bindkey demo-keyboard --mode both
```

解释时要明确：

- `--bindkey` 必填，对应 `config/daily_bindings.json` 中的分组。
- `--date` 可选，决定输出文件名中的日期，格式是 `YYYY-MM-DD`。
- `--mode` 可选，支持 `both`、`amazon`、`sif`。
- 脚本默认会使用本地 daemon：
  - `AMAZON_DAEMON_URL=http://127.0.0.1:8001`
  - `SIF_DAEMON_URL=http://127.0.0.1:8002`
- 成功时标准输出是生成的 CSV 绝对路径，例如：

```text
/data/devs/AI-MCP/amz-sif-crawler/runtime_data/demo-keyboard/2026-06-01-5.csv
```

## 执行 daily_report 的额外要求

如果 agent 执行了 `bash scripts/daily_report.sh ...`，在命令成功后必须继续完成下面动作：

1. 读取脚本输出里的 CSV 文件路径。
2. 确认该 CSV 已生成。
3. 把 CSV 发给用户，而不是只回复“命令执行成功”或只给路径。

如果当前会话环境不能直接发送文件，也要在回复里明确指出生成的 CSV 路径，并说明这是需要交付给用户的报表文件。

## 返回怎么理解

命令成功时，标准输出是 JSON，顶层结构固定为：

```json
{
  "status": "success",
  "count": 2,
  "results": [
    {
      "timestamp": "2026-06-01 12:00:00",
      "asin": "B0CDX5XGLK",
      "status": "SUCCESS",
      "failure_reason": "",
      "amazon_title": "...",
      "amazon_price": "...",
      "amazon_list_price": "...",
      "amazon_savings_text": "...",
      "amazon_has_price_discount": false,
      "amazon_deal_type": "...",
      "amazon_is_limited_time_deal": false,
      "amazon_coupon_text": "...",
      "amazon_applied_coupon_text": "...",
      "amazon_has_coupon": false,
      "amazon_model": "...",
      "amazon_total_variants": 0,
      "amazon_variants": [],
      "sif_1_kw": "...",
      "full_sif": []
    },
    {
      "timestamp": "2026-06-01 12:00:01",
      "asin": "B0CDX5XGLK",
      "status": "SUCCESS",
      "failure_reason": "",
      "amazon_title": "...",
      "amazon_price": "...",
      "amazon_list_price": "...",
      "amazon_savings_text": "...",
      "amazon_has_price_discount": false,
      "amazon_deal_type": "...",
      "amazon_is_limited_time_deal": false,
      "amazon_coupon_text": "...",
      "amazon_applied_coupon_text": "...",
      "amazon_has_coupon": false,
      "amazon_model": "...",
      "amazon_total_variants": 0,
      "amazon_variants": [],
      "sif_1_kw": "...",
      "full_sif": []
    }
  ]
}
```

解释时重点说这几件事：

- `status: "success"` 表示这次 HTTP 调用成功返回。
- `count: 2` 表示返回了两条结果，因为输入里有两个参数。
- `results` 里通常会有两条记录，而且两条的 `asin` 都是 `B0CDX5XGLK`。
- `results[].status` 是单条抓取状态，常见值是 `SUCCESS` 或 `PARTIAL`。
- `results[].failure_reason` 只有部分失败时才有内容，比如 `Amazon Empty Data`、`SIF Empty Data`。
- `amazon_*` 字段表示 Amazon 商品抓取结果。
- `sif_1_kw` 和 `full_sif` 表示 SIF 反查结果。

## 失败时怎么说

如果本地 `8000` 服务没启动，命令不会返回上面的 JSON，而会直接报错退出。解释时只需要指出：

- 这是连接本地抓取服务失败
- 失败点是 `http://127.0.0.1:8000/crawl`
- 这不属于返回 JSON 中的业务失败，而是命令本身执行失败

## 不要做什么

- 不要把这个 skill 改写成通用 API 文档。
- 不要解释 `--amazon-only`、`--sif-only` 或其他输入形式。
- 不要展开介绍整个仓库。
- 不要假设用户要的是别的 ASIN。
