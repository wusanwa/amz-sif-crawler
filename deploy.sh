#!/bin/bash

# --- 配置区 ---
VENV_NAME=".venv"
PYTHON_BIN="python3"
PORT=8000
MODE="sse" # 默认使用 SSE 模式，适合远程调用

# --- 颜色定义 ---
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Amazon & SIF Crawler MCP Service 一键部署脚本 ===${NC}"

# 1. 检查并安装 Python (如果缺失)
if ! command -v $PYTHON_BIN &> /dev/null; then
    echo -e "${YELLOW}[INFO] 未找到 $PYTHON_BIN，正尝试安装...${NC}"
    
    # 检查 sudo 或是否为 root
    SUDO=""
    if [ "$EUID" -ne 0 ]; then
        if command -v sudo &> /dev/null; then
            SUDO="sudo"
        fi
    fi

    # 检查包管理器
    if command -v apt-get &> /dev/null; then
        echo -e "${BLUE}[APT] 检测到 Debian/Ubuntu 环境，正在安装 Python 3.10+...${NC}"
        $SUDO apt-get update
        $SUDO apt-get install -y python3 python3-pip python3-venv python3-dev build-essential
    elif command -v dnf &> /dev/null; then
        echo -e "${BLUE}[DNF] 检测到 RHEL/CentOS/Fedora 环境，正在安装 Python 3.10+...${NC}"
        $SUDO dnf install -y python3 python3-pip python3-devel
    elif command -v yum &> /dev/null; then
        echo -e "${BLUE}[YUM] 检测到 RHEL/CentOS 环境，正在安装 Python 3.10+...${NC}"
        $SUDO yum install -y python3 python3-pip python3-devel
    else
        echo -e "${RED}[ERROR] 无法识别的包管理器。请手动安装 Python 3.10+、pip 和 venv 后再试。${NC}"
        exit 1
    fi
fi

# 再次检查以确保安装成功
if ! command -v $PYTHON_BIN &> /dev/null; then
    echo -e "${RED}[ERROR] Python 安装失败或仍无法找到，请手动检查环境。${NC}"
    exit 1
fi

# 检查是否支持 venv (部分发行版需要单独安装 python3-venv)
$PYTHON_BIN -m venv --help &> /dev/null
if [ $? -ne 0 ]; then
    echo -e "${YELLOW}[INFO] 检测到 Python 缺少 venv 模块，正在尝试安装...${NC}"
    if command -v apt-get &> /dev/null; then
        sudo apt-get install -y python3-venv
    else
        echo -e "${RED}[ERROR] 缺少 venv 模块。请手动运行相应的包管理命令 (如 apt-get install python3-venv) 后再试。${NC}"
        exit 1
    fi
fi

# 2. 创建并激活虚拟环境
if [ ! -d "$VENV_NAME" ]; then
    echo -e "${YELLOW}[INFO] 正在创建虚拟环境 $VENV_NAME...${NC}"
    $PYTHON_BIN -m venv $VENV_NAME
fi

source $VENV_NAME/bin/activate
echo -e "${GREEN}[SUCCESS] 虚拟环境已激活。${NC}"

# 3. 安装依赖
echo -e "${YELLOW}[INFO] 正在安装 Python 依赖...${NC}"
$PYTHON_BIN -m pip install --upgrade pip
$PYTHON_BIN -m pip install -r requirements.txt

# 4. 安装 Playwright 浏览器
echo -e "${YELLOW}[INFO] 正在安装 Playwright 浏览器核心与系统依赖...${NC}"
playwright install chromium
if [ $? -eq 0 ]; then
    # 尝试安装系统依赖 (需要 sudo/root 权限)
    echo -e "${YELLOW}[INFO] 正在尝试安装必要的系统库 (Playwright install-deps)...${NC}"
    if command -v sudo &> /dev/null; then
        sudo ./$VENV_NAME/bin/playwright install-deps
    else
        ./$VENV_NAME/bin/playwright install-deps
    fi
fi
echo -e "${GREEN}[SUCCESS] 浏览器安装完成。${NC}"

# 5. 检查配置文件
if [ ! -f "config/settings.json" ]; then
    echo -e "${YELLOW}[WARNING] 未发现 config/settings.json，请手动根据 README.md 创建并配置 API KEY。${NC}"
    mkdir -p config
    echo '{"LLM": {"provider": "openai/gpt-4o-mini", "api_token": "YOUR_API_KEY", "base_url": "https://api.openai.com/v1", "temperature": 0}, "CACHE_EXPIRY_SEC": 80000}' > config/settings.json.example
    echo -e "${BLUE}[HINT] 已生成 config/settings.json.example 模板。${NC}"
fi

# 6. 检查浏览器 Profile
echo -e "${YELLOW}[INFO] 正在检查浏览器 Profiles...${NC}"
if [ ! -d "profiles/amazon" ] || [ ! -d "profiles/sif" ]; then
    echo -e "${YELLOW}[IMPORTANT] 检测到部分浏览器配置(Profile)缺失。${NC}"
    echo -e "${YELLOW}由于涉及验证码或人工登录，请在部署完成后手动执行以下命令进行初始化：${NC}"
    echo -e "${BLUE}  source $VENV_NAME/bin/activate${NC}"
    echo -e "${BLUE}  python setup_profiles.py --amazon  # 修改地区或处理验证码${NC}"
    echo -e "${BLUE}  python setup_profiles.py --sif     # 完成 SIF 登录${NC}"
fi

# 7. 启动服务提示
echo -e "${GREEN}=== 部署完成 ===${NC}"
echo -e "${BLUE}您可以选择以下方式启动服务：${NC}"
echo -e "1. 前台启动 (默认端口 $PORT):"
echo -e "   ${YELLOW}source $VENV_NAME/bin/activate && python mcp_server.py --mode $MODE --port $PORT${NC}"
echo -e "2. 使用 nohup 后台启动:"
echo -e "   ${YELLOW}nohup $VENV_NAME/bin/python mcp_server.py --mode $MODE --port $PORT > mcp_server.log 2>&1 &${NC}"
echo -e "   日志保存在 mcp_server.log"

# 如果用户直接运行，我们询问是否现在启动
read -p "是否立即在前台启动服务(SSE 模式)? (y/n): " choice
if [[ "$choice" == "y" || "$choice" == "Y" ]]; then
    echo -e "${GREEN}[INFO] 正在启动服务...${NC}"
    python mcp_server.py --mode $MODE --port $PORT
fi
