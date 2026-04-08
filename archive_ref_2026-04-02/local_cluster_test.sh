#!/bin/bash

# 获取虚拟环境
VENV_NAME=".venv"
if [ -d "$VENV_NAME" ]; then
    source $VENV_NAME/bin/activate
fi

echo "--- 正在清理旧的爬虫进程 ---"
pkill -9 -f mcp_server.py
pkill -9 -f mcp_gateway.py
pkill -9 -f chrome

# 1. 启动 Amazon 专业节点 (端口 8001)
echo "🚀 启动 Amazon 节点 (端口 8001)..."
NODE_TYPE=amazon python3 mcp_server.py --mode sse --port 8001 > amz_node.log 2>&1 &
AMZ_PID=$!

# 2. 启动 SIF 专业节点 (端口 8002)
echo "🚀 启动 SIF 节点 (端口 8002)..."
NODE_TYPE=sif python3 mcp_server.py --mode sse --port 8002 > sif_node.log 2>&1 &
SIF_PID=$!

# 等待节点完全就绪
sleep 4

# 3. 启动分发网关 (端口 8888)，自动开启串行测试模式以防浏览器冲突
echo "🚀 启动网关调度器 (串行模式)..."
GATEWAY_MODE=serial python3 mcp_gateway.py > gateway.log 2>&1 &
GATE_PID=$!

echo "=============================================="
echo "✅ 本地 [串行安全模式] 集群已启动！"
echo "👉 网关地址: http://localhost:8888"
echo "----------------------------------------------"
echo "💡 注意：本地由于非 Docker 隔离，Amazon 和 SIF 将会依次运行"
echo "   以防止浏览器进程冲突。部署到 Docker 后将自动加速为并行。"
echo "=============================================="

trap "kill $AMZ_PID $SIF_PID $GATE_PID; echo '集群已停止'; exit" SIGINT
wait
