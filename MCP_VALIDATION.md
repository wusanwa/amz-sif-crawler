# MCP 验证工具

这个项目现在包含两套 MCP 验证方式：

- `scripts/validate_mcp.py`
  适合命令行快速验服务是否正常启动、`/sse` 是否可连、以及可选的 `/crawl` 探测。
- `@modelcontextprotocol/inspector`
  官方 Inspector，适合在浏览器里检查 MCP 连接、工具列表和调用过程。

## 1. 安装

### Python 侧

项目虚拟环境里已经包含脚本所需依赖：

```bash
.venv/bin/python -m pip install -r requirements.txt
```

### Inspector

项目根目录已配置本地依赖，安装命令：

```bash
npm install
```

## 2. 命令行验证

只验证网关健康检查和 SSE：

```bash
./.venv/bin/python scripts/validate_mcp.py
```

验证并额外探测一次 `/crawl`：

```bash
./.venv/bin/python scripts/validate_mcp.py --probe-crawl
```

如果抓取链路较慢，可以调大超时：

```bash
./.venv/bin/python scripts/validate_mcp.py --probe-crawl --crawl-timeout 180
```

指定其他地址：

```bash
./.venv/bin/python scripts/validate_mcp.py --base-url http://localhost:8888
```

## 3. 打开 Inspector

先启动本项目服务：

```bash
docker compose up -d
```

再启动 Inspector：

```bash
npm run mcp:inspector
```

然后在 Inspector 里连接这个 SSE 地址：

```text
http://localhost:8888/sse
```

## 4. 结果判定

命令行脚本会输出：

- `[PASS] health`
- `[PASS] sse`
- 可选的 `[PASS] crawl`

最后一行是总结果：

```text
Validation result: PASS
```

## 5. 注意事项

- `amazon-worker` 在当前 `docker-compose.yml` 里没有暴露宿主机端口，所以宿主机上最适合直接验证的是网关 `http://localhost:8888`。
- 如果要验证 worker 本身，优先通过网关走聚合链路，或者后续再单独给 worker 暴露端口。
- `--probe-crawl` 会真实触发一次抓取请求，耗时会比普通健康检查更长。
- `--probe-crawl` 默认使用 `120` 秒超时，必要时可通过 `--crawl-timeout` 调整。
