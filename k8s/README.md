# Kubernetes Deployment (amz-sif-crawler)

## 1) 打包镜像

本仓库提供一键打包脚本（构建 1 次并打 3 个标签）：

```bash
./scripts/build_k8s_images.sh --tag latest
```

如果需要推送到镜像仓库（例如 ghcr）：

```bash
./scripts/build_k8s_images.sh --tag v1 --registry ghcr.io/your-org --push
```

会产出并可选推送以下镜像：
- `amz-sif-crawler-amazon-worker:<tag>`
- `amz-sif-crawler-sif-worker:<tag>`
- `amz-sif-crawler-mcp-gateway:<tag>`

## 2) 准备存储

先按你的存储类修改 `k8s/pvc-example.yaml`（accessModes / storageClassName / size）。

说明：
- `amazon-profile-pvc`、`sif-profile-pvc` 是登录态 seed profile（只读挂载）。
- worker 启动时会复制 seed profile 到 Pod 私有目录 `/tmp/runtime-profiles/...`，避免 profile 竞争。
- `amazon-cache-pvc`、`sif-cache-pvc` 是缓存目录（`/app/cache_db`）。

## 3) 一键部署到 K8s

```bash
./scripts/deploy_k8s.sh --tag latest
```

如果镜像在远端仓库：

```bash
./scripts/deploy_k8s.sh --tag v1 --registry ghcr.io/your-org
```

常用参数：
- `--namespace amz-sif-crawler` 指定命名空间
- `--no-pvc` 跳过 `k8s/pvc-example.yaml` 应用
- `--timeout 300s` 调整 rollout 超时时间

## 4) 检查状态

```bash
kubectl -n amz-sif-crawler get pods -o wide
kubectl -n amz-sif-crawler get svc
kubectl -n amz-sif-crawler logs deploy/mcp-gateway --tail=200
```

## 5) 验证接口

如果 `mcp-gateway` 是 `LoadBalancer`，拿到 EXTERNAL-IP 后：

```bash
curl -X POST http://<EXTERNAL-IP>:8888/crawl \
  -H 'Content-Type: application/json' \
  -d '{"urls":["https://www.amazon.com/dp/B0CDX5XGLK"]}'
```

## 6) 伸缩

当前默认：
- `amazon-worker`: 2 副本
- `sif-worker`: 2 副本

可随时调整：

```bash
kubectl -n amz-sif-crawler scale deploy/amazon-worker --replicas=3
kubectl -n amz-sif-crawler scale deploy/sif-worker --replicas=3
```
