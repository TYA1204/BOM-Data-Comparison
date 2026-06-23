#!/bin/bash
# ============================================================================
# BOM Comparison — 自动更新系统
# 功能: 环境检测 / 自动备份 / 更新安装 / 完整性验证 / 失败回滚 / 日志报告
# 用法: ./auto_update.sh [command] [options]
# ============================================================================
set -uo pipefail
# 注意: 不使用 set -e，因为通配符在空目录时会导致非预期退出
# 所有关键路径使用显式的 return/exit 进行错误处理

# ============================================================================
# 常量定义
# ============================================================================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
CODE_DIR="$PROJECT_DIR/code"
CONFIG_FILE="$PROJECT_DIR/update_config.conf"
LOG_DIR="$PROJECT_DIR/logs/auto_update"
BACKUP_DIR="$PROJECT_DIR/backups"
PACKAGE_DIR="$PROJECT_DIR/packages"
TIMESTAMP="$(date '+%Y%m%d-%H%M%S')"
DATE_STR="$(date '+%Y-%m-%d')"

# ============================================================================
# 默认配置 (会被 update_config.conf 覆盖)
# ============================================================================
UPDATE_SOURCE="package"
GIT_BRANCH="main"
UPDATE_URL=""
BACKUP_MAX_COUNT=10
AUTO_CLEANUP_PACKAGES="true"
STOP_TIMEOUT=10
START_TIMEOUT=15
HEALTH_CHECK_RETRIES=3
HEALTH_CHECK_INTERVAL=3
WEBHOOK_URL=""
CRON_SCHEDULE="0 3 * * *"
SILENT_MODE="false"
FORCE_MODE="false"
DRY_RUN="false"

# ============================================================================
# 颜色定义
# ============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ============================================================================
# 工具函数
# ============================================================================

# 加载配置文件
load_config() {
    if [ -f "$CONFIG_FILE" ]; then
        # shellcheck source=/dev/null
        source "$CONFIG_FILE"
        log_info "已加载配置: $CONFIG_FILE"
    else
        log_warn "配置文件不存在: $CONFIG_FILE，使用默认值"
    fi
}

# 初始化目录结构
init_dirs() {
    mkdir -p "$LOG_DIR"      2>/dev/null || true
    mkdir -p "$BACKUP_DIR/db" 2>/dev/null || true
    mkdir -p "$BACKUP_DIR/code" 2>/dev/null || true
    mkdir -p "$PACKAGE_DIR"  2>/dev/null || true
}

# 日志函数
_log() {
    local level="$1"; shift
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] [$level] $*"
    # 写入日志文件
    echo "$msg" >> "$LOG_DIR/${DATE_STR}.log"
    # 控制台输出 (静默模式下只输出 ERROR)
    if [ "$SILENT_MODE" != "true" ] || [ "$level" = "ERROR" ]; then
        echo "$msg"
    fi
}

log_info()  { _log "INFO"  "$@"; }
log_warn()  { _log "WARN"  "$@"; }
log_error() { _log "ERROR" "$@"; }
log_ok()    { _log "OK"    "$@"; }

# 彩色输出 (仅非静默模式)
c_ok()    { if [ "$SILENT_MODE" != "true" ]; then echo -e "${GREEN}[✓]${NC} $*"; fi; }
c_fail()  { if [ "$SILENT_MODE" != "true" ]; then echo -e "${RED}[✗]${NC} $*"; fi; }
c_warn()  { if [ "$SILENT_MODE" != "true" ]; then echo -e "${YELLOW}[!]${NC} $*"; fi; }
c_info()  { if [ "$SILENT_MODE" != "true" ]; then echo -e "${BLUE}[i]${NC} $*"; fi; }
c_title() { if [ "$SILENT_MODE" != "true" ]; then echo -e "\n${CYAN}══════════════════════════════════════════════${NC}"; echo -e "${CYAN}  $*${NC}"; echo -e "${CYAN}══════════════════════════════════════════════${NC}\n"; fi; }

# 确认操作 (force 模式自动跳过)
confirm() {
    if [ "$FORCE_MODE" = "true" ] || [ "$DRY_RUN" = "true" ]; then
        return 0
    fi
    local prompt="$1"
    echo -ne "${YELLOW}$prompt [y/N]: ${NC}"
    read -r reply
    case "$reply" in
        [Yy]|[Yy][Ee][Ss]) return 0 ;;
        *) return 1 ;;
    esac
}

# 发送通知 (webhook)
send_notification() {
    if [ -z "$WEBHOOK_URL" ]; then
        return 0
    fi
    local title="$1"
    local content="$2"
    curl -s -X POST "$WEBHOOK_URL" \
        -H "Content-Type: application/json" \
        -d "{\"title\":\"$title\",\"content\":\"$content\",\"timestamp\":\"$(date -Iseconds)\"}" \
        > /dev/null 2>&1 || true
}

# ============================================================================
# 环境检测
# ============================================================================

detect_environment() {
    c_title "环境检测"

    local all_ok=true

    # 1. 检查项目目录
    if [ -d "$CODE_DIR" ]; then
        c_ok "项目目录存在: $CODE_DIR"
        log_info "项目目录: $CODE_DIR"
    else
        c_fail "项目目录不存在: $CODE_DIR"
        log_error "项目目录不存在: $CODE_DIR"
        all_ok=false
    fi

    # 2. 检查 Python 虚拟环境
    if [ -f "$CODE_DIR/venv/bin/activate" ]; then
        c_ok "Python 虚拟环境存在"
        log_info "Python 虚拟环境存在"
    else
        c_warn "Python 虚拟环境不存在，将在更新时重建"
        log_warn "Python 虚拟环境不存在"
    fi

    # 3. 检查 Python 版本
    if command -v python3 &>/dev/null; then
        local py_ver
        py_ver=$(python3 --version 2>&1)
        c_ok "Python: $py_ver"
        log_info "Python: $py_ver"
    else
        c_fail "未找到 python3"
        log_error "未找到 python3"
        all_ok=false
    fi

    # 4. 检查磁盘空间 (至少 500MB 可用)
    local avail_kb
    avail_kb=$(df -k "$PROJECT_DIR" 2>/dev/null | awk 'NR==2 {print $4}')
    if [ -n "$avail_kb" ] && [ "$avail_kb" -gt 512000 ]; then
        local avail_mb=$((avail_kb / 1024))
        c_ok "磁盘空间: ${avail_mb}MB 可用"
        log_info "磁盘空间: ${avail_mb}MB 可用"
    elif [ -n "$avail_kb" ]; then
        local avail_mb=$((avail_kb / 1024))
        c_warn "磁盘空间不足: ${avail_mb}MB 可用 (建议 >500MB)"
        log_warn "磁盘空间不足: ${avail_mb}MB"
    else
        c_warn "无法检测磁盘空间"
        log_warn "无法检测磁盘空间"
    fi

    # 5. 检查关键文件
    for f in "$PROJECT_DIR/.env" "$PROJECT_DIR/run.sh" "$PROJECT_DIR/stop.sh"; do
        if [ -f "$f" ]; then
            c_ok "$(basename "$f") 存在"
        else
            c_warn "$(basename "$f") 不存在"
            log_warn "缺少文件: $f"
        fi
    done

    # 6. 检查数据库
    local db_file="$CODE_DIR/data/bom_compare.db"
    if [ -f "$db_file" ]; then
        local db_size
        db_size=$(du -h "$db_file" 2>/dev/null | cut -f1)
        c_ok "数据库存在: $db_size"
        log_info "数据库: $db_file ($db_size)"

        # 检查数据库可读性
        if command -v sqlite3 &>/dev/null; then
            local table_count
            table_count=$(sqlite3 "$db_file" "SELECT count(*) FROM sqlite_master WHERE type='table';" 2>/dev/null)
            if [ -n "$table_count" ]; then
                c_ok "数据库可读: ${table_count} 个表"
            else
                c_fail "数据库不可读"
                log_error "数据库不可读"
                all_ok=false
            fi
        fi
    else
        c_warn "数据库不存在: $db_file"
        log_warn "数据库不存在: $db_file"
    fi

    # 7. 检查端口占用
    local port="${PORT:-40045}"
    if command -v ss &>/dev/null; then
        if ss -lntp 2>/dev/null | grep -q ":$port "; then
            c_ok "端口 $port 正在监听"
            log_info "端口 $port 正在监听 (服务运行中)"
        else
            c_warn "端口 $port 未监听 (服务未运行)"
            log_warn "端口 $port 未监听"
        fi
    fi

    # 8. 检查服务进程 (仅本项目)
    local our_pids
    our_pids=$(_our_gunicorn_pids)
    if [ -n "$our_pids" ]; then
        c_ok "Gunicorn 进程运行中 (PID=$(echo "$our_pids" | head -1))"
        log_info "Gunicorn PID=$(echo "$our_pids" | tr '\n' ',') 运行中"
    elif [ -f "$PROJECT_DIR/app.pid" ]; then
        c_warn "PID 文件存在但项目进程不存在"
        log_warn "PID 文件过期"
    fi

    if [ "$all_ok" = false ]; then
        log_error "环境检测存在严重问题，请先修复"
        return 1
    fi

    c_ok "环境检测完成"
    return 0
}

# ============================================================================
# 备份系统
# ============================================================================

create_backup() {
    c_title "创建备份"

    local backup_id="backup-${TIMESTAMP}"
    local backup_code="$BACKUP_DIR/code/${backup_id}.tar.gz"
    local backup_db="$BACKUP_DIR/db/${backup_id}.db.bak"
    local db_file="$CODE_DIR/data/bom_compare.db"

    # 1. 备份数据库
    if [ -f "$db_file" ]; then
        if [ "$DRY_RUN" = "true" ]; then
            c_info "[DRY-RUN] 将备份数据库到 $backup_db"
            log_info "[DRY-RUN] 将备份数据库: $db_file → $backup_db"
        else
            cp "$db_file" "$backup_db"
            c_ok "数据库已备份: $(du -h "$backup_db" | cut -f1)"
            log_info "数据库已备份: $backup_db"
        fi
    else
        c_warn "未找到数据库文件，跳过数据库备份"
        log_warn "数据库文件不存在，跳过备份"
    fi

    # 2. 备份代码
    if [ "$DRY_RUN" = "true" ]; then
        c_info "[DRY-RUN] 将备份代码到 $backup_code"
        log_info "[DRY-RUN] 将备份代码: $CODE_DIR → $backup_code"
    else
        tar czf "$backup_code" \
            --exclude='code/venv' \
            --exclude='code/uploads' \
            --exclude='code/__pycache__' \
            --exclude='code/app/__pycache__' \
            --exclude='code/app/**/__pycache__' \
            --exclude='backup-*.tar.gz' \
            -C "$PROJECT_DIR" \
            code/ 2>/dev/null

        if [ -f "$backup_code" ]; then
            c_ok "代码已备份: $(du -h "$backup_code" | cut -f1)"
            log_info "代码已备份: $backup_code"
        else
            c_fail "代码备份失败"
            log_error "代码备份失败"
            return 1
        fi
    fi

    # 3. 备份 .env 和脚本
    if [ "$DRY_RUN" = "true" ]; then
        c_info "[DRY-RUN] 将备份配置文件"
    else
        cp "$PROJECT_DIR/.env" "$BACKUP_DIR/code/${backup_id}.env" 2>/dev/null || true
        cp "$PROJECT_DIR/run.sh" "$BACKUP_DIR/code/${backup_id}.run.sh" 2>/dev/null || true
        cp "$PROJECT_DIR/stop.sh" "$BACKUP_DIR/code/${backup_id}.stop.sh" 2>/dev/null || true
        c_ok "配置文件已备份"
        log_info "配置文件已备份"
    fi

    # 4. 写入备份元信息
    if [ "$DRY_RUN" != "true" ]; then
        cat > "$BACKUP_DIR/code/${backup_id}.meta" << EOF
backup_id=$backup_id
timestamp=$TIMESTAMP
date=$(date -Iseconds)
source=$UPDATE_SOURCE
EOF
        log_info "备份元信息已写入"
    fi

    # 5. 清理旧备份 (保留最近 N 个)
    cleanup_old_backups

    echo "$backup_id"  # 返回备份 ID
}

cleanup_old_backups() {
    local max_count="${BACKUP_MAX_COUNT:-10}"
    c_info "清理旧备份 (保留最近 $max_count 个)..."

    # 清理代码备份
    local code_count
    code_count=$(ls -1t "$BACKUP_DIR/code"/*.tar.gz 2>/dev/null | wc -l)
    if [ "$code_count" -gt "$max_count" ]; then
        ls -1t "$BACKUP_DIR/code"/*.tar.gz | tail -n +$((max_count + 1)) | while read -r f; do
            local base
            base=$(basename "$f" .tar.gz)
            rm -f "$f" "${BACKUP_DIR}/code/${base}.env" "${BACKUP_DIR}/code/${base}.run.sh" "${BACKUP_DIR}/code/${base}.stop.sh" "${BACKUP_DIR}/code/${base}.meta"
            log_info "清理旧备份: $base"
        done
        c_ok "已清理 $((code_count - max_count)) 个旧代码备份"
    fi

    # 清理数据库备份
    local db_count
    db_count=$(ls -1t "$BACKUP_DIR/db"/*.db.bak 2>/dev/null | wc -l)
    if [ "$db_count" -gt "$max_count" ]; then
        ls -1t "$BACKUP_DIR/db"/*.db.bak | tail -n +$((max_count + 1)) | while read -r f; do
            rm -f "$f"
            log_info "清理旧数据库备份: $(basename "$f")"
        done
        c_ok "已清理 $((db_count - max_count)) 个旧数据库备份"
    fi
}

list_backups() {
    echo ""
    echo "===== 可用备份列表 ====="
    echo ""
    echo "--- 代码备份 ---"
    if ls "$BACKUP_DIR/code"/*.tar.gz &>/dev/null; then
        for f in $(ls -1t "$BACKUP_DIR/code"/*.tar.gz); do
            local base size
            base=$(basename "$f" .tar.gz)
            size=$(du -h "$f" | cut -f1)
            echo "  $base  ($size)"
        done
    else
        echo "  (无)"
    fi

    echo ""
    echo "--- 数据库备份 ---"
    if ls "$BACKUP_DIR/db"/*.db.bak &>/dev/null; then
        for f in $(ls -1t "$BACKUP_DIR/db"/*.db.bak); do
            local base size
            base=$(basename "$f" .db.bak)
            size=$(du -h "$f" | cut -f1)
            echo "  $base  ($size)"
        done
    else
        echo "  (无)"
    fi
    echo ""
}

# ============================================================================
# 服务管理
# ============================================================================

stop_service() {
    c_info "停止服务..."

    if [ "$DRY_RUN" = "true" ]; then
        c_info "[DRY-RUN] 将执行 stop.sh"
        return 0
    fi

    local port="${PORT:-40045}"

    # 优先使用 stop.sh
    if [ -f "$PROJECT_DIR/stop.sh" ]; then
        cd "$PROJECT_DIR" && bash stop.sh 2>&1 | while read -r line; do
            log_info "stop.sh: $line"
        done
    else
        # 备用: 手动停止
        if [ -f "$PROJECT_DIR/app.pid" ]; then
            local pid
            pid=$(cat "$PROJECT_DIR/app.pid")
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null || true
                log_info "发送 SIGTERM 到 PID=$pid"
            fi
        fi
    fi

    # 等待本项目的 gunicorn 进程退出
    local waited=0
    while [ $waited -lt "${STOP_TIMEOUT:-10}" ]; do
        local remaining
        remaining=$(_our_gunicorn_pids)
        if [ -z "$remaining" ]; then
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done

    # 如果仍未退出，强制终止（仅本项目进程）
    local remaining
    remaining=$(_our_gunicorn_pids)
    if [ -n "$remaining" ]; then
        c_warn "正常停止超时，强制终止本项目进程..."
        log_warn "强制终止 gunicorn 进程: $(echo "$remaining" | tr '\n' ' ')"
        for pid in $remaining; do
            kill -9 "$pid" 2>/dev/null || true
        done
        sleep 2
    fi

    # 最终确认
    remaining=$(_our_gunicorn_pids)
    if [ -n "$remaining" ]; then
        c_fail "服务停止失败，仍有进程残留"
        log_error "服务停止失败: $(echo "$remaining" | tr '\n' ' ')"
        return 1
    fi

    rm -f "$PROJECT_DIR/app.pid"
    c_ok "服务已停止"
    log_info "服务已停止"
    return 0
}

start_service() {
    c_info "启动服务..."

    if [ "$DRY_RUN" = "true" ]; then
        c_info "[DRY-RUN] 将执行 run.sh"
        return 0
    fi

    if [ -f "$PROJECT_DIR/run.sh" ]; then
        cd "$PROJECT_DIR" && bash run.sh 2>&1 | while read -r line; do
            log_info "run.sh: $line"
        done
    else
        c_fail "run.sh 不存在"
        log_error "run.sh 不存在"
        return 1
    fi

    # 等待服务启动
    local waited=0
    local port="${PORT:-40045}"
    while [ $waited -lt "${START_TIMEOUT:-15}" ]; do
        if [ -f "$PROJECT_DIR/app.pid" ]; then
            local pid
            pid=$(cat "$PROJECT_DIR/app.pid")
            if kill -0 "$pid" 2>/dev/null; then
                c_ok "服务已启动 (PID=$pid)"
                log_info "服务已启动: PID=$pid"
                return 0
            fi
        fi
        sleep 1
        waited=$((waited + 1))
    done

    c_fail "服务启动超时"
    log_error "服务启动超时 ($START_TIMEOUT 秒)"
    return 1
}

# 检查本项目的 gunicorn 是否在运行
# 策略: 先检查 PID 文件, 再按项目路径 + 端口精确匹配
_our_gunicorn_pids() {
    local pids=""

    # 方法1: PID 文件
    if [ -f "$PROJECT_DIR/app.pid" ]; then
        local pid
        pid=$(cat "$PROJECT_DIR/app.pid" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            pids="$pid"
        fi
    fi

    # 方法2: 按项目路径精确匹配 (避免误杀其他用户的 gunicorn)
    local port="${PORT:-40045}"
    local matched
    matched=$(pgrep -f "gunicorn.*${port}.*wsgi:app" 2>/dev/null || true)
    if [ -n "$matched" ]; then
        if [ -n "$pids" ]; then
            pids="$pids"$'\n'"$matched"
        else
            pids="$matched"
        fi
    fi

    echo "$pids" | sort -u
}

service_status() {
    local pids
    pids=$(_our_gunicorn_pids)
    if [ -n "$pids" ]; then
        echo "true"
    else
        echo "false"
    fi
}

# ============================================================================
# 更新检测
# ============================================================================

check_for_updates() {
    c_title "检查更新"

    case "$UPDATE_SOURCE" in
        package)
            check_package_update
            ;;
        git)
            check_git_update
            ;;
        http)
            check_http_update
            ;;
        *)
            c_fail "未知的更新源: $UPDATE_SOURCE"
            log_error "未知更新源: $UPDATE_SOURCE"
            return 1
            ;;
    esac
}

check_package_update() {
    c_info "检查 $PACKAGE_DIR 中的更新包..."

    if [ ! -d "$PACKAGE_DIR" ]; then
        c_fail "包目录不存在: $PACKAGE_DIR"
        log_error "包目录不存在: $PACKAGE_DIR"
        return 1
    fi

    # 查找最新的 tar.gz 包
    local latest_pkg
    latest_pkg=$(ls -1t "$PACKAGE_DIR"/*.tar.gz 2>/dev/null | head -1)

    if [ -z "$latest_pkg" ]; then
        c_info "未发现更新包"
        log_info "packages/ 目录为空，无可用更新"
        return 2  # 返回 2 = 无更新
    fi

    local pkg_name pkg_size
    pkg_name=$(basename "$latest_pkg")
    pkg_size=$(du -h "$latest_pkg" | cut -f1)

    c_ok "发现更新包: $pkg_name ($pkg_size)"
    log_info "发现更新包: $latest_pkg ($pkg_size)"

    # 检查是否已应用（通过 applied_packages 文件）
    local applied_file="$PACKAGE_DIR/.applied_packages"
    if [ -f "$applied_file" ] && grep -Fxq "$pkg_name" "$applied_file"; then
        c_warn "此更新包已应用过: $pkg_name"
        log_warn "更新包已应用: $pkg_name"
        return 2
    fi

    # 验证包完整性
    if ! tar tzf "$latest_pkg" > /dev/null 2>&1; then
        c_fail "更新包损坏或格式错误: $pkg_name"
        log_error "更新包验证失败: $pkg_name"
        return 1
    fi

    # 验证包内容包含 code/ 目录
    if ! tar tzf "$latest_pkg" 2>/dev/null | grep -q "^code/"; then
        c_fail "更新包缺少 code/ 目录: $pkg_name"
        log_error "更新包结构无效，缺少 code/ 目录"
        return 1
    fi

    c_ok "更新包验证通过"
    echo "$latest_pkg"  # 返回包路径
    return 0
}

check_git_update() {
    c_info "检查 Git 更新..."

    if [ ! -d "$CODE_DIR/.git" ]; then
        c_fail "代码目录不是 Git 仓库"
        log_error "$CODE_DIR 不是 Git 仓库"
        return 1
    fi

    cd "$CODE_DIR"

    # 获取远程更新
    if ! git fetch origin "${GIT_BRANCH:-main}" 2>&1; then
        c_fail "Git fetch 失败"
        log_error "Git fetch 失败"
        return 1
    fi

    # 比较本地与远程
    local local_hash remote_hash
    local_hash=$(git rev-parse HEAD 2>/dev/null)
    remote_hash=$(git rev-parse "origin/${GIT_BRANCH:-main}" 2>/dev/null)

    if [ "$local_hash" = "$remote_hash" ]; then
        c_info "代码已是最新 (${local_hash:0:8})"
        log_info "代码已是最新: ${local_hash:0:8}"
        return 2
    fi

    c_ok "发现更新: ${local_hash:0:8} → ${remote_hash:0:8}"
    log_info "Git 更新可用: $local_hash → $remote_hash"
    return 0
}

check_http_update() {
    c_info "检查 HTTP 更新..."

    if [ -z "$UPDATE_URL" ]; then
        c_fail "未配置 UPDATE_URL"
        log_error "未配置 UPDATE_URL"
        return 1
    fi

    # 下载到临时文件检查
    local tmp_pkg="$PACKAGE_DIR/.tmp_update_$$.tar.gz"
    if curl -sL --connect-timeout 30 -o "$tmp_pkg" "$UPDATE_URL"; then
        if tar tzf "$tmp_pkg" > /dev/null 2>&1; then
            local size
            size=$(du -h "$tmp_pkg" | cut -f1)
            c_ok "HTTP 更新包下载成功 ($size)"
            log_info "HTTP 更新包下载成功: $UPDATE_URL ($size)"
            mv "$tmp_pkg" "$PACKAGE_DIR/update-${TIMESTAMP}.tar.gz"
            echo "$PACKAGE_DIR/update-${TIMESTAMP}.tar.gz"
            return 0
        else
            rm -f "$tmp_pkg"
            c_fail "下载的包格式无效"
            log_error "HTTP 更新包格式无效"
            return 1
        fi
    else
        rm -f "$tmp_pkg"
        c_fail "HTTP 下载失败"
        log_error "HTTP 下载失败: $UPDATE_URL"
        return 1
    fi
}

# ============================================================================
# 应用更新
# ============================================================================

apply_update() {
    local update_source="$1"  # 包路径 或 "git"
    c_title "应用更新"

    case "$UPDATE_SOURCE" in
        package|http)
            apply_package_update "$update_source"
            ;;
        git)
            apply_git_update
            ;;
    esac
}

apply_package_update() {
    local pkg_file="$1"

    c_info "解压更新包: $(basename "$pkg_file")"

    if [ "$DRY_RUN" = "true" ]; then
        c_info "[DRY-RUN] 将解压 $pkg_file 到 $CODE_DIR"
        return 0
    fi

    # 备份旧 requirements.txt 以比对依赖变化
    local old_req="$BACKUP_DIR/.old_requirements.txt"
    if [ -f "$CODE_DIR/requirements.txt" ]; then
        cp "$CODE_DIR/requirements.txt" "$old_req"
    else
        rm -f "$old_req"
        touch "$old_req"
    fi

    # 清空旧代码（保留 venv, uploads, data）
    c_info "清理旧代码..."
    find "$CODE_DIR" -mindepth 1 -maxdepth 1 \
        ! -name 'venv' \
        ! -name 'uploads' \
        ! -name 'data' \
        -exec rm -rf {} + 2>/dev/null || true

    # 解压新代码
    if ! tar xzf "$pkg_file" -C "$PROJECT_DIR" 2>/dev/null; then
        c_fail "解压失败，触发回滚"
        log_error "解压失败: $pkg_file"
        return 1
    fi

    c_ok "代码已解压"

    # 检查依赖变化
    local deps_changed=false
    if [ -f "$old_req" ] && [ -f "$CODE_DIR/requirements.txt" ]; then
        if ! diff -q "$old_req" "$CODE_DIR/requirements.txt" > /dev/null 2>&1; then
            deps_changed=true
            c_info "检测到依赖变化"
            log_info "requirements.txt 已变更"
        fi
    fi

    # 更新依赖
    if [ "$deps_changed" = true ]; then
        update_dependencies
    fi

    # 标记包已应用
    local pkg_name
    pkg_name=$(basename "$pkg_file")
    echo "$pkg_name" >> "$PACKAGE_DIR/.applied_packages"

    # 清理已应用的包 (可选)
    if [ "$AUTO_CLEANUP_PACKAGES" = "true" ]; then
        rm -f "$pkg_file"
        c_info "已清理更新包: $pkg_name"
        log_info "已清理更新包: $pkg_name"
    fi

    rm -f "$old_req"
    c_ok "更新应用完成"
    return 0
}

apply_git_update() {
    c_info "拉取 Git 更新..."

    if [ "$DRY_RUN" = "true" ]; then
        c_info "[DRY-RUN] 将执行 git pull"
        return 0
    fi

    cd "$CODE_DIR"

    if ! git pull origin "${GIT_BRANCH:-main}" 2>&1; then
        c_fail "Git pull 失败"
        log_error "Git pull 失败"
        return 1
    fi

    c_ok "Git 更新成功"

    # 检查依赖变化
    update_dependencies

    return 0
}

update_dependencies() {
    c_info "检查并更新 Python 依赖..."

    if [ "$DRY_RUN" = "true" ]; then
        c_info "[DRY-RUN] 将执行 pip install"
        return 0
    fi

    if [ ! -f "$CODE_DIR/requirements.txt" ]; then
        c_warn "未找到 requirements.txt"
        log_warn "未找到 requirements.txt"
        return 0
    fi

    cd "$CODE_DIR"

    # 激活虚拟环境
    if [ -f "venv/bin/activate" ]; then
        # shellcheck source=/dev/null
        source venv/bin/activate
    else
        c_warn "虚拟环境不存在，将重建"
        log_warn "虚拟环境不存在，重建中..."
        rm -rf venv
        python3 -m venv venv
        # shellcheck source=/dev/null
        source venv/bin/activate
        pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com 2>/dev/null
    fi

    # 安装依赖
    c_info "安装 Python 依赖..."
    if pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com 2>&1 | while read -r line; do
        log_info "pip: $line"
    done; then
        # 确保 gunicorn 已安装
        pip install gunicorn -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com 2>/dev/null || true
        c_ok "依赖安装完成"
        log_info "依赖安装完成"
    else
        c_fail "依赖安装失败"
        log_error "pip install 失败"
        deactivate 2>/dev/null || true
        return 1
    fi

    deactivate 2>/dev/null || true
    return 0
}

# ============================================================================
# 验证更新
# ============================================================================

verify_update() {
    c_title "验证更新 (7 项检查)"

    if [ "$DRY_RUN" = "true" ]; then
        c_info "[DRY-RUN] 将执行验证检查"
        return 0
    fi

    local all_ok=true
    local port="${PORT:-40045}"

    # 1. 进程检查 (仅本项目)
    c_info "检查 1/7: Gunicorn 进程..."
    local our_pids
    our_pids=$(_our_gunicorn_pids)
    if [ -n "$our_pids" ]; then
        local workers
        workers=$(echo "$our_pids" | wc -l)
        c_ok "Gunicorn 运行中 ($workers 进程)"
        log_info "验证: Gunicorn 进程 OK ($workers workers)"
    else
        c_fail "Gunicorn 未运行"
        log_error "验证: Gunicorn 进程 MISSING"
        all_ok=false
    fi

    # 2. 端口检查
    c_info "检查 2/7: 端口监听..."
    if command -v ss &>/dev/null; then
        if ss -lntp 2>/dev/null | grep -q ":$port "; then
            c_ok "端口 $port 正在监听"
            log_info "验证: 端口 $port OK"
        else
            c_fail "端口 $port 未监听"
            log_error "验证: 端口 $port NOT LISTENING"
            all_ok=false
        fi
    fi

    # 3-5. HTTP 健康检查
    local health_ok=true
    for i in $(seq 1 "${HEALTH_CHECK_RETRIES:-3}"); do
        c_info "HTTP 健康检查 (第 $i 次)..."

        # 首页
        local resp
        resp=$(curl -sI -o /dev/null -w '%{http_code}' --connect-timeout 5 "http://127.0.0.1:$port/" 2>/dev/null)
        if [ "$resp" = "200" ]; then
            c_ok "检查 3/7: 首页可用 (200)"
            log_info "验证: 首页 HTTP $resp"
            break
        else
            if [ "$i" -lt "${HEALTH_CHECK_RETRIES:-3}" ]; then
                c_warn "首页返回 $resp，${HEALTH_CHECK_INTERVAL}s 后重试..."
                sleep "${HEALTH_CHECK_INTERVAL:-3}"
            else
                c_fail "检查 3/7: 首页不可用 (HTTP $resp)"
                log_error "验证: 首页 HTTP $resp"
                health_ok=false
            fi
        fi
    done

    # 上传页面
    resp=$(curl -sI -o /dev/null -w '%{http_code}' --connect-timeout 5 "http://127.0.0.1:$port/upload/" 2>/dev/null)
    if [ "$resp" = "200" ]; then
        c_ok "检查 4/7: 上传页面可用 (200)"
        log_info "验证: 上传页面 HTTP $resp"
    else
        c_fail "检查 4/7: 上传页面不可用 (HTTP $resp)"
        log_error "验证: 上传页面 HTTP $resp"
        all_ok=false
    fi

    # 比对页面
    resp=$(curl -sI -o /dev/null -w '%{http_code}' --connect-timeout 5 "http://127.0.0.1:$port/compare/" 2>/dev/null)
    if [ "$resp" = "200" ]; then
        c_ok "检查 5/7: 比对页面可用 (200)"
        log_info "验证: 比对页面 HTTP $resp"
    else
        c_fail "检查 5/7: 比对页面不可用 (HTTP $resp)"
        log_error "验证: 比对页面 HTTP $resp"
        all_ok=false
    fi

    # 6. 错误日志
    c_info "检查 6/7: 错误日志..."
    local err_log="$PROJECT_DIR/logs/gunicorn-error.log"
    if [ -f "$err_log" ]; then
        local recent_errors
        recent_errors=$(tail -20 "$err_log" 2>/dev/null | grep -ci "error\|traceback\|exception" || true)
        if [ "$recent_errors" -eq 0 ]; then
            c_ok "错误日志干净"
            log_info "验证: 错误日志 OK (最近 20 行无错误)"
        else
            c_warn "错误日志包含 $recent_errors 条错误记录"
            log_warn "验证: 错误日志包含 $recent_errors 条错误"
        fi
    else
        c_warn "错误日志文件不存在"
        log_warn "验证: 错误日志文件不存在"
    fi

    # 7. 数据库可读
    c_info "检查 7/7: 数据库可读..."
    local db_file="$CODE_DIR/data/bom_compare.db"
    if [ -f "$db_file" ] && command -v sqlite3 &>/dev/null; then
        local count
        count=$(sqlite3 "$db_file" "SELECT count(*) FROM bom_header;" 2>/dev/null)
        if [ -n "$count" ]; then
            c_ok "数据库可读: bom_header 表 ($count 行)"
            log_info "验证: 数据库 OK (bom_header: $count rows)"
        else
            c_fail "数据库查询失败"
            log_error "验证: 数据库查询失败"
            all_ok=false
        fi
    else
        c_warn "无法检查数据库"
        log_warn "验证: 数据库检查跳过"
    fi

    if [ "$all_ok" = false ] || [ "$health_ok" = false ]; then
        c_fail "验证失败，触发回滚"
        log_error "验证失败"
        return 1
    fi

    c_ok "全部验证通过"
    log_ok "全部 7 项验证通过"
    return 0
}

# ============================================================================
# 回滚机制
# ============================================================================

rollback() {
    local target_backup="${1:-}"

    c_title "执行回滚"

    if [ "$DRY_RUN" = "true" ]; then
        c_info "[DRY-RUN] 将回滚到备份"
        return 0
    fi

    # 如果没有指定备份，使用最新的
    if [ -z "$target_backup" ]; then
        target_backup=$(ls -1t "$BACKUP_DIR/code"/*.tar.gz 2>/dev/null | head -1)
        if [ -z "$target_backup" ]; then
            c_fail "没有可用的备份"
            log_error "回滚失败: 无可用备份"
            return 1
        fi
        c_info "使用最新备份: $(basename "$target_backup")"
    else
        # 支持指定 backup_id
        if [ ! -f "$BACKUP_DIR/code/${target_backup}.tar.gz" ]; then
            c_fail "备份不存在: $target_backup"
            log_error "回滚失败: 备份 $target_backup 不存在"
            return 1
        fi
        target_backup="$BACKUP_DIR/code/${target_backup}.tar.gz"
    fi

    local backup_base
    backup_base=$(basename "$target_backup" .tar.gz)
    log_info "开始回滚到: $backup_base"

    # 1. 停止服务
    stop_service || true  # 即使停服失败也继续

    # 2. 恢复代码
    c_info "恢复代码..."
    find "$CODE_DIR" -mindepth 1 -maxdepth 1 \
        ! -name 'venv' \
        ! -name 'uploads' \
        ! -name 'data' \
        -exec rm -rf {} + 2>/dev/null || true

    if tar xzf "$target_backup" -C "$PROJECT_DIR" 2>/dev/null; then
        c_ok "代码已恢复"
        log_info "代码已从 $backup_base 恢复"
    else
        c_fail "代码恢复失败"
        log_error "代码恢复失败: $target_backup"
        return 1
    fi

    # 3. 恢复数据库
    local db_backup="$BACKUP_DIR/db/${backup_base}.db.bak"
    local db_file="$CODE_DIR/data/bom_compare.db"
    if [ -f "$db_backup" ]; then
        cp "$db_backup" "$db_file"
        c_ok "数据库已恢复"
        log_info "数据库已从 $backup_base 恢复"
    else
        c_warn "未找到数据库备份，保持当前数据库"
        log_warn "回滚: 未找到数据库备份 $backup_base"
    fi

    # 4. 恢复配置文件
    local env_backup="$BACKUP_DIR/code/${backup_base}.env"
    if [ -f "$env_backup" ]; then
        cp "$env_backup" "$PROJECT_DIR/.env"
        c_ok ".env 已恢复"
    fi

    # 5. 重建虚拟环境（如果 requirements 有变）
    if [ -f "$CODE_DIR/requirements.txt" ]; then
        c_info "重建虚拟环境..."
        cd "$CODE_DIR"
        rm -rf venv
        python3 -m venv venv
        # shellcheck source=/dev/null
        source venv/bin/activate
        pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com 2>/dev/null
        pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com 2>/dev/null
        pip install gunicorn -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com 2>/dev/null
        deactivate
        c_ok "虚拟环境已重建"
        log_info "回滚: 虚拟环境已重建"
    fi

    # 6. 重启服务
    start_service

    # 7. 快速验证
    sleep 2
    local port="${PORT:-40045}"
    if curl -sI --connect-timeout 5 "http://127.0.0.1:$port/" 2>/dev/null | grep -q "200"; then
        c_ok "回滚成功，服务已恢复"
        log_ok "回滚完成: 服务已恢复"
        send_notification "BOM Comparison 回滚完成" "已回滚到 $backup_base，服务正常运行"
        return 0
    else
        c_fail "回滚后服务验证失败，请手动检查"
        log_error "回滚后验证失败"
        send_notification "BOM Comparison 回滚异常" "回滚后服务验证失败，需要人工介入"
        return 1
    fi
}

# ============================================================================
# 报告生成
# ============================================================================

generate_report() {
    local action="$1"    # update / rollback / backup
    local status="$2"    # success / failed
    local details="${3:-}"
    local duration="${4:-}"

    local report_file="$LOG_DIR/report-${TIMESTAMP}.md"

    cat > "$report_file" << 'REPORT_EOF'
# BOM Comparison — 更新报告

REPORT_EOF

    {
        echo ""
        echo "| 项目 | 内容 |"
        echo "|------|------|"
        echo "| 操作类型 | $action |"
        echo "| 执行时间 | $(date '+%Y-%m-%d %H:%M:%S') |"
        echo "| 执行结果 | **$status** |"
        echo "| 更新源 | $UPDATE_SOURCE |"
        if [ -n "$duration" ]; then
            echo "| 耗时 | ${duration} |"
        fi
        if [ -n "$details" ]; then
            echo "| 详情 | $details |"
        fi
        echo "| 服务器 | $(hostname 2>/dev/null || echo 'unknown') |"
        echo "| 端口 | ${PORT:-40045} |"
        echo "| 日志文件 | $LOG_DIR/${DATE_STR}.log |"
        echo ""

        # 备份信息
        echo "## 备份信息"
        echo ""
        local latest_backup
        latest_backup=$(ls -1t "$BACKUP_DIR/code"/*.tar.gz 2>/dev/null | head -1)
        if [ -n "$latest_backup" ]; then
            echo "- 最新备份: $(basename "$latest_backup") ($(du -h "$latest_backup" | cut -f1))"
        fi
        local db_latest
        db_latest=$(ls -1t "$BACKUP_DIR/db"/*.db.bak 2>/dev/null | head -1)
        if [ -n "$db_latest" ]; then
            echo "- 最新数据库备份: $(basename "$db_latest") ($(du -h "$db_latest" | cut -f1))"
        fi

    } >> "$report_file"

    c_ok "报告已生成: $report_file"
    log_info "更新报告: $report_file"
    echo "$report_file"
}

# ============================================================================
# 定时任务调度
# ============================================================================

do_schedule() {
    local action="${1:-status}"

    case "$action" in
        install)
            install_cron
            ;;
        remove)
            remove_cron
            ;;
        status)
            show_cron_status
            ;;
        daemon)
            start_daemon
            ;;
        stop-daemon)
            stop_daemon
            ;;
        *)
            echo "用法: $0 schedule [install|remove|status|daemon|stop-daemon]"
            ;;
    esac
}

install_cron() {
    c_title "安装定时任务"

    if ! command -v crontab &>/dev/null; then
        c_fail "crontab 命令不可用"
        echo ""
        echo "  此服务器未安装 crontab，请使用守护进程模式:"
        echo "    $0 schedule daemon    # 启动守护进程 (后台循环检查)"
        echo ""
        return 1
    fi

    local cron_cmd="$PROJECT_DIR/auto_update.sh update --silent --force"
    local schedule="${CRON_SCHEDULE:-0 3 * * *}"

    # 检查是否已存在
    if crontab -l 2>/dev/null | grep -q "auto_update.sh"; then
        c_warn "定时任务已存在，将替换"
        log_info "定时任务已存在，替换中..."
    fi

    # 添加新任务（保留其他任务）
    local tmp_cron
    tmp_cron=$(mktemp)
    crontab -l 2>/dev/null | grep -v "auto_update.sh" > "$tmp_cron" || true
    echo "$schedule $cron_cmd # BOM Comparison Auto-Update" >> "$tmp_cron"

    if crontab "$tmp_cron" 2>/dev/null; then
        rm -f "$tmp_cron"
        c_ok "定时任务已安装: $schedule"
        log_info "定时任务已安装: $schedule $cron_cmd"

        echo ""
        echo "  调度: $schedule"
        echo "  命令: $cron_cmd"
        echo "  日志: $LOG_DIR/"
        echo ""
        echo "  💡 提示: 更新包请放入 $PACKAGE_DIR/"
        echo ""
    else
        rm -f "$tmp_cron"
        c_fail "定时任务安装失败"
        log_error "crontab 安装失败"
    fi
}

remove_cron() {
    c_title "移除定时任务"

    if crontab -l 2>/dev/null | grep -q "auto_update.sh"; then
        local tmp_cron
        tmp_cron=$(mktemp)
        crontab -l 2>/dev/null | grep -v "auto_update.sh" > "$tmp_cron" || true
        if crontab "$tmp_cron" 2>/dev/null; then
            rm -f "$tmp_cron"
            c_ok "定时任务已移除"
            log_info "定时任务已从 crontab 移除"
        else
            rm -f "$tmp_cron"
            c_fail "移除失败"
        fi
    else
        c_info "未找到定时任务"
    fi
}

show_cron_status() {
    c_title "定时任务状态"

    # 显示守护进程状态
    echo ""
    echo "  == 守护进程 =="
    show_daemon_status
    echo ""

    if crontab -l 2>/dev/null | grep -q "auto_update.sh"; then
        echo ""
        crontab -l 2>/dev/null | grep "auto_update.sh" | while read -r line; do
            echo "  $line"
        done
        echo ""

        # 显示最近的执行日志
        if ls "$LOG_DIR"/*.log &>/dev/null; then
            echo "  最近执行记录:"
            echo "  ---"
            for logf in $(ls -1t "$LOG_DIR"/*.log 2>/dev/null | head -3); do
                local log_date
                log_date=$(basename "$logf" .log)
                local entries
                entries=$(grep -c "\[" "$logf" 2>/dev/null || echo "0")
                echo "  $log_date: $entries 条记录"
            done
            echo ""
        fi
    else
        c_info "未配置定时任务"
        echo ""
        # crontab 不可用时的替代方案
        if ! command -v crontab &>/dev/null; then
            echo "  ⚠ crontab 不可用，请使用守护进程模式:"
            echo "    $0 schedule daemon    # 启动守护进程"
            echo "    $0 schedule stop-daemon  # 停止守护进程"
            echo ""
        else
            echo "  安装定时任务: $0 schedule install"
            echo ""
        fi
    fi
}

# 守护进程模式 (crontab 不可用时的替代方案)
DAEMON_PID_FILE="$PROJECT_DIR/.auto_update_daemon.pid"
DAEMON_INTERVAL=3600  # 默认每小时检查一次

start_daemon() {
    c_title "启动守护进程"

    if [ -f "$DAEMON_PID_FILE" ]; then
        local pid
        pid=$(cat "$DAEMON_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            c_warn "守护进程已在运行 (PID=$pid)"
            return 0
        fi
        rm -f "$DAEMON_PID_FILE"
    fi

    local interval="${DAEMON_INTERVAL:-3600}"
    local interval_h=$((interval / 3600))

    c_info "守护进程将每 ${interval_h} 小时检查一次更新"

    # 启动后台循环
    nohup bash -c "
        echo \$\$ > '$DAEMON_PID_FILE'
        while true; do
            sleep $interval
            cd '$PROJECT_DIR'
            bash auto_update.sh update --silent --force >> '$LOG_DIR/daemon.log' 2>&1
        done
    " > /dev/null 2>&1 &

    sleep 1
    if [ -f "$DAEMON_PID_FILE" ]; then
        local pid
        pid=$(cat "$DAEMON_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            c_ok "守护进程已启动 (PID=$pid)"
            log_info "守护进程已启动: PID=$pid, 间隔=${interval_h}h"
            echo ""
            echo "  日志: $LOG_DIR/daemon.log"
            echo "  停止: $0 schedule stop-daemon"
            echo ""
        else
            c_fail "守护进程启动失败"
        fi
    else
        c_fail "守护进程启动失败"
    fi
}

stop_daemon() {
    c_title "停止守护进程"

    if [ ! -f "$DAEMON_PID_FILE" ]; then
        c_info "守护进程未运行"
        return 0
    fi

    local pid
    pid=$(cat "$DAEMON_PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null
        sleep 1
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
        fi
        c_ok "守护进程已停止 (PID=$pid)"
        log_info "守护进程已停止"
    else
        c_info "守护进程已不在运行"
    fi

    rm -f "$DAEMON_PID_FILE"

    # 清理可能残留的后台循环
    pkill -f "auto_update.sh.*daemon" 2>/dev/null || true
}

show_daemon_status() {
    if [ -f "$DAEMON_PID_FILE" ]; then
        local pid
        pid=$(cat "$DAEMON_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "  ${GREEN}● 守护进程运行中${NC} (PID=$pid, 间隔=${DAEMON_INTERVAL}s)"
        else
            echo -e "  ${RED}● 守护进程已停止${NC} (PID 文件残留)"
        fi
    else
        echo "  ● 未运行"
    fi
}

# ============================================================================
# 主命令处理
# ============================================================================

do_check() {
    load_config
    init_dirs
    detect_environment || true
    check_for_updates
    result=$?
    case $result in
        0) c_ok "发现可用更新" ;;
        2) c_info "当前已是最新" ;;
        *) c_fail "检查更新失败" ;;
    esac
    return $result
}

do_update() {
    local start_time
    start_time=$(date +%s)
    local backup_id=""
    local update_pkg=""

    load_config
    init_dirs

    c_title "BOM Comparison 自动更新"
    log_info "========== 自动更新开始 =========="

    # 步骤 1: 环境检测
    if ! detect_environment; then
        if [ "$FORCE_MODE" != "true" ]; then
            log_error "环境检测失败，终止更新"
            send_notification "BOM Comparison 更新失败" "环境检测未通过"
            generate_report "update" "失败" "环境检测未通过" ""
            return 1
        fi
        log_warn "环境检测存在问题，但强制继续"
    fi

    # 步骤 2: 检查更新
    update_pkg=$(check_for_updates)
    check_rc=$?
    if [ "$check_rc" -ne 0 ]; then
        if [ "$check_rc" -eq 2 ]; then
            log_info "无可用更新，退出"
            generate_report "update" "跳过" "无可用更新" ""
            return 0
        fi
        log_error "检查更新失败 (exit=$check_rc)"
        generate_report "update" "失败" "检查更新失败" ""
        return 1
    fi

    if [ "$FORCE_MODE" != "true" ] && [ "$DRY_RUN" != "true" ]; then
        if ! confirm "确认执行更新?"; then
            log_info "用户取消更新"
            return 0
        fi
    fi

    # 步骤 3: 创建备份
    backup_id=$(create_backup)
    if [ -z "$backup_id" ]; then
        log_error "备份失败，终止更新"
        generate_report "update" "失败" "备份创建失败" ""
        send_notification "BOM Comparison 更新失败" "备份创建失败"
        return 1
    fi

    # 步骤 4: 停止服务
    if ! stop_service; then
        log_error "停止服务失败"
        generate_report "update" "失败" "停止服务失败" ""
        send_notification "BOM Comparison 更新失败" "停止服务失败"
        return 1
    fi

    # 步骤 5: 应用更新
    if ! apply_update "$update_pkg"; then
        log_error "应用更新失败，触发回滚"
        rollback "$backup_id"
        local end_time
        end_time=$(date +%s)
        generate_report "update" "失败(已回滚)" "应用更新失败，已回滚到 $backup_id" "$((end_time - start_time))秒"
        send_notification "BOM Comparison 更新失败" "应用更新失败，已自动回滚到 $backup_id"
        return 1
    fi

    # 步骤 6: 启动服务
    if ! start_service; then
        log_error "启动服务失败，触发回滚"
        rollback "$backup_id"
        local end_time
        end_time=$(date +%s)
        generate_report "update" "失败(已回滚)" "启动服务失败，已回滚到 $backup_id" "$((end_time - start_time))秒"
        send_notification "BOM Comparison 更新失败" "启动服务失败，已自动回滚到 $backup_id"
        return 1
    fi

    # 步骤 7: 验证更新
    if ! verify_update; then
        log_error "验证失败，触发回滚"
        rollback "$backup_id"
        local end_time
        end_time=$(date +%s)
        generate_report "update" "失败(已回滚)" "验证未通过，已回滚到 $backup_id" "$((end_time - start_time))秒"
        send_notification "BOM Comparison 更新失败" "验证未通过，已自动回滚"
        return 1
    fi

    # 步骤 8: 生成报告
    local end_time
    end_time=$(date +%s)
    local report_file
    report_file=$(generate_report "update" "成功" "所有步骤完成" "$((end_time - start_time))秒")

    c_title "更新完成"
    c_ok "BOM Comparison 已成功更新"
    echo ""
    echo "  报告: $report_file"
    echo "  备份: $backup_id"
    echo "  耗时: $((end_time - start_time)) 秒"
    echo ""

    log_ok "========== 自动更新成功完成 =========="
    send_notification "BOM Comparison 更新成功" "更新已完成，耗时 $((end_time - start_time)) 秒"

    return 0
}

do_rollback() {
    local target="${1:-}"
    load_config
    init_dirs

    if [ -n "$target" ]; then
        c_info "回滚到指定备份: $target"
    else
        c_info "回滚到最新备份"
        list_backups
    fi

    if [ "$FORCE_MODE" != "true" ] && [ "$DRY_RUN" != "true" ]; then
        if ! confirm "确认执行回滚?"; then
            return 0
        fi
    fi

    local start_time
    start_time=$(date +%s)

    if rollback "$target"; then
        local end_time
        end_time=$(date +%s)
        generate_report "rollback" "成功" "回滚完成" "$((end_time - start_time))秒"
        return 0
    else
        local end_time
        end_time=$(date +%s)
        generate_report "rollback" "失败" "回滚失败" "$((end_time - start_time))秒"
        return 1
    fi
}

do_status() {
    load_config
    init_dirs

    c_title "系统状态"

    echo ""
    echo "  == 基本信息 =="
    echo "  项目目录: $PROJECT_DIR"
    echo "  代码目录: $CODE_DIR"
    echo "  更新源:   $UPDATE_SOURCE"
    echo "  端口:     ${PORT:-40045}"
    echo ""

    echo "  == 服务状态 =="
    if [ "$(service_status)" = "true" ]; then
        if [ -f "$PROJECT_DIR/app.pid" ]; then
            local pid
            pid=$(cat "$PROJECT_DIR/app.pid")
            echo -e "  ${GREEN}● 运行中${NC} (PID=$pid)"
        else
            echo -e "  ${GREEN}● 运行中${NC}"
        fi
    else
        echo -e "  ${RED}● 未运行${NC}"
    fi
    echo ""

    echo "  == 备份统计 =="
    local code_count db_count
    code_count=$(ls -1 "$BACKUP_DIR/code"/*.tar.gz 2>/dev/null | wc -l)
    db_count=$(ls -1 "$BACKUP_DIR/db"/*.db.bak 2>/dev/null | wc -l)
    echo "  代码备份: $code_count 个 (保留最多 ${BACKUP_MAX_COUNT} 个)"
    echo "  数据库备份: $db_count 个"
    echo ""

    echo "  == 待处理更新包 =="
    if ls "$PACKAGE_DIR"/*.tar.gz &>/dev/null 2>&1; then
        for p in "$PACKAGE_DIR"/*.tar.gz; do
            echo "  - $(basename "$p") ($(du -h "$p" | cut -f1))"
        done
    else
        echo "  (无)"
    fi
    echo ""

    echo "  == 磁盘使用 =="
    df -h "$PROJECT_DIR" 2>/dev/null | tail -1 | awk '{print "  " $4 " 可用 / " $2 " 总量 (" $5 " 已用)"}'
    echo ""

    echo "  == 最近日志 =="
    if [ -f "$LOG_DIR/${DATE_STR}.log" ]; then
        tail -5 "$LOG_DIR/${DATE_STR}.log" | while read -r line; do
            echo "  $line"
        done
    else
        echo "  (今日无日志)"
    fi
    echo ""
}

do_backup() {
    load_config
    init_dirs

    c_title "手动备份"

    local backup_id
    backup_id=$(create_backup)

    if [ -n "$backup_id" ]; then
        c_ok "备份完成: $backup_id"
        generate_report "backup" "成功" "手动备份: $backup_id" ""
        list_backups
    else
        c_fail "备份失败"
    fi
}

show_help() {
    cat << 'HELP_EOF'

╔══════════════════════════════════════════════════════════════╗
║        BOM Comparison — 自动更新系统 v1.0                    ║
╚══════════════════════════════════════════════════════════════╝

用法: ./auto_update.sh <命令> [选项]

命令:
  update              执行完整更新流程（检测→备份→更新→验证）
  check               检查是否有可用更新（不执行实际更新）
  rollback [备份ID]   回滚到指定备份（默认回滚到最新备份）
  status              查看系统状态、备份、运行情况
  backup              仅创建备份（不执行更新）
  schedule [子命令]   管理定时任务
    install           安装每日自动更新 cron 任务
    remove            移除定时任务
    status            查看定时任务状态
    daemon            启动守护进程模式 (crontab 不可用时使用)
    stop-daemon       停止守护进程
  help                显示此帮助信息

选项:
  --silent            静默模式（仅输出错误，适用于 cron）
  --force             跳过确认提示
  --dry-run           模拟执行，不做实际更改

示例:
  # 检查更新
  ./auto_update.sh check

  # 执行更新
  ./auto_update.sh update

  # 强制更新（跳过确认）
  ./auto_update.sh update --force

  # 回滚到上一个版本
  ./auto_update.sh rollback

  # 安装每日 3:00 AM 自动更新
  ./auto_update.sh schedule install

  # 手动创建备份
  ./auto_update.sh backup

工作流程:
  环境检测 → 创建备份 → 停止服务 → 应用更新 → 启动服务 → 验证(7项) → 失败回滚

更新方式:
  package  将 tar.gz 包放入 packages/ 目录，自动检测并安装
  git      配置 Git 仓库后，自动拉取最新代码
  http     配置 UPDATE_URL 后，自动下载更新包

配置:
  编辑 update_config.conf 文件设置更新策略、备份保留数量等

日志:
  操作日志: logs/auto_update/YYYY-MM-DD.log
  更新报告: logs/auto_update/report-YYYYMMDD-HHMMSS.md
  备份目录: backups/

HELP_EOF
}

# ============================================================================
# 入口
# ============================================================================

main() {
    # 解析全局选项
    local args=()
    while [ $# -gt 0 ]; do
        case "$1" in
            --silent) SILENT_MODE="true"; shift ;;
            --force)  FORCE_MODE="true"; shift ;;
            --dry-run) DRY_RUN="true"; shift ;;
            *) args+=("$1"); shift ;;
        esac
    done

    local cmd="${args[0]:-help}"
    local sub="${args[1]:-}"

    case "$cmd" in
        check)
            do_check
            ;;
        update)
            do_update
            ;;
        rollback)
            do_rollback "$sub"
            ;;
        status)
            do_status
            ;;
        backup)
            do_backup
            ;;
        schedule)
            do_schedule "$sub"
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            echo "未知命令: $cmd"
            echo "使用 '$0 help' 查看帮助"
            exit 1
            ;;
    esac
}

main "$@"
