# Amazon + SIF Crawler (Docker 方案)

本项目已切换为 **Docker Compose 主方案**，默认运行形态为三节点：

- `amazon-worker`：抓 Amazon 商品信息
- `sif-worker`：抓 SIF 关键词排名
- `mcp-gateway`：并行调度并聚合结果（统一对外入口）

## 架构与端口

- 网关 HTTP 测试入口：`POST http://localhost:8888/crawl`
- 网关 MCP SSE：`http://localhost:8888/sse`
- Amazon Worker：`http://localhost:8001`（容器内服务端口 8000）
- SIF Worker：`http://localhost:8002`（容器内服务端口 8000）

`docker-compose.yml` 中默认 `GATEWAY_MODE=parallel`，网关会并发请求两个 worker。

## 1. 前置准备

### 1.1 配置文件
编辑 [config/settings.json](config/settings.json)：

- `LLM.provider`
- `LLM.api_token`
- `LLM.base_url`
- 其他缓存与输出配置

建议不要提交真实密钥到仓库。

### 1.2 初始化浏览器 Profile（宿主机执行）

项目已改为“压缩包入库 + 运行时解压”模式：

- 本地存放：`./profile_bundles/amazon.tar.gz`、`./profile_bundles/sif.tar.gz`（默认不提交）
- 运行时目录：`./runtime_data/profiles/amazon`、`./runtime_data/profiles/sif`

首次使用请在宿主机执行：

```bash
# 进入项目目录
cd <project-root>

# Amazon（处理地区/验证码）
bash scripts/setup_amazon_manual.sh

# SIF（手动登录）
bash scripts/setup_sif_manual.sh

# 生成/更新本地压缩包
bash scripts/profile_bundle.sh pack all
```

部署前自动解压（`deploy.sh` 和 `docker_deploy.sh` 已内置）：

```bash
bash scripts/profile_bundle.sh unpack all
```

如果 profile 目录存在残留锁文件：

```bash
bash scripts/setup_sif_manual.sh --force-unlock
```

## 2. 启动服务（Docker Compose）

```bash
# 若仓库内已有 profile 压缩包，可先恢复到 runtime_data
bash scripts/profile_bundle.sh unpack all

docker compose up -d --build
```

查看状态：

```bash
docker compose ps
```

查看日志：

```bash
docker compose logs -f mcp-gateway
docker compose logs -f amazon-worker
docker compose logs -f sif-worker
```

停止服务：

```bash
docker compose stop
```

## 2.1 本地直接运行 `sif_login.py`

如果你是在宿主机直接调试 `sif_login.py`，除了安装 `requirements.txt` 里的 Python 依赖，还需要安装 Playwright 的 Chromium 浏览器：

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python sif_login.py --install-browser-only
.venv/bin/python sif_login.py
```

如果浏览器尚未安装，脚本现在也会在启动时自动尝试执行：

```bash
.venv/bin/python -m playwright install chromium
```

## 2.2 Git 发布方式（子树同步）

当前目录现在是独立 Git 仓库，但和上层 monorepo `AI-MCP` 的关系仍然是 subtree 工作流。也就是：

- 拉取上游时，从 `AI-MCP/master` 的 `amz-sif-crawler/` 子树拆分后同步到本地
- 发布改动时，把当前仓库内容同步回远端 `master` 的 `amz-sif-crawler/` 路径

拉取上游更新：

```bash
git status --short
env GIT_TERMINAL_PROMPT=0 scripts/pull_from_gkb_subtree.sh
```

发布本地改动：

```bash
git status --short
git add -A
git commit -m "feat: your change"

# 用 subtree 脚本发布当前仓库
env GIT_TERMINAL_PROMPT=0 scripts/sync_to_gkb_subtree.sh
```

这两个脚本会自动兼容两种形态：

- 在 monorepo 子目录里运行时，按 `amz-sif-crawler/` 做 subtree split / merge
- 在当前这种独立仓库里运行时，自动把仓库根目录内容同步到远端 `amz-sif-crawler/`

## 3. 快速验证

### 3.1 通过网关发起聚合抓取

```bash
curl -X POST http://localhost:8888/crawl \
  -H 'Content-Type: application/json' \
  -d '{"urls":["https://www.amazon.com/dp/B0CDX5XGLK"]}'
```

预期返回：

- `status: success`
- `results[*].amazon_*` 字段（Amazon 结果）
- `results[*].full_sif` 字段（SIF 结果）

## 4. MCP 工具说明

### 网关工具（推荐）

- 工具名：`track_competitor_intelligence`
- 入参：`urls: list[str]`
- 说明：用于亚马逊店铺竞品追踪与抓包分析；并行调度 amazon/sif，并按 ASIN 聚合结果

### Worker 工具（单节点）

- 工具名：`crawl_amazon`
- 入口文件：[mcp_server.py](mcp_server.py)
- 说明：在单 worker 容器内按 `NODE_TYPE` 执行（amazon 或 sif）

## 4.1 通用 Browser Daemon（Amazon + SIF 抓包）

新增了一个独立的通用 daemon，用来参考 `lingxing-ads-automation/src/daemon` 的思路，把 Amazon 和 SIF 的浏览器常驻、状态管理与 HTTP 查询统一起来。

入口：

- [daemon_server.py](daemon_server.py)
- [daemon/browser_daemon.py](daemon/browser_daemon.py)
- [daemon/http_api.py](daemon/http_api.py)

用途：

- 维持 Amazon / SIF 持久化浏览器 profile
- 统一 warmup 浏览器实例
- 统一打开目标页并直接通过 HTTP 返回结果

启动：

```bash
python daemon_server.py
```

默认端口：

- `http://localhost:8890`

示例：

```bash
curl http://localhost:8890/health

curl -X POST http://localhost:8890/daemon/warmup \
  -H 'Content-Type: application/json' \
  -d '{"provider":"sif"}'

curl -X POST http://localhost:8890/daemon/capture \
  -H 'Content-Type: application/json' \
  -d '{"provider":"amazon","url":"https://www.amazon.com/dp/B0CDX5XGLK","idle_ms":5000}'
```

当前这个 daemon 是第一版基础设施，先独立于现有 `crawler_worker.py` 工作。这样可以先稳定做“通用抓包”，后续再逐步把现有 Amazon/SIF 抓取逻辑改成走 daemon，而不是每次请求都重新起浏览器。

### SIF 任务接入 daemon

现在 `sif_query.py` 已经支持优先复用 daemon 里的常驻 `sif` 浏览器。

启用方式：

```bash
export SIF_DAEMON_URL=http://127.0.0.1:8891
```

可选参数：

```bash
export SIF_DAEMON_IDLE_MS=2500
```

启用后，现有 `crawler_worker.py` 在执行 SIF 任务时会：

- 先调用 daemon 的 `/daemon/warmup`
- 再调用 `/daemon/sif/query`
- 如果 daemon 没拿到有效结果，再自动回退到原来的本地浏览器抓取逻辑

这意味着你可以渐进迁移，不需要一次性改掉现有 worker 全部逻辑。

### 常驻浏览器与选项卡复用

如果设置了：

```bash
export SIF_DAEMON_URL=http://127.0.0.1:8891
```

当前行为就是：

- `sif` 浏览器由 daemon 常驻维护
- `crawler_worker.py` 不会在任务结束后销毁这个浏览器
- daemon 会优先复用同一个 `page/tab`
- 下一个 SIF 任务会继续使用这个已存在的页面上下文

也就是说，这已经是“浏览器不销毁、选项卡复用”的实现方式。

推荐调用顺序：

```bash
# 终端 1：启动 daemon
env DAEMON_PORT=8891 .venv/bin/python daemon_server.py

# 终端 2：预热常驻 sif 浏览器
curl -X POST http://127.0.0.1:8891/daemon/warmup \
  -H 'Content-Type: application/json' \
  -d '{"provider":"sif"}'

# 终端 2：后续所有 sif 任务都复用 daemon 浏览器
export SIF_DAEMON_URL=http://127.0.0.1:8891
```

### SIF 多 Tab 并发复用

当前 `sif` daemon 已支持 tab 池模式：

- 单个 `sif` 浏览器常驻
- 默认最多复用 `5` 个 tab
- 最多支持 `5` 个 SIF 任务同时执行
- 超出的请求会等待空闲 tab，而不是再起新浏览器

你可以通过状态接口观察 tab 池：

```bash
curl http://127.0.0.1:8891/daemon/status
```

会返回：

- `max_tabs`
- `tabs[*].slot_id`
- `tabs[*].busy`
- `tabs[*].current_url`
- `tabs[*].last_used_at`

并发调用时，继续复用同一个接口即可：

```bash
curl -X POST http://127.0.0.1:8891/daemon/sif/query \
  -H 'Content-Type: application/json' \
  -d '{"asin":"B0CDX5XGLK","idle_ms":2500}'
```

如果你同时发起最多 5 个不同 ASIN 请求，daemon 会自动分配到 5 个复用 tab 上执行。

## 5. 目录说明（当前）

- [docker-compose.yml](docker-compose.yml)：三服务编排
- [mcp_gateway.py](mcp_gateway.py)：网关调度与聚合
- [mcp_server.py](mcp_server.py)：worker 服务入口
- [crawler_worker.py](crawler_worker.py)：核心抓取逻辑
- [setup_profiles.py](setup_profiles.py)：profile 初始化与修复
- [config/settings.json](config/settings.json)：运行配置
- `profile_bundles/*.tar.gz`：本地 profile 压缩包（默认忽略提交）
- `runtime_data/profiles/*`：运行时解压 profile（默认忽略提交）
- `runtime_data/cache_db/*`：运行时缓存（默认忽略提交）
- [archive_ref_2026-04-02](archive_ref_2026-04-02)：归档的历史文件

## 6. 常见问题

### Q1: `ProcessSingleton` / `profile is already in use`
先关闭占用该 profile 的 Chromium/Playwright 进程，再重试；必要时加 `--force-unlock`。

### Q2: `Permission denied`（profile 无法访问）
修复目录权限后重试：

```bash
sudo chown -R $USER:$USER ./runtime_data/profiles/sif
chmod -R u+rwX ./runtime_data/profiles/sif
```

Amazon profile 同理处理对应目录。

### Q3: Compose 提示 `version is obsolete`
这是 Compose v2 的提示，不影响运行；可后续从 `docker-compose.yml` 移除 `version` 字段。
