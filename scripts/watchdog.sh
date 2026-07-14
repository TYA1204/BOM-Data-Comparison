#!/bin/bash
# ============================================================================
# BOM Comparison — 看门狗守护脚本
# 功能: 每30秒检查服务是否存活，挂了自动拉起
# 用法: bash watchdog.sh {start|stop|status|restart}
# ============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PID_FILE="$PROJECT_DIR/watchdog.pid"
LOG_FILE="$PROJECT_DIR/logs/watchdog.log"
PORT="${PORT:-40045}"
CHECK_INTERVAL="${CHECK_INTERVAL:-30}"  # 检查间隔（秒）

mkdir -p "$PROJECT_DIR/logs"

# ─────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log_info()  { echo -e "${GREEN}[WATCHDOG]${NC} $*"; log "$*"; }
log_warn()  { echo -e "${YELLOW}[WATCHDOG]${NC} $*"; log "WARN: $*"; }
log_error() { echo -e "${RED}[WATCHDOG]${NC} $*"; log "ERROR: $*"; }

# ─────────────────────────────────────────────────────
# 健康检查：curl 端口是否可达
# ─────────────────────────────────────────────────────
is_alive() {
    local http_code
    http_code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 \
        "http://localhost:$PORT/compare/" 2>/dev/null || echo "000")
    [ "$http_code" = "200" ] || [ "$http_code" = "302" ]
}

# ─────────────────────────────────────────────────────
# 自动恢复
# ─────────────────────────────────────────────────────
do_recover() {
    log_warn "🔧 开始自动恢复..."

    # Step 1: 停止服务 + 清缓存
    if [ -f "$SCRIPT_DIR/stop.sh" ]; then
        bash "$SCRIPT_DIR/stop.sh" >> "$LOG_FILE" 2>&1 || true
        sleep 2
    fi

    # Step 2: 启动服务（AUTO_RECOVER 模式，不清缓存不堵路）
    if [ -f "$SCRIPT_DIR/run.sh" ]; then
        AUTO_RECOVER=true bash "$SCRIPT_DIR/run.sh" >> "$LOG_FILE" 2>&1
        local rc=$?
        sleep 3
        if [ $rc -eq 0 ] && is_alive; then
            log_info "✅ 自动恢复成功"
            return 0
        else
            log_error "❌ 自动恢复失败 (run.sh 退出码=$rc)"
            return 1
        fi
    else
        log_error "❌ 找不到 run.sh"
        return 1
    fi
}

# ─────────────────────────────────────────────────────
# 启动看门狗
# ─────────────────────────────────────────────────────
cmd_start() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        log_info "看门狗已在运行 (PID=$(cat "$PID_FILE"))"
        return 0
    fi

    log_info "🚀 启动看门狗..."
    log_info "   检查间隔: ${CHECK_INTERVAL}s"
    log_info "   监控端口: $PORT"

    # 后台启动监控循环
    nohup bash -c "
        echo \$\$ > '$PID_FILE'
        cd '$PROJECT_DIR'

        consecutive_failures=0
        while true; do
            sleep $CHECK_INTERVAL

            if curl -s -o /dev/null --connect-timeout 5 --max-time 10 \
                http://localhost:$PORT/compare/ > /dev/null 2>&1; then
                # 服务正常，重置计数器
                consecutive_failures=0
            else
                consecutive_failures=\$((consecutive_failures + 1))
                echo \"[\$(date '+%Y-%m-%d %H:%M:%S')] WARN: 服务无响应 (第 \$consecutive_failures 次)\" >> '$LOG_FILE'

                # 连续两次失败才执行恢复（避免偶发抖动误触）
                if [ \$consecutive_failures -ge 2 ]; then
                    echo \"[\$(date '+%Y-%m-%d %H:%M:%S')] ERROR: 服务挂了，触发自动恢复\" >> '$LOG_FILE'
                    bash '$SCRIPT_DIR/stop.sh' >> '$LOG_FILE' 2>&1 || true
                    sleep 2
                    AUTO_RECOVER=true bash '$SCRIPT_DIR/run.sh' >> '$LOG_FILE' 2>&1
                    sleep 3
                    consecutive_failures=0
                fi
            fi
        done
    " > /dev/null 2>&1 &

    sleep 1
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        log_info "看门狗已启动 (PID=$(cat "$PID_FILE"))"
    else
        log_error "看门狗启动失败"
        exit 1
    fi
}

# ─────────────────────────────────────────────────────
# 停止看门狗
# ─────────────────────────────────────────────────────
cmd_stop() {
    if [ ! -f "$PID_FILE" ]; then
        log_info "看门狗未运行"
        return 0
    fi

    local pid
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
        log_info "停止看门狗 (PID=$pid)..."
        kill "$pid" 2>/dev/null || true
        sleep 1
        # 如果还活着，强杀
        kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
        log_info "看门狗已停止"
    else
        log_info "看门狗进程已不存在"
    fi
    rm -f "$PID_FILE"
}

# ─────────────────────────────────────────────────────
# 查看状态
# ─────────────────────────────────────────────────────
cmd_status() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        local pid
        pid=$(cat "$PID_FILE")
        local uptime_str
        uptime_str=$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ' || echo "未知")
        echo -e "${GREEN}看门狗运行中${NC}"
        echo "  PID:     $pid"
        echo "  运行时间: $uptime_str"
        echo "  检查间隔: ${CHECK_INTERVAL}s"
        echo "  监控端口: $PORT"

        # 同时显示服务状态
        if is_alive; then
            echo -e "  服务状态: ${GREEN}正常${NC} (HTTP $PORT)"
        else
            echo -e "  服务状态: ${RED}异常${NC}"
        fi

        # 看最近的恢复记录
        if [ -f "$LOG_FILE" ]; then
            local last_recover
            last_recover=$(grep -c "自动恢复成功" "$LOG_FILE" 2>/dev/null || echo 0)
            local last_error
            last_error=$(grep -c "服务挂了" "$LOG_FILE" 2>/dev/null || echo 0)
            echo "  历史恢复: $last_recover 次"
            echo "  检测断连: $last_error 次"
        fi
    else
        echo -e "${RED}看门狗未运行${NC}"
        if is_alive; then
            echo -e "  服务状态: ${GREEN}正常${NC} (HTTP $PORT)"
        else
            echo -e "  服务状态: ${RED}异常${NC}"
        fi
    fi
}

# ─────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────
case "${1:-}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    restart) cmd_stop; sleep 1; cmd_start ;;
    *)
        echo "用法: bash watchdog.sh {start|stop|status|restart}"
        echo ""
        echo "环境变量:"
        echo "  PORT=40045           监控端口"
        echo "  CHECK_INTERVAL=30    检查间隔（秒）"
        exit 1
        ;;
esac
