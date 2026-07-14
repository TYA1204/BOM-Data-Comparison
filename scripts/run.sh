#!/bin/bash
# ============================================================================
# BOM Comparison — 增强版启动脚本
# 功能: 启动前缓存检查 + PYTHONDONTWRITEBYTECODE=1 + 端口检查
# 用法: bash run.sh [--force] [--skip-cache-check]
#   --force              强制启动，即使发现缓存残留（自动清除后继续）
#   --skip-cache-check   跳过缓存检查（不推荐）
# 环境变量:
#   AUTO_RECOVER=true    看门狗自动恢复模式（等同 --force，不阻塞启动）
# ============================================================================
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
CODE_DIR="$PROJECT_DIR/code"
LOGS_DIR="$PROJECT_DIR/logs"

# ─────────────────────────────────────────────────────
# 参数解析
# ─────────────────────────────────────────────────────
FORCE_MODE="false"
SKIP_CACHE_CHECK="false"
for arg in "$@"; do
    case "$arg" in
        --force)              FORCE_MODE="true" ;;
        --skip-cache-check)   SKIP_CACHE_CHECK="true" ;;
    esac
done

# AUTO_RECOVER 环境变量：用于看门狗自动恢复场景
# 当该变量为 true 时，遇到 pycache 残留自动清除（等同 --force），避免堵死自动恢复链路
if [ "${AUTO_RECOVER:-}" = "true" ]; then
    FORCE_MODE="true"
fi

# ─────────────────────────────────────────────────────
# 颜色定义
# ─────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ─────────────────────────────────────────────────────
# 第0步: 禁止 Python 生成 .pyc 文件
# ─────────────────────────────────────────────────────
export PYTHONDONTWRITEBYTECODE=1
log_info "PYTHONDONTWRITEBYTECODE=1 (禁止生成 .pyc)"

# ─────────────────────────────────────────────────────
# 第1步: 缓存残留检查
# ─────────────────────────────────────────────────────
check_pycache() {
    if [ "$SKIP_CACHE_CHECK" = "true" ]; then
        log_warn "已跳过缓存检查 (--skip-cache-check)"
        return 0
    fi

    local found_dirs=0
    local found_files=0
    local dir_list=""
    local file_list=""

    # 扫描 app/ 目录下的 __pycache__
    if [ -d "$CODE_DIR/app" ]; then
        dir_list=$(find "$CODE_DIR/app" -type d -name '__pycache__' 2>/dev/null || true)
        if [ -n "$dir_list" ]; then
            found_dirs=$(echo "$dir_list" | wc -l)
        fi

        # 扫描独立的 .pyc 文件
        file_list=$(find "$CODE_DIR/app" -type f -name '*.pyc' -not -path '*/__pycache__/*' 2>/dev/null || true)
        if [ -n "$file_list" ]; then
            found_files=$(echo "$file_list" | wc -l)
        fi
    fi

    if [ $found_dirs -gt 0 ] || [ $found_files -gt 0 ]; then
        echo ""
        echo -e "${RED}${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
        echo -e "${RED}${BOLD}║  ⚠️  检测到 Python 字节码缓存残留！                     ║${NC}"
        echo -e "${RED}${BOLD}╠══════════════════════════════════════════════════════════╣${NC}"
        echo -e "${RED}║  这可能导致旧模块代码被执行，引发难以排查的 Bug。       ║${NC}"
        echo -e "${RED}╠══════════════════════════════════════════════════════════╣${NC}"

        if [ $found_dirs -gt 0 ]; then
            echo -e "${RED}║  __pycache__ 目录 (${found_dirs} 个):${NC}"
            echo "$dir_list" | while read -r d; do
                echo -e "${RED}║    - $d${NC}"
            done
        fi
        if [ $found_files -gt 0 ]; then
            echo -e "${RED}║  .pyc 文件 (${found_files} 个):${NC}"
            echo "$file_list" | head -10 | while read -r f; do
                echo -e "${RED}║    - $f${NC}"
            done
        fi

        echo -e "${RED}${BOLD}╠══════════════════════════════════════════════════════════╣${NC}"

        if [ "$FORCE_MODE" = "true" ]; then
            echo -e "${YELLOW}${BOLD}║  已启用 --force 模式，自动清除缓存后继续启动...         ║${NC}"
            echo -e "${RED}${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
            echo ""

            # 自动清除（使用 find -exec 避免 while read 子shell 与 set -e 冲突）
            if [ -n "$dir_list" ]; then
                find "$CODE_DIR/app" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
            fi
            if [ -n "$file_list" ]; then
                find "$CODE_DIR/app" -type f -name '*.pyc' -not -path '*/__pycache__/*' -delete 2>/dev/null || true
            fi
            log_info "缓存清除完毕，继续启动..."
        else
            echo -e "${RED}${BOLD}║  请执行以下操作之一：                                   ║${NC}"
            echo -e "${RED}║  1. 手动清除: bash stop.sh (推荐)                      ║${NC}"
            echo -e "${RED}║  2. 强制启动: bash run.sh --force (自动清除)           ║${NC}"
            echo -e "${RED}${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
            echo ""
            log_error "启动已阻止！请先清除缓存残留。"
            exit 1
        fi
    else
        log_info "缓存检查通过 — 无 __pycache__ 残留"
    fi
}

# 执行缓存检查
check_pycache

# ─────────────────────────────────────────────────────
# 第2步: 端口占用检查
# ─────────────────────────────────────────────────────
check_port() {
    local port="${PORT:-40045}"
    if command -v ss &>/dev/null; then
        if ss -tlnp 2>/dev/null | grep -q ":$port "; then
            log_warn "端口 $port 已被占用，尝试清理..."
            pid=$(ss -tlnp 2>/dev/null | grep ":$port " | grep -oP 'pid=\K\d+' | head -1)
            if [ -n "$pid" ]; then
                kill "$pid" 2>/dev/null || true
                sleep 1
            fi
        fi
    elif command -v netstat &>/dev/null; then
        if netstat -tlnp 2>/dev/null | grep -q ":$port "; then
            log_warn "端口 $port 已被占用"
        fi
    fi
}

# ─────────────────────────────────────────────────────
# 第3步: 加载环境变量
# ─────────────────────────────────────────────────────
cd "$CODE_DIR"

# 确保 PYTHONDONTWRITEBYTECODE 也被写入环境变量文件
set -a
[ -f "$PROJECT_DIR/.env" ] && source "$PROJECT_DIR/.env"
set +a

# 再次确保（覆盖 .env 中的设置）
export PYTHONDONTWRITEBYTECODE=1

PORT="${PORT:-40045}"

# ─────────────────────────────────────────────────────
# 第4步: 激活虚拟环境并启动
# ─────────────────────────────────────────────────────
source venv/bin/activate

log_info "Python: $(which python3) ($(python3 --version))"
log_info "PYTHONDONTWRITEBYTECODE: ${PYTHONDONTWRITEBYTECODE:-未设置}"

check_port

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 启动 BOM Comparison..."
echo "  端口: $PORT"
echo "  进程数: 4 workers"

# 确保日志目录存在
mkdir -p "$LOGS_DIR"

nohup gunicorn \
    -w 4 \
    -b 0.0.0.0:$PORT \
    --access-logfile "$LOGS_DIR/gunicorn-access.log" \
    --error-logfile "$LOGS_DIR/gunicorn-error.log" \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    --pid "$PROJECT_DIR/app.pid" \
    wsgi:app \
    > "$LOGS_DIR/gunicorn-stdout.log" 2>&1 &

echo $! > "$PROJECT_DIR/app.pid"

# 快速验证
sleep 2
if kill -0 "$(cat "$PROJECT_DIR/app.pid")" 2>/dev/null; then
    log_info "已启动, PID=$(cat $PROJECT_DIR/app.pid)"
    # 验证 HTTP 可达
    sleep 1
    if command -v curl &>/dev/null; then
        http_code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/compare/" 2>/dev/null || echo "000")
        if [ "$http_code" = "200" ]; then
            log_info "HTTP 验证通过 (200)"
        else
            log_warn "HTTP 响应码: $http_code, 可能需要更多启动时间"
        fi
    fi
else
    log_error "启动失败！请检查日志: $LOGS_DIR/gunicorn-stdout.log"
    exit 1
fi

# ─────────────────────────────────────────────────────
# 验证: 确认 PYTHONDONTWRITEBYTECODE 已生效
# ─────────────────────────────────────────────────────
sleep 1
VERIFY_DIRS=$(find "$CODE_DIR/app" -type d -name '__pycache__' 2>/dev/null || true)
if [ -n "$VERIFY_DIRS" ]; then
    log_error "⚠️  PYTHONDONTWRITEBYTECODE 未生效！启动后生成了新的 __pycache__:"
    echo "$VERIFY_DIRS"
else
    log_info "PYTHONDONTWRITEBYTECODE 验证通过 — 无新缓存生成"
fi
