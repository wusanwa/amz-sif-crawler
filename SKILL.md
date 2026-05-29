---
name: amz-sif-crawler
description: >-
  Amazon + SIF 抓取结果入口。用于向 amz-sif-crawler 传入一个或多个 Amazon URL，
  返回聚合后的 Amazon 与 SIF 抓取结果。
---

# amz-sif-crawler

## 用途

向网关传入 Amazon URL，获取抓取结果。

## 调用方式

```bash
curl -X POST http://localhost:8888/crawl \
  -H 'Content-Type: application/json' \
  -d '{"urls":["https://www.amazon.com/dp/B0CDX5XGLK"]}'
```

## 输入

```json
{
  "urls": ["https://www.amazon.com/dp/B0CDX5XGLK"]
}
```

## 输出

返回聚合后的抓取结果，通常包含：

- Amazon 商品信息
- SIF 反查/关键词相关结果
