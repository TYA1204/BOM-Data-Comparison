#!/bin/bash
# ============================================================
#  BOM Data Comparison — Ubuntu 26.04 LTS 一键部署脚本
#  用法: sudo bash deploy.sh
# ============================================================
set -e

APP_NAME="bom-comparison"
APP_DIR="/opt/${APP_NAME}"
APP_USER="www-data"
APP_GROUP="www-data"
PYTHON_BIN="python3"

echo "============================================"
echo "  BOM Data Comparison 部署脚本"
echo "  目标: Ubuntu 26.04 LTS"
echo "============================================"
echo ""

# ── Step 1: 安装系统依赖 ──
echo "[1/6] 安装系统依赖..."
sudo apt update -qq
sudo apt install -y -qq \
    ${PYTHON_BIN} \
    ${PYTHON_BIN}-venv \
    ${PYTHON_BIN}-dev \
    nginx \
    fonts-noto-cjk \
    fonts-wqy-zenhei \
    fonts-wqy-microhei
echo "  ✅ 系统依赖安装完成"

# ── Step 2: 创建应用目录 ──
echo "[2/6] 创建应用目录..."
sudo mkdir -p "${APP_DIR}"
# 复制当前目录所有文件到应用目录（排除 venv、__pycache__、.git）
sudo rsync -a --exclude='venv' --exclude='__pycache__' --exclude='.git' --exclude='*.pyc' \
    "$(dirname "$0")/" "${APP_DIR}/"
sudo mkdir -p "${APP_DIR}/uploads" "${APP_DIR}/reports" "${APP_DIR}/data" "${APP_DIR}/exports"
sudo chown -R ${APP_USER}:${APP_GROUP} "${APP_DIR}"
echo "  ✅ 应用代码已部署到 ${APP_DIR}"

# ── Step 3: 创建 Python 虚拟环境 ──
echo "[3/6] 创建 Python 虚拟环境..."
cd "${APP_DIR}"
sudo -u ${APP_USER} ${PYTHON_BIN} -m venv venv
sudo -u ${APP_USER} venv/bin/pip install --upgrade pip -q
sudo -u ${APP_USER} venv/bin/pip install -r requirements.txt -q
sudo -u ${APP_USER} venv/bin/pip install gunicorn -q
echo "  ✅ Python 环境创建完成"

# ── Step 4: 设置环境变量 ──
echo "[4/6] 配置环境变量..."
if [ ! -f "${APP_DIR}/.env" ]; then
    cat > "${APP_DIR}/.env" << 'EOF'
# BOM Data Comparison 生产环境配置
SECRET_KEY=change-me-to-a-random-string
FLASK_ENV=production
EOF
    echo "  ⚠️ 请编辑 ${APP_DIR}/.env 修改 SECRET_KEY"
fi
sudo chown ${APP_USER}:${APP_GROUP} "${APP_DIR}/.env"
sudo chmod 640 "${APP_DIR}/.env"
echo "  ✅ 环境变量配置完成"

# ── Step 5: 创建 systemd 服务 ──
echo "[5/6] 创建 systemd 服务..."
sudo tee /etc/systemd/system/${APP_NAME}.service > /dev/null << EOF
[Unit]
Description=BOM Data Comparison Flask Application
After=network.target

[Service]
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/venv/bin/gunicorn \\
    -w 4 \\
    -b 127.0.0.1:5002 \\
    --access-logfile ${APP_DIR}/gunicorn-access.log \\
    --error-logfile ${APP_DIR}/gunicorn-error.log \\
    --max-requests 1000 \\
    --max-requests-jitter 100 \\
    wsgi:app
Restart=always
RestartSec=5
StandardOutput=append:${APP_DIR}/gunicorn.log
StandardError=append:${APP_DIR}/gunicorn.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${APP_NAME}
sudo systemctl restart ${APP_NAME}
echo "  ✅ systemd 服务已创建并启动"

# ── Step 6: 配置 Nginx ──
echo "[6/6] 配置 Nginx..."
# 获取服务器 IP 用于显示
SERVER_IP=$(hostname -I | awk '{print $1}')

sudo tee /etc/nginx/sites-available/${APP_NAME} > /dev/null << EOF
server {
    listen 80;
    server_name _;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:5002;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 60s;
        proxy_read_timeout 60s;
    }

    # 静态文件直接由 Nginx 提供（可选优化）
    location /static/ {
        alias ${APP_DIR}/app/static/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/${APP_NAME} /etc/nginx/sites-enabled/
# 如果默认站点存在，禁用它
sudo rm -f /etc/nginx/sites-enabled/default

# 测试 Nginx 配置
sudo nginx -t
sudo systemctl restart nginx
echo "  ✅ Nginx 配置完成"

# ── 验证 ──
echo ""
echo "============================================"
echo "  部署完成！"
echo "============================================"
echo ""
echo "  应用目录:   ${APP_DIR}"
echo "  服务端口:   5002 (内部) / 80 (Nginx)"
echo "  服务管理:"
echo "    sudo systemctl status ${APP_NAME}"
echo "    sudo systemctl restart ${APP_NAME}"
echo "    sudo journalctl -u ${APP_NAME} -f"
echo ""
echo "  访问地址:   http://${SERVER_IP}"
echo ""
echo "  ⚠️ 请编辑 ${APP_DIR}/.env 修改 SECRET_KEY"
echo "  ⚠️ 如需域名，编辑 /etc/nginx/sites-available/${APP_NAME}"
echo ""
