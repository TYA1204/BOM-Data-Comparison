#!/bin/bash
# ============================================================
#  BOM Data Comparison — 服务器迭代更新脚本
#  用法: sudo bash update.sh
# ============================================================
set -e

APP_DIR="/opt/bom-comparison"
SERVICE_NAME="bom-comparison"
GIT_REMOTE="origin"
GIT_BRANCH="main"

echo "============================================"
echo "  BOM Data Comparison 迭代更新"
echo "============================================"
echo ""

cd "${APP_DIR}"

# ── Step 1: 拉取最新代码 ──
echo "[1/4] 拉取最新代码..."

if [ -d ".git" ]; then
    # 先 stash 本地改动（如 .env 等），拉取后再恢复
    git stash --include-untracked -m "auto-stash before update" 2>/dev/null || true
    git pull ${GIT_REMOTE} ${GIT_BRANCH}
    git stash pop 2>/dev/null || true
    echo "  ✅ 代码已更新到最新版本"
else
    echo "  ❌ 未检测到 Git 仓库"
    echo ""
    echo "  首次设置 Git 跟踪（仅需执行一次）:"
    echo "    cd ${APP_DIR}"
    echo "    git init"
    echo "    git remote add origin https://github.com/TYA1204/BOM-Data-Comparison.git"
    echo "    git fetch origin"
    echo "    git checkout -b main origin/main"
    echo ""
    echo "  或者手动上传新压缩包覆盖文件后运行:"
    echo "    sudo bash update.sh --skip-git"
    echo ""
    if [ "$1" = "--skip-git" ]; then
        echo "  ⚠️ --skip-git 模式：跳过 Git，仅检查依赖并重启服务"
    else
        exit 1
    fi
fi

# ── Step 2: 检查并安装新依赖 ──
echo "[2/4] 检查依赖更新..."
if [ -f "requirements.txt" ]; then
    sudo -u www-data venv/bin/pip install -r requirements.txt -q
    echo "  ✅ 依赖已同步"
else
    echo "  ⚠️ requirements.txt 不存在，跳过"
fi

# ── Step 3: 重启服务 ──
echo "[3/4] 重启服务..."
sudo systemctl restart ${SERVICE_NAME}

# ── Step 4: 验证状态 ──
echo "[4/4] 验证服务状态..."
sleep 2

# 检查 systemd 状态
if systemctl is-active --quiet ${SERVICE_NAME}; then
    echo "  ✅ 服务运行中"
else
    echo "  ❌ 服务未运行，检查日志："
    sudo journalctl -u ${SERVICE_NAME} --no-pager -n 20
    exit 1
fi

# ── 完成 ──
echo ""
echo "============================================"
echo "  更新完成！"
echo "============================================"
echo ""
echo "  服务状态: $(systemctl is-active ${SERVICE_NAME})"
echo "  查看日志: sudo journalctl -u ${SERVICE_NAME} -f"
echo "  访问地址: http://$(hostname -I | awk '{print $1}')"
echo ""
