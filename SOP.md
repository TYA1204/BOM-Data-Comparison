# BOM Comparison — 更新迭代 SOP

## 前置条件

- 服务器: 172.20.217.12, Ubuntu 26.04 LTS
- 部署用户: tangyongan (无 sudo)
- Python: `/usr/local/python3/python-3.11.2/bin/python3`
- 项目目录: `~/services/bom-comparison`

---

## 一、更新前备份

每次更新代码或数据库结构前必须备份：

```bash
cd ~/services/bom-comparison

# 1. 备份 SQLite 数据库
cp code/data/bom_compare.db "code/data/bom_compare.db.$(date +%Y%m%d-%H%M%S).bak"

# 2. 备份当前代码（不含 venv，venv 可重建）
tar czf "backup-$(date +%Y%m%d-%H%M%S).tar.gz" \
    --exclude='code/venv' \
    --exclude='code/uploads' \
    --exclude='backup-*.tar.gz' \
    code/ .env run.sh stop.sh
```

---

## 二、代码更新

### 2.1 全量替换（用户提供新压缩包）

```bash
cd ~/services/bom-comparison

# 1. 停止服务
bash stop.sh

# 2. 备份（见第一章）

# 3. 清空旧代码，解压新代码
rm -rf code/app code/wsgi.py code/run.py code/requirements.txt
tar xzf /path/to/new-package.tar.gz -C code/

# 4. 比对新旧 requirements.txt，如有变化则重建 venv
diff <(tar xzf backup-*.tar.gz code/requirements.txt -O 2>/dev/null) \
     code/requirements.txt || echo "依赖有变化，需重建 venv"
```

### 2.2 增量更新（只改某几个文件）

```bash
cd ~/services/bom-comparison

# 1. 停止服务
bash stop.sh

# 2. 备份要修改的文件
cp code/app/routes/compare.py code/app/routes/compare.py.bak

# 3. 替换/编辑文件
# （用 scp 上传或直接 vim 编辑）

# 4. 如需新增依赖，进入 venv 安装
cd code && source venv/bin/activate
pip install <new-package> -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
deactivate
```

---

## 三、依赖管理

```bash
cd ~/services/bom-comparison/code
source venv/bin/activate

# 查看已安装包
pip freeze

# 安装新依赖
pip install <package> -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

# 批量安装（requirements.txt 有变化时）
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

# 重建 venv（依赖冲突时使用）
deactivate
rm -rf venv
/usr/local/python3/python-3.11.2/bin/python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
pip install gunicorn -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
```

---

## 四、数据库变更

SQLite 文件位于 `code/data/bom_compare.db`。

### 4.1 手动 SQL 变更

```bash
cd ~/services/bom-comparison/code
sqlite3 data/bom_compare.db

# 在 sqlite3 交互式终端中执行 SQL
# .schema          -- 查看表结构
# .tables          -- 查看所有表
# ALTER TABLE ...  -- 修改表
# .quit            -- 退出
```

### 4.2 应用层自动迁移

应用启动时 `app/__init__.py` 中的 `_migrate_comparison_result()` 会自动检测并添加缺失字段，新增迁移逻辑可参照该函数模式。

---

## 五、重启服务

```bash
cd ~/services/bom-comparison

# 停止
bash stop.sh

# 确认进程已退出
ps aux | grep gunicorn | grep wsgi:app | grep -v grep

# 若未退出，手动终止
pkill -f "gunicorn.*wsgi:app"

# 启动
bash run.sh

# 等待 3 秒后验证
sleep 3 && curl -sI http://127.0.0.1:40045/ | head -1
# 预期输出: HTTP/1.1 200 OK

# 确认看门狗还在跑（自动恢复机制）
bash scripts/watchdog.sh status
```

### 5.1 看门狗管理

看门狗守护进程每 30 秒检查服务是否存活，挂了自动拉起。

```bash
cd ~/services/bom-comparison

bash scripts/watchdog.sh status   # 查看状态
bash scripts/watchdog.sh start    # 启动
bash scripts/watchdog.sh stop     # 停止
bash scripts/watchdog.sh restart  # 重启

# 查看恢复日志
tail -f logs/watchdog.log
```

---

## 六、验证清单

更新后必须逐项验证：

| # | 检查项 | 命令 | 预期 |
|---|--------|------|------|
| 1 | 进程运行 | `ps aux \| grep gunicorn \| grep wsgi:app` | 有 1 master + 4 worker |
| 2 | 端口监听 | `ss -lntp \| grep 40045` | LISTEN 状态 |
| 3 | 首页可用 | `curl -sI http://127.0.0.1:40045/` | HTTP/1.1 200 |
| 4 | 上传页面 | `curl -sI http://127.0.0.1:40045/upload/` | HTTP/1.1 200 |
| 5 | 比对页面 | `curl -sI http://127.0.0.1:40045/compare/` | HTTP/1.1 200 |
| 6 | 错误日志 | `tail -20 logs/gunicorn-error.log` | 无新增异常 |
| 7 | 数据库可读 | `sqlite3 code/data/bom_compare.db "SELECT count(*) FROM bom_header;"` | 正常返回数字 |
| 8 | 看门狗运行 | `bash scripts/watchdog.sh status` | 看门狗运行中 |

---

## 七、回滚

```bash
cd ~/services/bom-comparison

# 1. 停止服务
bash stop.sh

# 2. 恢复代码（从备份）
tar xzf backup-YYYYMMDD-HHMMSS.tar.gz

# 3. 恢复数据库
cp code/data/bom_compare.db.YYYYMMDD-HHMMSS.bak code/data/bom_compare.db

# 4. 重建 venv（如 requirements 有变化）
cd code && rm -rf venv && /usr/local/python3/python-3.11.2/bin/python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
pip install gunicorn -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

# 5. 重启
cd ~/services/bom-comparison && bash run.sh

# 6. 验证（见第六章）
```

---

## 八、端口变更

如需更换监听端口：

```bash
cd ~/services/bom-comparison

# 1. 停止服务
bash stop.sh

# 2. 探测新端口（参见部署规则 §4）
PORT=新端口
ss -lntp 2>/dev/null | awk '{print $4}' | grep -E "[:.]${PORT}$" && echo "占用" || echo "空闲"

# 3. 修改 .env 中的 PORT
vim .env  # 改 PORT=新端口

# 4. 重启
bash run.sh
```

---

## 九、常见问题

| 现象 | 原因 | 解决 |
|------|------|------|
| 502/拒绝连接 | 进程未启动 | `bash run.sh`；看门狗 30s 内会自动恢复 |
| 端口占用 | 上次未正常退出 | `pkill -f "gunicorn.*wsgi:app"` 后重启 |
| pip 安装超时 | 内网网络波动 | 重试，已配置阿里源 |
| SQLite locked | 并发写入冲突 | 非高并发场景极少出现，重启即可 |
| Word 报告乱码 | 缺少中文字体 | 联系管理员安装或改用 PDF |

---

## 十、禁止事项

- 禁止 `sudo` / `systemctl` / `apt install`（无权限，直接拒绝）
- 禁止修改 `/etc/nginx`、`/etc/systemd` 等系统路径
- 禁止在 `code/venv/` 外写入文件
- 禁止删除 `code/data/bom_compare.db` 及其备份
- `.env` 含 SECRET_KEY，禁止提交到 git
