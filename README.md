# BOM Data Comparison

跨机型 BOM 物料清单比对工具，对比两份 SAP BOM 的叶子物料差异，支持版本比对与跨机型比对。

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.11 + Flask 3.1 |
| 前端 | Vanilla JS + Tailwind CSS (CDN) |
| 数据库 | SQLite |
| 生产部署 | Gunicorn + Nginx |
| 服务器 | Ubuntu 26.04 LTS |

## 目录结构

```
.
├── app/
│   ├── __init__.py          # Flask 工厂函数 + 数据库迁移
│   ├── config.py            # 配置类
│   ├── models/              # 数据模型（SQLite 直操作）
│   ├── routes/              # 路由（upload / compare / report）
│   │   ├── upload.py        # BOM 上传 + 解析 + 树结构 API
│   │   ├── compare.py       # 比对 API
│   │   └── report.py        # 报告导出 API
│   ├── services/            # 业务逻辑
│   │   ├── differ.py        # 核心比对引擎
│   │   ├── change_notice.py # 差异归组 + WORD 导出
│   │   └── parser.py        # SAP BOM 解析器
│   ├── templates/           # Jinja2 模板
│   │   └── compare.html     # 主比对界面
│   └── static/              # 静态资源
│       ├── css/
│       └── js/
│           └── app.js       # 前端核心逻辑
├── scripts/                 # 运维脚本
│   ├── stop.sh              # 停止服务（自动清除 __pycache__）
│   ├── run.sh               # 启动服务（缓存检查 + PYTHONDONTWRITEBYTECODE）
│   ├── deploy.sh            # 一键部署（7 步自动化 + 自动回滚）
│   ├── health_check.sh      # 健康检查（7 项）
│   └── test_cache_protection.sh  # 缓存防护验证
├── wsgi.py                  # Gunicorn 入口
├── auto_update.sh           # 自动更新系统
├── update_config.conf       # 更新配置
├── requirements.txt         # Python 依赖
└── .env                     # 环境变量（含 SECRET_KEY，不入库）
```

## 核心功能

### 1. BOM 上传与解析

- 支持 SAP 导出的 `.xls` / `.xlsx` 格式 BOM 文件
- 自动解析组件层级、物料编码（PN）、名称、用量、单位等字段
- BOM 树结构可视化展示，按 SAP 原始行号排序

### 2. 两种比对模式

| 模式 | 范围 | 说明 |
|------|------|------|
| **组件对比（排除物料）** | 全层级，仅组件 | 遍历全部子层级，对比所有"有子件"的组件节点，排除纯叶子物料 |
| **全层级对比（含物料）** | 全层级，全部 | 遍历全部子层级，组件与物料完整对比 |

### 3. 差异类型

| 类型 | 标签 | 说明 |
|------|------|------|
| `added` | 新增 | PN 在目标 BOM 中存在，在基准 BOM 中不存在 |
| `removed` | 删除 | PN 在基准 BOM 中存在，在目标 BOM 中不存在 |
| `modified` | 变更 | 相同 PN + 相同父件，用量/单位/版本发生变化 |

分类维度：
- `component` — 组件（有子件的 PN）
- `leaf` — 物料（无子件的叶子 PN）
- `quantity` — 数量变更
- `unit` — 单位变更
- `version` — 版本变更（仅版本比对）

### 4. 差异报告导出

- **WORD**：按 P3EM 父节点归组，H5F 叶子数据按功能键合并到对应 P3EM 父节点下
- **Excel**：结构化列表导出

## 快速开始

### 环境要求

- Python 3.11+
- Node.js（前端构建可选，当前使用 CDN）

### 本地开发

```bash
# 克隆项目
git clone <repo-url>
cd bom-comparison

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate   # Linux/Mac
# venv\Scripts\activate    # Windows

# 安装依赖
pip install -r requirements.txt

# 配置环境变量（复制模板后编辑 SECRET_KEY）
cp .env.example .env

# 启动开发服务器
python wsgi.py
```

访问 `http://localhost:5002`

### 生产部署（服务器）

```bash
# 一键部署（推荐）
cd ~/services/bom-comparison
bash deploy.sh ~/bom-update.tar.gz

# 手动部署
bash stop.sh            # 停服 + 自动清除缓存
bash run.sh --force     # 启动（含缓存检查）

# 健康检查
bash health_check.sh
```

## 部署运维

### 服务管理

| 操作 | 命令 |
|------|------|
| 启动 | `bash run.sh` 或 `bash run.sh --force` |
| 停止 | `bash stop.sh`（自动清除 __pycache__） |
| 重启 | `bash stop.sh && bash run.sh --force` |
| 状态 | `bash run.sh status` 或 `bash health_check.sh` |
| 健康检查 | `bash health_check.sh [--json] [--silent]` |

### Python 缓存防护（铁律）

- `PYTHONDONTWRITEBYTECODE=1` 已在 run.sh 和 wsgi.py 中双重设置
- stop.sh 每次停服自动清除 `__pycache__` 和 `.pyc`
- run.sh 启动前扫描缓存残留，发现则阻止启动
- 部署后必须执行 `bash health_check.sh` 确认 7 项检查通过
- **严禁**修改 .py 后直接 `bash run.sh`，必须先 `bash stop.sh`

### 自动更新

```bash
bash auto_update.sh check       # 检查环境
bash auto_update.sh update      # 更新部署
bash auto_update.sh rollback    # 回滚到上一版本
bash auto_update.sh status      # 查看状态
bash auto_update.sh backup      # 手动创建备份
bash auto_update.sh schedule    # 配置定时更新
```

更新源支持 `package` / `git` / `http`，配置在 `update_config.conf`。

## API 接口

### BOM 相关

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/upload/api/parse` | 上传并解析 BOM 文件 |
| GET | `/upload/api/bom-list` | 获取 BOM 列表 |
| GET | `/upload/api/bom-tree/<id>` | 获取 BOM 树结构 |

### 比对相关

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/compare/api/start` | 启动比对任务 |
| GET | `/compare/api/result/<task_id>` | 获取比对结果 |
| GET | `/compare/api/history` | 比对历史 |
| DELETE | `/compare/api/result/<task_id>` | 删除比对任务 |

### 报告相关

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/report/api/word/<task_id>` | 导出 WORD 报告 |
| POST | `/report/api/excel/<task_id>` | 导出 Excel 报告 |

### 健康检查

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 轻量健康检查（JSON） |

## 配置说明

### 关键配置项（update_config.conf）

```ini
# 更新源类型: package | git | http
UPDATE_SOURCE=package

# 保留备份上限
BACKUP_MAX_COUNT=10

# 健康检查重试
HEALTH_CHECK_RETRIES=3

# 禁止 Python 生成 .pyc（推荐 true）
PYTHON_NO_BYTECODE=true

# 启动前强制检查缓存残留（推荐 true）
PYCACHE_CHECK_ENFORCE=true
```

### 环境变量（.env）

```bash
SECRET_KEY=<random-string>
FLASK_ENV=production
PORT=40045
```

## 对比逻辑流程

```
选中组件 → 全层级递归收集子树 (_collect_subtree)
         → 计算 parent_pns（基于全量 BOM 数据）
         → [可选] exclude_leaves: 排除不在 parent_pns 中的项
         → 逐项比对:
           Step 1: 新增（PN in B not in A）
           Step 2: 删除（PN in A not in B）
           Step 3: 变更（同 PN + 同父件 → 用量/单位/版本变化）
         → 输出差异记录
```

## 叶子数据定义

叶子 = PN 在整个 BOM 树中**从未作为任何行的 `parent_pn` 出现**的节点。即该物料不包含任何子件。

## License

[Internal Use]
