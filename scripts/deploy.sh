#!/bin/bash
# ============================================================================
# BOM Comparison — 一键部署脚本
# 功能: stop → clean cache → deploy → start → health check → (auto-rollback)
# 用法: bash deploy.sh <source_dir|tar.gz> [--force] [--no-rollback]
#
# 示例:
#   bash deploy.sh ~/bom-update.tar.gz          # 从 tar.gz 包部署
#   bash deploy.sh ~/new-code/                  # 从目录部署
#   bash deploy.sh ~/update.tar.gz --force      # 跳过确认+强制清除缓存
#   bash deploy.sh ~/update.tar.gz --no-rollback # 健康检查失败时不回滚
# ============================================================================
set -e

# ─────────────────────────────────────────────────────
# 常量 & 颜色
# ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 自动检测项目根目录 (支持两种布局)
# 布局A: wsgi.py 在项目根目录
# 布局B: wsgi.py 在 code/ 子目录 (服务器标准布局)
if [ -f "$SCRIPT_DIR/wsgi.py" ]; then
    PROJECT_DIR="$SCRIPT_DIR"
elif [ -f "$SCRIPT_DIR/code/wsgi.py" ]; then
    PROJECT_DIR="$SCRIPT_DIR"
elif [ -f "$SCRIPT_DIR/../wsgi.py" ]; then
    PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
elif [ -f "$SCRIPT_DIR/../code/wsgi.py" ]; then
    PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
else
    echo "错误: 无法定位项目根目录（需要 wsgi.py 或 code/wsgi.py）"
    echo "当前目录: $SCRIPT_DIR"
    echo "请将此脚本放在项目根目录或 scripts/ 子目录下"
    exit 1
fi
CODE_DIR="$PROJECT_DIR/code"
LOGS_DIR="$PROJECT_DIR/logs"
BACKUP_DIR="$PROJECT_DIR/backups"
TIMESTAMP="$(date '+%Y%m%d-%H%M%S')"
DEPLOY_LOG="$LOGS_DIR/deploy-${TIMESTAMP}.log"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[DEPLOY]${NC} $*" | tee -a "$DEPLOY_LOG"; }
log_warn()  { echo -e "${YELLOW}[DEPLOY]${NC} $*" | tee -a "$DEPLOY_LOG"; }
log_error() { echo -e "${RED}[DEPLOY]${NC} $*" | tee -a "$DEPLOY_LOG"; }
log_step()  { echo -e "${BLUE}${BOLD}[步骤 $1/$TOTAL_STEPS]${NC} $2" | tee -a "$DEPLOY_LOG"; }

# ─────────────────────────────────────────────────────
# 参数解析
# ─────────────────────────────────────────────────────
FORCE_MODE="false"
NO_ROLLBACK="false"
SOURCE=""

for arg in "$@"; do
    case "$arg" in
        --force)        FORCE_MODE="true" ;;
        --no-rollback)  NO_ROLLBACK="true" ;;
        -h|--help)
            echo "用法: bash deploy.sh <source> [--force] [--no-rollback]"
            echo "  source       部署源: tar.gz 包路径 或 代码目录路径"
            echo "  --force       跳过确认，强制清除缓存后部署"
            echo "  --no-rollback 健康检查失败时不自动回滚"
            exit 0
            ;;
        *) SOURCE="$arg" ;;
    esac
done

TOTAL_STEPS=7

# ─────────────────────────────────────────────────────
# 初始化
# ─────────────────────────────────────────────────────
init() {
    mkdir -p "$LOGS_DIR" "$BACKUP_DIR"
    echo "" > "$DEPLOY_LOG"
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  BOM Comparison 一键部署脚本                            ║"
    echo "║  时间: $(date '+%Y-%m-%d %H:%M:%S')                      ║"
    echo "║  日志: $DEPLOY_LOG"
    echo "╚══════════════════════════════════════════════════════════╝"
    echo ""

    if [ -z "$SOURCE" ]; then
        log_error "缺少部署源参数！"
        echo "用法: bash deploy.sh <source.tar.gz|source_dir> [--force]"
        exit 1
    fi

    if [ ! -e "$SOURCE" ]; then
        log_error "部署源不存在: $SOURCE"
        exit 1
    fi

    if [ "$FORCE_MODE" = "true" ]; then
        log_warn "⚠️  --force 模式已启用：跳过确认 + 强制清除缓存"
    fi
}

# ─────────────────────────────────────────────────────
# 步骤 1: 部署前确认
# ─────────────────────────────────────────────────────
step1_confirm() {
    log_step 1 "部署前确认"

    echo ""
    echo "  部署源:     $SOURCE"
    echo "  目标目录:   $CODE_DIR"
    echo "  部署时间:   $TIMESTAMP"
    echo "  强制模式:   $FORCE_MODE"
    echo "  自动回滚:   $([ "$NO_ROLLBACK" = "true" ] && echo '否' || echo '是')"
    echo ""

    if [ "$FORCE_MODE" != "true" ]; then
        read -r -p "  确认部署? [y/N] " response
        case "$response" in
            [yY][eE][sS]|[yY]) ;;
            *) log_warn "已取消部署"; exit 0 ;;
        esac
    fi
}

# ─────────────────────────────────────────────────────
# 步骤 2: 创建备份
# ─────────────────────────────────────────────────────
step2_backup() {
    log_step 2 "创建部署前备份"

    local backup_name="pre-deploy-${TIMESTAMP}"
    local backup_path="$BACKUP_DIR/$backup_name"

    mkdir -p "$backup_path/code" "$backup_path/db"

    # 备份代码（排除 venv、__pycache__、logs）
    if [ -d "$CODE_DIR" ]; then
        rsync -a --exclude='venv' --exclude='__pycache__' --exclude='*.pyc' \
            --exclude='logs' --exclude='*.log' --exclude='backups' \
            "$CODE_DIR/" "$backup_path/code/" 2>/dev/null || true
        log_info "代码备份: $backup_path/code/"
    fi

    # 备份数据库
    local db_file="$CODE_DIR/data/bom_compare.db"
    if [ -f "$db_file" ]; then
        cp "$db_file" "$backup_path/db/bom_compare.db"
        log_info "数据库备份: $backup_path/db/bom_compare.db ($(stat -c%s "$db_file") bytes)"
    fi

    # 记录备份到日志
    echo "$TIMESTAMP|$backup_path|$(du -sh "$backup_path" 2>/dev/null | cut -f1)" >> "$BACKUP_DIR/backup_manifest.log"

    # 清理旧备份（保留最近 10 个）
    local backup_count
    backup_count=$(ls -d "$BACKUP_DIR"/pre-deploy-* 2>/dev/null | wc -l)
    if [ "$backup_count" -gt 10 ]; then
        ls -dt "$BACKUP_DIR"/pre-deploy-* 2>/dev/null | tail -n +11 | while read -r old; do
            rm -rf "$old"
            log_info "清理旧备份: $(basename "$old")"
        done
    fi

    # 保存备份路径供回滚使用
    echo "$backup_path" > "/tmp/deploy_last_backup"
    log_info "备份完成 (保留最近10个)"
}

# ─────────────────────────────────────────────────────
# 步骤 3: 停止服务 + 清除缓存
# ─────────────────────────────────────────────────────
step3_stop_and_clean() {
    log_step 3 "停止服务 + 清除 __pycache__"

    # 停止服务
    if [ -f "$PROJECT_DIR/stop.sh" ]; then
        bash "$PROJECT_DIR/stop.sh"
        log_info "服务已停止"
    else
        # 兼容旧版：手动停止
        if [ -f "$PROJECT_DIR/app.pid" ]; then
            kill "$(cat "$PROJECT_DIR/app.pid")" 2>/dev/null || true
        fi
        pkill -f "gunicorn.*40045" 2>/dev/null || true
        sleep 2
    fi

    # 强制清除所有缓存
    log_info "深度清除 Python 缓存..."
    local pycache_count=0
    local pyc_count=0

    if [ -d "$CODE_DIR/app" ]; then
        find "$CODE_DIR/app" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null && pycache_count=1 || true
        find "$CODE_DIR/app" -type f -name '*.pyc' -delete 2>/dev/null && pyc_count=1 || true
    fi
    find "$PROJECT_DIR" -maxdepth 2 -type d -name '__pycache__' \
        -not -path "$CODE_DIR/app/*" -exec rm -rf {} + 2>/dev/null || true
    find "$PROJECT_DIR" -maxdepth 2 -type f -name '*.pyc' -delete 2>/dev/null || true

    # 验证清除结果
    local remaining
    remaining=$(find "$CODE_DIR/app" -type d -name '__pycache__' 2>/dev/null | wc -l)
    if [ "$remaining" -gt 0 ]; then
        log_error "⚠️ 缓存清除不彻底！残留 $remaining 个 __pycache__ 目录"
        if [ "$FORCE_MODE" != "true" ]; then
            exit 1
        fi
    else
        log_info "缓存清除完毕 — 零残留"
    fi
}

# ─────────────────────────────────────────────────────
# 步骤 4: 部署代码
# ─────────────────────────────────────────────────────
step4_deploy_code() {
    log_step 4 "部署代码"

    if [ -f "$SOURCE" ] && [[ "$SOURCE" =~ \.tar\.gz$|\.tgz$ ]]; then
        # tar.gz 包部署
        log_info "从压缩包部署: $SOURCE"
        local extract_dir="/tmp/bom-deploy-${TIMESTAMP}"
        mkdir -p "$extract_dir"
        tar -xzf "$SOURCE" -C "$extract_dir"

        # 查找实际的代码目录
        local src_code=""
        if [ -d "$extract_dir/code" ]; then
            src_code="$extract_dir/code"
        elif [ -d "$extract_dir/app" ]; then
            src_code="$extract_dir"
        else
            src_code=$(find "$extract_dir" -name 'wsgi.py' -maxdepth 2 -printf '%h\n' -quit 2>/dev/null || echo "")
        fi

        if [ -z "$src_code" ]; then
            log_error "无法在压缩包中找到代码目录（需要 wsgi.py 或 app/ 目录）"
            rm -rf "$extract_dir"
            exit 1
        fi

        # rsync 到目标（排除 venv、__pycache__、.git）
        rsync -a --delete \
            --exclude='venv' --exclude='__pycache__' --exclude='*.pyc' \
            --exclude='.git' --exclude='*.db' --exclude='*.db-journal' \
            --exclude='logs' --exclude='*.log' --exclude='backups' \
            --exclude='uploads' --exclude='exports' --exclude='reports' \
            "$src_code/" "$CODE_DIR/"
        rm -rf "$extract_dir"
        log_info "代码已从压缩包同步到 $CODE_DIR"

    elif [ -d "$SOURCE" ]; then
        # 目录部署
        log_info "从目录部署: $SOURCE"

        # 检测源目录结构
        local src_code="$SOURCE"
        if [ -f "$SOURCE/wsgi.py" ]; then
            src_code="$SOURCE"
        elif [ -f "$SOURCE/code/wsgi.py" ]; then
            src_code="$SOURCE/code"
        else
            log_error "源目录结构不识别（需要 wsgi.py 或 code/wsgi.py）"
            exit 1
        fi

        rsync -a --delete \
            --exclude='venv' --exclude='__pycache__' --exclude='*.pyc' \
            --exclude='.git' --exclude='*.db' --exclude='*.db-journal' \
            --exclude='logs' --exclude='*.log' --exclude='backups' \
            --exclude='uploads' --exclude='exports' --exclude='reports' \
            "$src_code/" "$CODE_DIR/"
        log_info "代码已从目录同步到 $CODE_DIR"
    else
        log_error "不支持的部署源类型: $SOURCE"
        exit 1
    fi

    # 再次确认无 __pycache__ 被带入
    local brought_in
    brought_in=$(find "$CODE_DIR/app" -type d -name '__pycache__' 2>/dev/null | wc -l)
    if [ "$brought_in" -gt 0 ]; then
        log_warn "部署源包含 $brought_in 个 __pycache__ 目录，正在清除..."
        find "$CODE_DIR/app" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null
    fi

    log_info "代码部署完成"
}

# ─────────────────────────────────────────────────────
# 步骤 5: 检查依赖
# ─────────────────────────────────────────────────────
step5_check_deps() {
    log_step 5 "检查 Python 依赖"

    cd "$CODE_DIR"

    # 检查虚拟环境
    if [ ! -f "venv/bin/python3" ]; then
        log_warn "虚拟环境不存在，正在创建..."
        python3 -m venv venv
        source venv/bin/activate
        pip install --upgrade pip -q
        pip install -r requirements.txt -q
        pip install gunicorn -q
    else
        source venv/bin/activate
        # 确保关键依赖已安装
        pip install -r requirements.txt -q 2>/dev/null || log_warn "部分依赖安装可能有问题"
    fi

    log_info "Python: $(python3 --version)"
    log_info "依赖检查完成"
}

# ─────────────────────────────────────────────────────
# 步骤 6: 启动服务
# ─────────────────────────────────────────────────────
step6_start() {
    log_step 6 "启动服务"

    if [ -f "$PROJECT_DIR/run.sh" ]; then
        bash "$PROJECT_DIR/run.sh" --force --skip-cache-check
    else
        log_error "run.sh 不存在，无法启动！"
        exit 1
    fi

    # 等待服务稳定
    sleep 3
}

# ─────────────────────────────────────────────────────
# 步骤 7: 健康检查
# ─────────────────────────────────────────────────────
step7_health_check() {
    log_step 7 "健康检查"

    local port="${PORT:-40045}"
    local errors=0

    # 7.1 HTTP 可达性
    log_info "检查 HTTP 端点..."
    local http_code
    http_code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$port/compare/" 2>/dev/null || echo "000")

    if [ "$http_code" != "200" ]; then
        log_error "HTTP 检查失败！响应码: $http_code"
        errors=$((errors + 1))
    else
        log_info "HTTP 200 OK"
    fi

    # 7.2 缓存残留检查
    log_info "检查 __pycache__ 残留..."
    local cache_found
    cache_found=$(find "$CODE_DIR/app" -type d -name '__pycache__' 2>/dev/null | wc -l)
    if [ "$cache_found" -gt 0 ]; then
        log_error "发现 $cache_found 个 __pycache__ 目录（PYTHONDONTWRITEBYTECODE 可能未生效）"
        errors=$((errors + 1))
    else
        log_info "零缓存残留 ✓"
    fi

    # 7.3 模块版本验证（对比关键文件时间戳）
    log_info "验证模块版本..."
    local deploy_time
    deploy_time=$(stat -c %Y "$DEPLOY_LOG" 2>/dev/null || stat -c %Y "$CODE_DIR/wsgi.py")

    # 检查核心模块修改时间是否在部署之后
    for module in wsgi.py app/__init__.py app/services/differ.py app/routes/compare.py app/templates/compare.html; do
        local mod_path="$CODE_DIR/$module"
        if [ -f "$mod_path" ]; then
            local mod_time
            mod_time=$(stat -c %Y "$mod_path" 2>/dev/null || echo "0")
            # 允许 60 秒的误差（部署本身需要时间）
            if [ "$mod_time" -lt $((deploy_time - 60)) ]; then
                log_warn "模块 $module 修改时间早于部署时间（可能未被更新）"
            fi
        fi
    done
    log_info "模块时间戳验证完成"

    # 7.4 进程检查
    log_info "检查进程..."
    if [ -f "$PROJECT_DIR/app.pid" ]; then
        local pid
        pid=$(cat "$PROJECT_DIR/app.pid")
        if kill -0 "$pid" 2>/dev/null; then
            log_info "进程 PID=$pid 运行中 ✓"
        else
            log_error "PID 文件存在但进程未运行！"
            errors=$((errors + 1))
        fi
    fi

    # ── 结果判断 ──
    echo ""
    if [ $errors -eq 0 ]; then
        echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
        echo -e "${GREEN}${BOLD}║  ✅ 部署成功！所有健康检查通过。                       ║${NC}"
        echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
        echo ""
        log_info "访问地址: http://localhost:$port/compare/"
        log_info "部署日志: $DEPLOY_LOG"
        return 0
    else
        echo -e "${RED}${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
        echo -e "${RED}${BOLD}║  ❌ 健康检查失败！发现 ${errors} 个错误。                  ║${NC}"
        echo -e "${RED}${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
        echo ""

        if [ "$NO_ROLLBACK" != "true" ]; then
            log_warn "正在自动回滚..."
            do_rollback
        else
            log_error "--no-rollback 模式，不执行回滚。请手动检查。"
        fi
        return 1
    fi
}

# ─────────────────────────────────────────────────────
# 自动回滚
# ─────────────────────────────────────────────────────
do_rollback() {
    log_info "=== 开始自动回滚 ==="

    local backup_path
    backup_path=$(cat /tmp/deploy_last_backup 2>/dev/null || echo "")

    if [ -z "$backup_path" ] || [ ! -d "$backup_path" ]; then
        log_error "未找到有效的备份，无法回滚！"
        return 1
    fi

    # 停止服务
    if [ -f "$PROJECT_DIR/stop.sh" ]; then
        bash "$PROJECT_DIR/stop.sh" --no-clean
    else
        pkill -f "gunicorn.*40045" 2>/dev/null || true
        sleep 2
    fi

    # 恢复代码
    if [ -d "$backup_path/code" ]; then
        rsync -a --delete \
            --exclude='venv' --exclude='__pycache__' --exclude='*.pyc' \
            "$backup_path/code/" "$CODE_DIR/"
        log_info "代码已回滚到备份: $backup_path"
    fi

    # 恢复数据库（如果部署中没有被修改通常不需要，但保留作为安全措施）
    # if [ -f "$backup_path/db/bom_compare.db" ]; then
    #     cp "$backup_path/db/bom_compare.db" "$CODE_DIR/data/bom_compare.db"
    #     log_info "数据库已回滚"
    # fi

    # 清除所有缓存
    find "$CODE_DIR/app" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
    find "$CODE_DIR/app" -type f -name '*.pyc' -delete 2>/dev/null || true

    # 重新启动
    if [ -f "$PROJECT_DIR/run.sh" ]; then
        bash "$PROJECT_DIR/run.sh" --force --skip-cache-check
    fi

    log_info "=== 回滚完成 ==="
    log_info "服务已恢复到部署前版本"
}

# ─────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────
main() {
    init
    step1_confirm
    step2_backup
    step3_stop_and_clean
    step4_deploy_code
    step5_check_deps
    step6_start

    if ! step7_health_check; then
        log_error "部署最终状态: 失败（已回滚）"
        exit 1
    fi

    log_info "部署最终状态: 成功"
    log_info "日志文件: $DEPLOY_LOG"
}

main
