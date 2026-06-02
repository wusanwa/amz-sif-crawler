# Amazon + SIF Crawler

一个只保留“静态 JS 抓取”方案的精简项目：

- Amazon：Playwright 打开商品页，在页面内执行 JS 提取商品信息
- SIF：Playwright 打开反查页面，在页面内执行 JS 提取前 3 个关键词排名
- 单服务入口：`POST /crawl`

## 一键抓包

直接执行：

```bash
bash scripts/crawl.sh https://www.amazon.com/dp/B0CDX5XGLK/
```

也可以直接传 ASIN：

```bash
bash scripts/crawl.sh B0CDX5XGLK
```

只抓 Amazon：

```bash
bash scripts/crawl.sh --amazon-only B0CDX5XGLK
```

只抓 SIF：

```bash
bash scripts/crawl.sh --sif-only B0CDX5XGLK
```

如果你想把结果同时落到本地文件：

```bash
PYTHONPATH=src .venv/bin/python crawl_once.py https://www.amazon.com/dp/B0CDX5XGLK/ --outfile runtime_data/results.jsonl
```

生成按 `bindKey` 管理的每日报表 CSV：

```bash
HERMES_BINDING_KEY=demo-keyboard bash scripts/daily_report.sh
```

成功时只输出 CSV 文件绝对路径。

指定日期：

```bash
HERMES_BINDING_KEY=demo-keyboard bash scripts/daily_report.sh --date 2026-06-01
```

输出路径固定为：

```text
runtime_data/<bindkey>/<日期>-<序号>.csv
```

例如：

```text
runtime_data/demo-keyboard/2026-06-01-1.csv
runtime_data/demo-keyboard/2026-06-01-2.csv
```

`bindKey` 对应的 ASIN 清单可以直接维护 `config/daily_bindings.json`，也可以用脚本管理：

```bash
HERMES_BINDING_KEY=demo-keyboard scripts/daily_asin_list --action list
HERMES_BINDING_KEY=demo-keyboard scripts/daily_asin_list --action add --asin B0CDX5XGLK --asin B0TEST0001
HERMES_BINDING_KEY=demo-keyboard scripts/daily_asin_list --action remove --asin B0TEST0001
```

## 目录结构

```text
.
├── config/
│   └── settings.json
├── runtime_data/
│   ├── cache_db/
│   └── profiles/
│       ├── amazon/
│       └── sif/
├── src/
│   └── amz_sif_crawler/
│       ├── api/
│       ├── fetchers/
│       ├── runtime/
│       ├── models.py
│       ├── service.py
│       └── utils.py
├── crawler_worker.py
├── mcp_server.py
├── mcp_gateway.py
├── sif_login.py
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── requirements.txt
```

## 本地运行

```bash
python -m pip install -r requirements.txt
playwright install chromium
python mcp_server.py
```

服务启动后：

- 健康检查：`GET http://localhost:8000/`
- 抓取接口：`POST http://localhost:8000/crawl`

示例：

```bash
curl -X POST http://localhost:8000/crawl \
  -H 'Content-Type: application/json' \
  -d '{"urls":["https://www.amazon.com/dp/B0CDX5XGLK"]}'
```

## 统一测试命令

项目提供了一个统一测试命令，串行执行：

- 语法编译检查
- 关键模块导入检查
- `pytest` 最小测试集

执行：

```bash
bash scripts/test.sh
```

如果你想显式指定解释器：

```bash
PYTHON_BIN=.venv/bin/python bash scripts/test.sh
```

## SIF 登录

首次使用前，在 `config/settings.json` 填写：

```json
{
  "SIF": {
    "phone": "your-phone",
    "password": "your-password"
  }
}
```

然后执行：

```bash
python sif_login.py
```

该脚本会直接复用 `runtime_data/profiles/sif` 下的持久化浏览器 profile。

## Docker

```bash
docker compose up -d --build
```

默认端口：

- `http://localhost:8000/`
- `http://localhost:8000/crawl`

## 设计原则

- 只保留 Playwright 原生抓取
- 不再包含 `crawl4ai`、LLM 提取、daemon、多 worker 网关、历史调试脚本
- 所有核心逻辑统一收敛到 `src/amz_sif_crawler/`
