#!/bin/bash
# ============================================================================
# BOM Comparison — 独立健康检查脚本
# 功能: 7项检查清单（进程/端口/页面/日志/DB/缓存/模块版本）
# 用法: bash health_check.sh [--json] [--silent]
#   --json    输出 JSON 格式
#   --silent  不输出彩色终端，只返回退出码
# ============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 自动检测项目根目录 (支持两种布局)
if [ -f "$SCRIPT_DIR/wsgi.py" ]; then
    PROJECT_DIR="$SCRIPT_DIR"
elif [ -f "$SCRIPT_DIR/code/wsgi.py" ]; then
    PROJECT_DIR="$SCRIPT_DIR"
elif [ -f "$SCRIPT_DIR/../wsgi.py" ]; then
    PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
elif [ -f "$SCRIPT_DIR/../code/wsgi.py" ]; then
    PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
else
    echo "错误: 无法定位项目根目录"
    echo "当前目录: $SCRIPT_DIR"
    exit 1
fi
CODE_DIR="$PROJECT_DIR/code"
PORT="${PORT:-40045}"
BASE_URL="http://localhost:$PORT"

JSON_OUT="false"
SILENT="false"
for arg in "$@"; do
    case "$arg" in
        --json)   JSON_OUT="true" ;;
        --silent) SILENT="true" ;;
    esac
done

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

# 检查结果收集
CHECKS=()
ERRORS=0
WARNINGS=0

add_check() {
    local name="$1" result="$2" detail="$3"
    CHECKS+=("{\"name\":\"$name\",\"result\":\"$result\",\"detail\":\"$detail\"}")
    if [ "$result" = "fail" ]; then
        ERRORS=$((ERRORS + 1))
        [ "$SILENT" != "true" ] && echo -e "  ${RED}✗${NC} $name: $detail"
    elif [ "$result" = "warn" ]; then
        WARNINGS=$((WARNINGS + 1))
        [ "$SILENT" != "true" ] && echo -e "  ${YELLOW}⚠${NC} $name: $detail"
    else
        [ "$SILENT" != "true" ] && echo -e "  ${GREEN}✓${NC} $name: $detail"
    fi
}

[ "$SILENT" != "true" ] && echo "=== BOM Comparison 健康检查 @ $(date '+%Y-%m-%d %H:%M:%S') ==="
[ "$SILENT" != "true" ] && echo ""

# ─────────────────────────────────────────────────────
# 检查 1: 进程存活
# ─────────────────────────────────────────────────────
check_process() {
    if [ -f "$PROJECT_DIR/app.pid" ]; then
        local pid
        pid=$(cat "$PROJECT_DIR/app.pid")
        if kill -0 "$pid" 2>/dev/null; then
            add_check "进程存活" "pass" "PID=$pid 运行中"
            return 0
        else
            add_check "进程存活" "fail" "PID 文件存在但进程未运行 ($pid)"
            return 1
        fi
    else
        # 尝试通过端口查找
        local pids
        pids=$(pgrep -f "gunicorn.*$PORT" 2>/dev/null || true)
        if [ -n "$pids" ]; then
            add_check "进程存活" "warn" "PID 文件不存在，但找到进程: $pids"
            return 0
        else
            add_check "进程存活" "fail" "无 PID 文件且未找到 gunicorn 进程"
            return 1
        fi
    fi
}

# ─────────────────────────────────────────────────────
# 检查 2: 端口监听
# ─────────────────────────────────────────────────────
check_port() {
    if command -v ss &>/dev/null; then
        if ss -tlnp 2>/dev/null | grep -q ":$PORT "; then
            add_check "端口监听" "pass" "端口 $PORT 已监听"
            return 0
        fi
    elif command -v netstat &>/dev/null; then
        if netstat -tlnp 2>/dev/null | grep -q ":$PORT "; then
            add_check "端口监听" "pass" "端口 $PORT 已监听"
            return 0
        fi
    fi
    add_check "端口监听" "fail" "端口 $PORT 未监听"
    return 1
}

# ─────────────────────────────────────────────────────
# 检查 3: HTTP 页面可达
# ─────────────────────────────────────────────────────
check_http() {
    if command -v curl &>/dev/null; then
        local http_code
        http_code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 "$BASE_URL/compare/" 2>/dev/null || echo "000")
        if [ "$http_code" = "200" ]; then
            add_check "HTTP 页面" "pass" "200 OK"
            return 0
        else
            add_check "HTTP 页面" "fail" "响应码: $http_code"
            return 1
        fi
    else
        add_check "HTTP 页面" "warn" "curl 不可用，跳过"
        return 0
    fi
}

# ─────────────────────────────────────────────────────
# 检查 4: API 端点
# ─────────────────────────────────────────────────────
check_api() {
    if command -v curl &>/dev/null; then
        local resp
        resp=$(curl -s --connect-timeout 5 "$BASE_URL/compare/api/result/1" 2>/dev/null || echo '{"ok":false}')
        local ok
        ok=$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print('1' if d.get('ok') or d.get('task_name') else '0')" 2>/dev/null || echo "0")
        if [ "$ok" = "1" ]; then
            add_check "API 端点" "pass" "正常响应"
            return 0
        else
            add_check "API 端点" "warn" "响应异常: $resp"
            return 0
        fi
    else
        add_check "API 端点" "warn" "curl 不可用，跳过"
        return 0
    fi
}

# ─────────────────────────────────────────────────────
# 检查 5: 日志
# ─────────────────────────────────────────────────────
check_logs() {
    local err_log="$PROJECT_DIR/logs/gunicorn-error.log"
    if [ -f "$err_log" ]; then
        # 检查最近 5 分钟的错误
        local recent_errors=0
        if [ -s "$err_log" ]; then
            recent_errors=$(grep -ci "error\|exception\|traceback" "$err_log" 2>/dev/null || echo "0")
        fi
        if [ "$recent_errors" -gt 10 ]; then
            add_check "错误日志" "warn" "最近5分钟有约$recent_errors条错误"
        else
            add_check "错误日志" "pass" "无异常"
        fi
    else
        add_check "错误日志" "warn" "日志文件不存在: $err_log"
    fi
}

# ─────────────────────────────────────────────────────
# 检查 6: __pycache__ 缓存
# ─────────────────────────────────────────────────────
check_pycache() {
    local cache_dirs
    cache_dirs=$(find "$CODE_DIR/app" -type d -name '__pycache__' 2>/dev/null | wc -l)
    local pyc_files
    pyc_files=$(find "$CODE_DIR/app" -type f -name '*.pyc' 2>/dev/null | wc -l)

    if [ "$cache_dirs" -gt 0 ] || [ "$pyc_files" -gt 0 ]; then
        add_check "缓存残留" "fail" "$cache_dirs 个 __pycache__ 目录, $pyc_files 个 .pyc 文件"
        return 1
    else
        add_check "缓存残留" "pass" "零残留"
        return 0
    fi
}

# ─────────────────────────────────────────────────────
# 检查 7: 关键模块版本（文件修改时间）
# ─────────────────────────────────────────────────────
check_modules() {
    local stale_count=0
    local ref_time
    ref_time=$(stat -c %Y "$CODE_DIR/wsgi.py" 2>/dev/null || echo "0")

    for module in app/__init__.py app/services/differ.py app/routes/compare.py app/templates/compare.html; do
        local mod_path="$CODE_DIR/$module"
        if [ -f "$mod_path" ]; then
            local mod_time
            mod_time=$(stat -c %Y "$mod_path" 2>/dev/null || echo "0")
            # 检查是否与新部署的 wsgi.py 时间戳接近（1小时误差）
            local diff=$((ref_time - mod_time))
            [ $diff -lt 0 ] && diff=$((-diff))
            if [ $diff -gt 3600 ]; then
                stale_count=$((stale_count + 1))
            fi
        fi
    done

    if [ "$stale_count" -gt 0 ]; then
        add_check "模块版本" "warn" "$stale_count 个模块时间戳与 wsgi.py 不一致"
    else
        add_check "模块版本" "pass" "所有模块时间戳一致"
    fi
}

# ─────────────────────────────────────────────────────
# 运行所有检查
# ─────────────────────────────────────────────────────
check_process
check_port
check_http
check_api
check_logs
check_pycache
check_modules

# ─────────────────────────────────────────────────────
# 输出结果
# ─────────────────────────────────────────────────────
[ "$SILENT" != "true" ] && echo ""

if [ "$JSON_OUT" = "true" ]; then
    echo "{"
    echo "  \"timestamp\": \"$(date -Iseconds)\","
    echo "  \"errors\": $ERRORS,"
    echo "  \"warnings\": $WARNINGS,"
    echo "  \"status\": \"$([ $ERRORS -eq 0 ] && echo 'healthy' || echo 'unhealthy')\","
    echo "  \"checks\": ["
    first=true
    for check in "${CHECKS[@]}"; do
        [ "$first" = "true" ] && first=false || echo ","
        echo -n "    $check"
    done
    echo ""
    echo "  ]"
    echo "}"
else
    if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
        echo -e "${GREEN}${BOLD}✅ 系统健康 — 7/7 项检查通过${NC}"
    elif [ $ERRORS -eq 0 ]; then
        echo -e "${YELLOW}${BOLD}⚠️  系统基本正常 — $WARNINGS 个警告${NC}"
    else
        echo -e "${RED}${BOLD}❌ 系统异常 — $ERRORS 个错误, $WARNINGS 个警告${NC}"
    fi
fi

exit $ERRORS
