#!/usr/bin/env bash
# ourAgentTeams — Universal Setup Script
# Detects the best available tool: uv > conda > python venv
# Usage: bash setup.sh

set -e

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
CYAN="\033[0;36m"
RESET="\033[0m"

echo -e "${BOLD}${CYAN}"
echo "╔══════════════════════════════════════╗"
echo "║     ourAgentTeams — Setup Script     ║"
echo "╚══════════════════════════════════════╝"
echo -e "${RESET}"

# ── 检测工具并设置环境 ─────────────────────────────────────────────

if command -v uv &>/dev/null; then
    echo -e "${GREEN}[setup]${RESET} 检测到 ${BOLD}uv${RESET}，使用 uv 创建虚拟环境..."
    uv sync
    PYTHON="$(pwd)/.venv/bin/python"
    ACTIVATE_CMD="source .venv/bin/activate"
    # Windows 路径提示
    if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OSTYPE" == "win32" ]]; then
        ACTIVATE_CMD=".venv\\Scripts\\activate"
    fi
    METHOD="uv"

elif command -v conda &>/dev/null; then
    echo -e "${GREEN}[setup]${RESET} 检测到 ${BOLD}conda${RESET}，使用 conda 创建虚拟环境..."
    # 如果环境已存在则更新，否则创建
    if conda env list | grep -q "^ouragentteams "; then
        echo -e "${YELLOW}[setup]${RESET} 环境 ouragentteams 已存在，正在更新..."
        conda env update -f environment.yml --prune
    else
        conda env create -f environment.yml
    fi
    conda run -n ouragentteams pip install -e .
    PYTHON="conda run -n ouragentteams python"
    ACTIVATE_CMD="conda activate ouragentteams"
    METHOD="conda"

else
    echo -e "${YELLOW}[setup]${RESET} 未检测到 uv 或 conda，使用标准 ${BOLD}python venv${RESET}..."
    # 确保 Python 版本 >= 3.11
    PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [[ "$PY_MAJOR" -lt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 11 ) ]]; then
        echo -e "${YELLOW}[警告]${RESET} 当前 Python 版本为 ${PY_VERSION}，项目需要 3.11+。"
        echo "推荐先安装 uv（https://docs.astral.sh/uv/）或 conda 以自动管理 Python 版本。"
        echo "继续安装，但可能出现兼容性问题..."
    fi
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip -q
    pip install -r requirements.txt
    pip install -e .
    PYTHON="$(pwd)/.venv/bin/python"
    ACTIVATE_CMD="source .venv/bin/activate"
    METHOD="venv"
fi

# ── 下载 spacy 语言模型（presidio-analyzer 依赖） ───────────────────

echo -e "${GREEN}[setup]${RESET} 下载 spacy 语言模型 en_core_web_lg..."
if [[ "$METHOD" == "conda" ]]; then
    conda run -n ouragentteams python -m spacy download en_core_web_lg
else
    $PYTHON -m spacy download en_core_web_lg
fi

# ── 创建必要的数据目录 ─────────────────────────────────────────────

echo -e "${GREEN}[setup]${RESET} 初始化数据目录..."
mkdir -p data/tasks data/sessions data/memory data/vectorstore

# ── 完成提示 ───────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════╗"
echo "║         Setup 完成！                 ║"
echo "╚══════════════════════════════════════╝${RESET}"
echo ""
echo -e "下一步，激活环境："
echo -e "  ${CYAN}${ACTIVATE_CMD}${RESET}"
echo ""
echo -e "然后运行项目："
echo -e "  ${CYAN}ouragentteams --help${RESET}"
echo -e "  ${CYAN}ouragentteams${RESET}  # 进入交互模式"
echo -e "  ${CYAN}ouragentteams start \"你的任务\"${RESET}"
echo ""
echo -e "${YELLOW}提示：${RESET} Ollama 需要在宿主机提前运行，并拉取 Leader 模型："
echo -e "  ${CYAN}ollama pull qwen2.5:7b${RESET}   # 轻量 Leader（推荐入门）"
echo -e "  ${CYAN}ollama pull qwen2.5:72b${RESET}  # 高性能 Leader"
