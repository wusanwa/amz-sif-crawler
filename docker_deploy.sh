#!/bin/bash

# --- 1. 创建宿主机持久化目录 ---
mkdir -p data/node1/profiles data/node1/cache_db
mkdir -p data/node2/profiles data/node2/cache_db

# --- 2. 路径规范提示 ---
# 项目统一使用相对路径/运行时路径，不再依赖宿主机绝对目录。

# --- 3. 检查 Docker 环境 ---
if ! command -v docker &> /dev/null; then
    echo "[ERROR] 未发现 Docker 运行环境，请查阅官方文档进行安装。"
    exit 1
fi

# --- 4. 部署与启动 ---
echo "[DEPLOYING] 正在构建 Docker 镜像并启动隔离节点..."
docker-compose up --build -d

# --- 5. 状态反馈 ---
echo -e "\n=============================================="
echo "✅ 隔离环境部署完成！"
echo "----------------------------------------------"
echo "节点 1 (Isolated): http://localhost:8001"
echo "  - 查看日志: docker logs -f crawler-iso-1"
echo "----------------------------------------------"
echo "节点 2 (Isolated): http://localhost:8002"
echo "  - 查看日志: docker logs -f crawler-iso-2"
echo "=============================================="
echo "💡 注意：由于使用了持久化上下文(Profile)，"
echo "   如果需要手动配置 Amazon/SIF 验证码或登录，"
echo "   请分别在各自容器内运行交互式 setup_profiles 命令："
echo "   docker exec -it crawler-iso-1 python3 setup_profiles.py --amazon"
echo "=============================================="
