# 使用 Playwright 官方镜像，自带浏览器依赖
FROM mcr.microsoft.com/playwright:v1.43.0-jammy

# 设置工作目录
WORKDIR /app

# 安装 Python 和基础工具
RUN apt-get update && apt-get install -y \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# 升级 pip 到最新版本，避免旧版本 bug
RUN python3 -m pip install --upgrade pip setuptools wheel

# 复制依赖文件并安装
COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir -r requirements.txt

# 确保 Playwright 已正确安装且浏览器核心就绪
RUN playwright install chromium

# 复制项目代码
COPY . .

# 创建必要的运行目录
RUN mkdir -p profiles/amazon profiles/sif cache_db config

# 暴露 SSE 端口
EXPOSE 8000

# 启动命令
CMD ["python3", "mcp_server.py", "--mode", "sse", "--port", "8000"]
