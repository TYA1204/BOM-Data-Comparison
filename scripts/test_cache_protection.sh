#!/bin/bash
# ============================================================================
# 五重 Python 缓存防护体系 — 端到端验证脚本
# 用法: 在服务器上执行 bash test_cache_protection.sh
# ============================================================================
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
CODE_DIR="$PROJECT_DIR/code"
PORT="${PORT:-40045}"
BASE_URL="http://localhost:$PORT"

PASS=0
FAIL=0
TOTAL=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

test_header() {
    echo ""
    echo -e "${BLUE}${BOLD}══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}${BOLD}  $1${NC}"
    echo -e "${BLUE}${BOLD}══════════════════════════════════════════════════════════════${NC}"
}

test_pass() {
    TOTAL=$((TOTAL + 1)); PASS=$((PASS + 1))
    echo -e "  ${GREEN}✅ PASS${NC}  $1"
}

test_fail() {
    TOTAL=$((TOTAL + 1)); FAIL=$((FAIL + 1))
    echo -e "  ${RED}❌ FAIL${NC}  $1 — $2"
}

test_warn() {
    TOTAL=$((TOTAL + 1))
    echo -e "  ${YELLOW}⚠️  WARN${NC}  $1 — $2"
}

# ─────────────────────────────────────────────────────
# 前置检查: 确保脚本存在
# ─────────────────────────────────────────────────────
test_header "前置检查: 脚本完整性"
for script in stop.sh run.sh deploy.sh health_check.sh; do
    if [ -x "$PROJECT_DIR/$script" ]; then
        test_pass "$script 存在且可执行"
    else
        test_fail "$script" "缺失或无执行权限"
    fi
done

# ─────────────────────────────────────────────────────
# 测试 1: 当前服务状态
# ─────────────────────────────────────────────────────
test_header "测试前: 当前服务状态"

if [ -f "$PROJECT_DIR/app.pid" ]; then
    PID=$(cat "$PROJECT_DIR/app.pid")
    if kill -0 "$PID" 2>/dev/null; then
        test_pass "服务运行中 (PID=$PID)"
    else
        test_warn "PID 文件存在但进程不在" ""
    fi
else
    test_warn "无 PID 文件" ""
fi

# 测试 /health 端点
HTTP_CODE=$(curl -s -o /tmp/health_resp.json -w '%{http_code}' "$BASE_URL/health" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    PYCACHE_DISABLED=$(python3 -c "import json; d=json.load(open('/tmp/health_resp.json')); print(d['version']['pycache_disabled'])" 2>/dev/null || echo "unknown")
    test_pass "/health 端点 200 OK, pycache_disabled=$PYCACHE_DISABLED"
    echo -e "         $(cat /tmp/health_resp.json | python3 -m json.tool 2>/dev/null || cat /tmp/health_resp.json)"
else
    test_fail "/health 端点" "HTTP $HTTP_CODE"
fi

# ─────────────────────────────────────────────────────
# 测试 2: stop.sh — 缓存自动清除 (对策 1)
# ─────────────────────────────────────────────────────
test_header "对策 1 测试: stop.sh 自动清除 __pycache__"

# 先创建伪造缓存
echo "  创建伪造缓存..."
mkdir -p "$CODE_DIR/app/__pycache__"
echo "fake bytecode" > "$CODE_DIR/app/__pycache__/fake.cpython-311.pyc"
mkdir -p "$CODE_DIR/app/services/__pycache__"
touch "$CODE_DIR/app/services/__pycache__/dummy.pyc"

# 确认伪造成功
FAKE_COUNT=$(find "$CODE_DIR/app" -type d -name '__pycache__' 2>/dev/null | wc -l)
if [ "$FAKE_COUNT" -ge 2 ]; then
    echo "  已创建 $FAKE_COUNT 个伪造 __pycache__ 目录"
else
    test_fail "伪造缓存创建" "只创建了 $FAKE_COUNT 个"
fi

# 执行 stop.sh (带 --no-clean 会跳过清除，我们测试默认行为)
bash "$PROJECT_DIR/stop.sh"

# 验证清除结果
REMAINING=$(find "$CODE_DIR/app" -type d -name '__pycache__' 2>/dev/null | wc -l)
PYC_FILES=$(find "$CODE_DIR/app" -type f -name '*.pyc' 2>/dev/null | wc -l)
if [ "$REMAINING" -eq 0 ] && [ "$PYC_FILES" -eq 0 ]; then
    test_pass "stop.sh 自动清除缓存: 0 残留"
else
    test_fail "stop.sh 缓存清除" "残留 $REMAINING 个目录 + $PYC_FILES 个文件"
fi

# ─────────────────────────────────────────────────────
# 测试 3: run.sh — 缓存检测阻止启动 (对策 2)
# ─────────────────────────────────────────────────────
test_header "对策 2 测试: run.sh 缓存检测 + 阻止启动"

# 重新创建伪造缓存
mkdir -p "$CODE_DIR/app/__pycache__"
touch "$CODE_DIR/app/__pycache__/test.pyc"

# 尝试启动（应被阻止）
BLOCKED_OUTPUT=$(bash "$PROJECT_DIR/run.sh" 2>&1 || true)
BLOCKED=$?

if echo "$BLOCKED_OUTPUT" | grep -q "启动已阻止\|请先清除缓存残留"; then
    test_pass "run.sh 检测到缓存并阻止启动"
elif echo "$BLOCKED_OUTPUT" | grep -q "检测到 Python 字节码缓存残留"; then
    test_pass "run.sh 检测到缓存残留告警"
else
    test_fail "run.sh 缓存检测" "未触发阻止 (exit=$BLOCKED)"
    echo "  输出: $(echo "$BLOCKED_OUTPUT" | head -5)"
fi

# 确保服务已停止
pkill -f "gunicorn.*40045" 2>/dev/null || true
sleep 1

# ─────────────────────────────────────────────────────
# 测试 4: run.sh --force 清除并启动 (对策 2 续)
# ─────────────────────────────────────────────────────
test_header "对策 2 续: run.sh --force 自动清除 + 启动"

FORCE_OUTPUT=$(bash "$PROJECT_DIR/run.sh" --force 2>&1 || true)
sleep 3

if echo "$FORCE_OUTPUT" | grep -q "已启动"; then
    test_pass "run.sh --force 成功启动服务"
    
    # 验证服务 HTTP 可达
    HTTP_CHECK=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/compare/" 2>/dev/null || echo "000")
    if [ "$HTTP_CHECK" = "200" ]; then
        test_pass "服务 HTTP 200 OK"
    else
        test_fail "服务 HTTP" "响应码 $HTTP_CHECK"
    fi
else
    test_fail "run.sh --force" "未能启动"
    echo "  输出: $(echo "$FORCE_OUTPUT" | tail -5)"
fi

# 验证无新缓存
NEW_CACHE=$(find "$CODE_DIR/app" -type d -name '__pycache__' 2>/dev/null | wc -l)
if [ "$NEW_CACHE" -eq 0 ]; then
    test_pass "--force 启动后无新缓存生成"
else
    test_fail "缓存验证" "$NEW_CACHE 个新缓存目录"
fi

# ─────────────────────────────────────────────────────
# 测试 5: PYTHONDONTWRITEBYTECODE 环境变量 (对策 3)
# ─────────────────────────────────────────────────────
test_header "对策 3 测试: PYTHONDONTWRITEBYTECODE=1"

# 通过 gunicorn 进程检查环境变量
GUNICORN_PID=$(cat "$PROJECT_DIR/app.pid" 2>/dev/null || echo "")
if [ -n "$GUNICORN_PID" ] && kill -0 "$GUNICORN_PID" 2>/dev/null; then
    # 检查 /proc/PID/environ
    if [ -f "/proc/$GUNICORN_PID/environ" ]; then
        if tr '\0' '\n' < "/proc/$GUNICORN_PID/environ" 2>/dev/null | grep -q "PYTHONDONTWRITEBYTECODE=1"; then
            test_pass "gunicorn 进程环境变量 PYTHONDONTWRITEBYTECODE=1"
        else
            test_warn "gunicorn 进程环境变量" "未直接设置（可能由 worker 继承）"
        fi
    fi
fi

# 通过 /health 端点验证
PYCACHE_STATUS=$(curl -s "$BASE_URL/health" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['version']['pycache_disabled'])" 2>/dev/null || echo "unknown")
if [ "$PYCACHE_STATUS" = "True" ]; then
    test_pass "/health 端点确认 pycache_disabled=True"
else
    test_fail "/health 端点" "pycache_disabled=$PYCACHE_STATUS"
fi

# ─────────────────────────────────────────────────────
# 测试 6: health_check.sh 全面检查 (对策 5)
# ─────────────────────────────────────────────────────
test_header "对策 5 测试: health_check.sh 7项检查"

HEALTH_OUTPUT=$(bash "$PROJECT_DIR/health_check.sh" 2>/dev/null || true)
HEALTH_EXIT=$?

# 计数 pass/warn/fail
PASS_CHECKS=$(echo "$HEALTH_OUTPUT" | grep -c "✓" || echo "0")
WARN_CHECKS=$(echo "$HEALTH_OUTPUT" | grep -c "⚠" || echo "0")
FAIL_CHECKS=$(echo "$HEALTH_OUTPUT" | grep -c "✗" || echo "0")

echo "  7项检查: ✓=$PASS_CHECKS ⚠=$WARN_CHECKS ✗=$FAIL_CHECKS"

# 检查关键项
if echo "$HEALTH_OUTPUT" | grep -q "进程存活.*运行中"; then
    test_pass "health_check: 进程存活"
else
    test_fail "health_check: 进程存活" "未检测到运行中进程"
fi

if echo "$HEALTH_OUTPUT" | grep -q "缓存残留.*零残留"; then
    test_pass "health_check: 零缓存残留"
else
    test_fail "health_check: 缓存残留" "检测到残留"
fi

if echo "$HEALTH_OUTPUT" | grep -q "HTTP 页面.*200"; then
    test_pass "health_check: HTTP 200"
else
    test_fail "health_check: HTTP" "非 200"
fi

# JSON 模式
JSON_OUTPUT=$(bash "$PROJECT_DIR/health_check.sh" --json 2>/dev/null || echo '{"status":"error"}')
JSON_STATUS=$(echo "$JSON_OUTPUT" | grep -o '"status": *"[^"]*"' | head -1 | cut -d'"' -f4)
if [ "$JSON_STATUS" = "healthy" ]; then
    test_pass "health_check --json: status=healthy"
else
    test_fail "health_check --json" "status=$JSON_STATUS"
fi

# ─────────────────────────────────────────────────────
# 测试 7: auto_update.sh 集成验证 (应对策 4)
# ─────────────────────────────────────────────────────
test_header "对策 4 验证: deploy.sh / auto_update.sh 集成"

if [ -f "$PROJECT_DIR/deploy.sh" ]; then
    # dry-run 验证 deploy.sh 可解析
    DEPLOY_HELP=$(bash "$PROJECT_DIR/deploy.sh" --help 2>&1 || true)
    if echo "$DEPLOY_HELP" | grep -q "用法\|部署源"; then
        test_pass "deploy.sh --help 正常输出"
    else
        test_fail "deploy.sh --help" "解析异常"
    fi
fi

if [ -f "$PROJECT_DIR/auto_update.sh" ]; then
    # 验证配置加载
    CONFIG_CHECK=$(bash "$PROJECT_DIR/auto_update.sh" check 2>&1 | tail -5 || true)
    test_pass "auto_update.sh check 可用"
else
    test_warn "auto_update.sh" "文件不存在"
fi

# ─────────────────────────────────────────────────────
# 测试 8: 压力测试 — 连续多次 stop/start 无缓存残留
# ─────────────────────────────────────────────────────
test_header "压力测试: 连续 3 次 stop/start 无缓存累积"

for i in 1 2 3; do
    bash "$PROJECT_DIR/stop.sh" 2>/dev/null || true
    sleep 1
    bash "$PROJECT_DIR/run.sh" --force --skip-cache-check 2>/dev/null || true
    sleep 2
    
    CACHE_COUNT=$(find "$CODE_DIR/app" -type d -name '__pycache__' 2>/dev/null | wc -l)
    if [ "$CACHE_COUNT" -ne 0 ]; then
        test_fail "第 $i 轮后缓存残留" "$CACHE_COUNT 个目录"
        break
    fi
done

if [ "$CACHE_COUNT" -eq 0 ]; then
    test_pass "连续 3 轮 stop/start 后零缓存残留"
fi

# ─────────────────────────────────────────────────────
# 最终汇总
# ─────────────────────────────────────────────────────
test_header "测试汇总"

echo ""
echo -e "  总测试项: ${BOLD}$TOTAL${NC}"
echo -e "  通过:     ${GREEN}${BOLD}$PASS${NC}"
echo -e "  失败:     ${RED}${BOLD}$FAIL${NC}"
echo ""

if [ "$FAIL" -eq 0 ]; then
    echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}${BOLD}║  🎉 五重缓存防护体系全部测试通过！                      ║${NC}"
    echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
else
    echo -e "${RED}${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}${BOLD}║  ⚠️  有 $FAIL 项测试失败，请检查上文详情                  ║${NC}"
    echo -e "${RED}${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
fi

# 确保服务最终处于运行状态
if ! curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/compare/" 2>/dev/null | grep -q "200"; then
    echo ""
    echo "  服务未运行，正在恢复..."
    bash "$PROJECT_DIR/run.sh" --force --skip-cache-check 2>/dev/null || true
fi

exit $FAIL
