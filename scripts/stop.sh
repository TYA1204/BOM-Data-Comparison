#!/bin/bash
# ============================================================================
# BOM Comparison — 增强版停止脚本
# 功能: 停止服务 + 自动清除 __pycache__ 缓存目录
# 用法: bash stop.sh [--no-clean]
# ============================================================================
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
CODE_DIR="$PROJECT_DIR/code"
PID_FILE="$PROJECT_DIR/app.pid"
LOGS_DIR="$PROJECT_DIR/logs"

# ─────────────────────────────────────────────────────
# 参数解析
# ─────────────────────────────────────────────────────
NO_CLEAN="false"
for arg in "$@"; do
    case "$arg" in
        --no-clean) NO_CLEAN="true" ;;
    esac
done

# ─────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ─────────────────────────────────────────────────────
# 第1步: 清除 __pycache__ 缓存
# ─────────────────────────────────────────────────────
clean_pycache() {
    if [ "$NO_CLEAN" = "true" ]; then
        log_warn "已跳过 __pycache__ 清理 (--no-clean)"
        return 0
    fi

    log_info "正在清除 Python 字节码缓存..."

    local cleaned_count=0
    local cleaned_dirs=0

    # 扫描并删除 app/ 目录下的 __pycache__
    if [ -d "$CODE_DIR/app" ]; then
        while IFS= read -r -d '' dir; do
            rm -rf "$dir"
            cleaned_dirs=$((cleaned_dirs + 1))
        done < <(find "$CODE_DIR/app" -type d -name '__pycache__' -print0 2>/dev/null || true)

        # 扫描并删除独立的 .pyc 文件（不在 __pycache__ 中的）
        while IFS= read -r -d '' file; do
            rm -f "$file"
            cleaned_count=$((cleaned_count + 1))
        done < <(find "$CODE_DIR/app" -type f -name '*.pyc' -not -path '*/__pycache__/*' -print0 2>/dev/null || true)
    fi

    # 也扫描项目根目录
    while IFS= read -r -d '' dir; do
        rm -rf "$dir"
        cleaned_dirs=$((cleaned_dirs + 1))
    done < <(find "$PROJECT_DIR" -maxdepth 2 -type d -name '__pycache__' -not -path "$CODE_DIR/app/*" -print0 2>/dev/null || true)

    if [ $cleaned_dirs -gt 0 ] || [ $cleaned_count -gt 0 ]; then
        log_info "已清除 ${cleaned_dirs} 个 __pycache__ 目录, ${cleaned_count} 个 .pyc 文件"
    else
        log_info "无需清理，缓存目录不存在"
    fi
}

# ─────────────────────────────────────────────────────
# 第2步: 停止服务进程
# ─────────────────────────────────────────────────────
stop_service() {
    if [ ! -f "$PID_FILE" ]; then
        log_warn "未找到 PID 文件, 尝试按端口查找进程..."
        local pids
        pids=$(pgrep -f "gunicorn.*40045" 2>/dev/null || true)
        if [ -n "$pids" ]; then
            log_warn "发现残留 gunicorn 进程: $pids"
            kill $pids 2>/dev/null || true
            sleep 2
            # 如果还没死，强制杀
            pids=$(pgrep -f "gunicorn.*40045" 2>/dev/null || true)
            if [ -n "$pids" ]; then
                log_warn "强制终止残留进程..."
                kill -9 $pids 2>/dev/null || true
            fi
            log_info "已清理残留进程"
        else
            log_info "未找到运行中的进程"
        fi
        return 0
    fi

    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        log_info "正在停止 BOM Comparison (PID=$PID)..."
        kill "$PID"

        # 等待优雅退出（最多 10 秒）
        local waited=0
        while kill -0 "$PID" 2>/dev/null && [ $waited -lt 10 ]; do
            sleep 1
            waited=$((waited + 1))
        done

        if kill -0 "$PID" 2>/dev/null; then
            log_warn "优雅停止超时，强制终止..."
            kill -9 "$PID" 2>/dev/null || true
            sleep 1
        fi

        # 确保所有子进程也被清理（worker 进程可能残留）
        local child_pids
        child_pids=$(pgrep -f "gunicorn.*40045" 2>/dev/null || true)
        if [ -n "$child_pids" ]; then
            log_warn "清理残留 worker 进程..."
            kill -9 $child_pids 2>/dev/null || true
        fi

        log_info "已停止"
    else
        log_info "进程 $PID 已不存在"
    fi

    rm -f "$PID_FILE"
}

# ─────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────
echo "[$(date '+%Y-%m-%d %H:%M:%S')] === 停止 BOM Comparison ==="

# 先清除缓存（在进程停止前清除，因为新进程启动时会用源代码重新生成）
clean_pycache

# 再停止服务
stop_service

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === 停止完成 ==="
