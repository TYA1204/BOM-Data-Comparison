# BOM Data Comparison — Ubuntu 26.04 LTS 部署指南

## 项目技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| Web 框架 | Flask 3.1.1 | Python Web 框架 |
| WSGI 服务器 | Gunicorn | 生产级 WSGI HTTP 服务器 |
| 反向代理 | Nginx | 静态文件 + 反向代理 |
| 数据库 | SQLite 3 | 文件型数据库，零配置 |
| 进程管理 | systemd | Linux 原生服务管理 |
| Excel 解析 | openpyxl 3.1.5 / xlrd 2.0.1 | .xlsx / .xls 文件读取 |
| 数据分析 | pandas 2.2.3 | 数据处理与对比 |
| Word 生成 | python-docx 1.2+ | .docx 报告生成 |
| 模糊匹配 | rapidfuzz 3.12.2 | 物料名称模糊比对 |
| 压缩 | Flask-Compress 1.17 | HTTP 响应压缩 |

### Python 依赖清单

```text
Flask==3.1.1
Flask-Compress==1.17
openpyxl==3.1.5
xlrd==2.0.1
pandas==2.2.3
python-docx>=1.2.0
rapidfuzz==3.12.2
Werkzeug==3.1.3
gunicorn          # 生产部署额外依赖
```

### 项目文件结构

```
bom-comparison/
├── app/
│   ├── __init__.py          # Flask 工厂函数
│   ├── config.py            # 配置（数据库路径、上传限制等）
│   ├── models/              # 数据模型 (SQLAlchemy)
│   ├── routes/              # 路由 (main, upload, compare, report)
│   ├── services/            # 业务逻辑 (parser, differ, reporter, change_notice)
│   ├── static/              # CSS/JS 静态文件
│   └── templates/           # Jinja2 HTML 模板
├── data/                    # SQLite 数据库文件
├── uploads/                 # 上传文件临时目录
├── reports/                 # 导出报告目录
├── exports/                 # 导出文件目录
├── 整机清机更改通知单.docx   # Word 模板
├── wsgi.py                  # Gunicorn 入口
├── requirements.txt
├── deploy.sh                # 一键部署脚本
└── deploy.md                # 本文档
```

---

## 快速部署（推荐）

```bash
# 1. 上传压缩包到服务器
scp bom-comparison-*.tar.gz user@server:/tmp/

# 2. 解压并部署
ssh user@server
cd /tmp
tar -xzf bom-comparison-*.tar.gz
cd bom-comparison
sudo bash deploy.sh
```

部署脚本会自动完成：系统依赖安装 → 虚拟环境创建 → systemd 服务配置 → Nginx 配置。

---

## 手动部署

### 1. 系统依赖

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-dev nginx
# 中文字体（Word 导出必需）
sudo apt install -y fonts-noto-cjk fonts-wqy-zenhei fonts-wqy-microhei
```

### 2. 部署代码

```bash
sudo mkdir -p /opt/bom-comparison
sudo cp -r ./* /opt/bom-comparison/
sudo mkdir -p /opt/bom-comparison/{uploads,reports,data,exports}
sudo chown -R www-data:www-data /opt/bom-comparison
```

### 3. Python 虚拟环境

```bash
cd /opt/bom-comparison
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn
```

### 4. 环境变量

```bash
# 创建 .env 文件
cat > /opt/bom-comparison/.env << 'EOF'
SECRET_KEY=your-random-secret-key-here
FLASK_ENV=production
EOF
chown www-data:www-data /opt/bom-comparison/.env
chmod 640 /opt/bom-comparison/.env
```

### 5. systemd 服务

```bash
sudo tee /etc/systemd/system/bom-comparison.service << 'EOF'
[Unit]
Description=BOM Data Comparison Flask Application
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/bom-comparison
EnvironmentFile=/opt/bom-comparison/.env
ExecStart=/opt/bom-comparison/venv/bin/gunicorn \
    -w 4 \
    -b 127.0.0.1:5002 \
    --access-logfile /opt/bom-comparison/gunicorn-access.log \
    --error-logfile /opt/bom-comparison/gunicorn-error.log \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    wsgi:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable bom-comparison
sudo systemctl start bom-comparison
```

### 6. Nginx 反向代理

```bash
sudo tee /etc/nginx/sites-available/bom-comparison << 'EOF'
server {
    listen 80;
    server_name _;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:5002;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 60s;
        proxy_read_timeout 60s;
    }

    location /static/ {
        alias /opt/bom-comparison/app/static/;
        expires 7d;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/bom-comparison /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
```

---

## 服务管理

```bash
# 查看状态
sudo systemctl status bom-comparison

# 重启服务
sudo systemctl restart bom-comparison

# 查看日志
sudo journalctl -u bom-comparison -f

# 查看 Gunicorn 日志
tail -f /opt/bom-comparison/gunicorn.log
```

---

## 兼容性说明

| 项目 | 状态 | 备注 |
|------|------|------|
| Python 3.10+ | ✅ | Ubuntu 26.04 预装 |
| SQLite 3 | ✅ | 无需额外配置 |
| 中文字体 | ✅ | 已包含 Linux 字体回退 |
| 路径分隔符 | ✅ | 全部使用 `os.path.join()` |
| 文件上传 50MB | ✅ | Nginx + Flask 均已配置 |
| Gunicorn workers | 4 | 根据 CPU 核心数调整 (`-w` 参数) |

---

## 故障排查

| 问题 | 检查项 |
|------|--------|
| 502 Bad Gateway | `sudo systemctl status bom-comparison` 确认 Gunicorn 运行 |
| 文件上传失败 | 检查 `/opt/bom-comparison/uploads/` 权限 (www-data) |
| Word 报告乱码 | `sudo apt install fonts-noto-cjk fonts-wqy-microhei` |
| 端口占用 | `sudo lsof -i :5002` 检查端口 |
| 数据库锁定 | SQLite 不支持高并发写入，考虑单 worker 或迁移到 PostgreSQL |
