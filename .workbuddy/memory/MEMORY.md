# BOM Data Comparison — 项目记忆

## Git 操作规范

- **铁律：确认修复/变更完成 → 立即 commit + push**，确保代码实时同步到 GitHub，不留积压。
- **每完成一个完整功能立即 commit**，不要积压未提交的变更。
- 禁止 `git stash → git pull --rebase → git stash pop` 这条链路，冲突时极易丢失本地修改。
- 有未提交修改需要拉取时：先 `git commit -m "wip"` 暂存，拉取后再 `git reset HEAD~1` 继续改。
- rebase 冲突时，必须仔细核对 `git status`，不要盲目 `git checkout --ours`。

## 项目背景

- 跨机型 BOM 比对工具，核心目标：确认叶子物料差异（物料新增/删除/用量变更），结构变更无业务价值，已移除。
- 差异类型仅保留：`material`（物料）、`quantity`（数量）、`unit`（单位）、`version`（版本）、`reference`（位号，2026-06-27 新增）。
- `severity`（严重度）字段已彻底清除，不在任何文件中出现。
- WORD 导出：只展示 P3EM 父节点，H5F 父节点隐藏，H5F 叶子数据按功能键合并到对应 P3EM 父节点下。
- WORD 导出归组逻辑：按叶子节点的直接上级父组件归组，中间层级节点不输出。

## 关键技术决策

- `differ.py`：Step 3c（同 PN 不同父件的结构变更）已删除。Step 3 新增位号集合对比（同 PN + 同 parent，比对 reference_a/reference_b 集合差集，生成 diff_category='reference' 记录）。
- `change_notice.py`：`group_diffs_by_parent()` 按叶子判定（PN ∉ parent_pn_set）重新归组，无 self-referencing。WORD 导出中 reference 变更直接归类到组件 ADD/DEL 区（位号新增→ADD，位号删除→DEL）。同 PN 同时有数量+位号变更时，位号 ADD/DEL 优先，数量行自动隐藏。
- `compare.html`：无严重度筛选器/列，无 structure 相关 UI。
- `reporter.py`：Excel 报告无严重度列/着色，`structure` 已从分类标签中移除。

## 服务器环境

- 地址：172.20.217.12:40045，Ubuntu 26.04 LTS
- 部署用户：tangyongan（无 sudo），项目路径：~/services/bom-comparison/
- Python：/usr/local/python3/python-3.11.2/bin/python3
- 服务管理：run.sh / stop.sh（非 systemd），PID 文件：app.pid
- pip 源：https://mirrors.aliyun.com/pypi/simple/
- crontab 不可用，替代方案：auto_update.sh schedule daemon
- .env 含 SECRET_KEY，禁止提交 git

## Python 缓存防护（铁律）

- **PYTHONDONTWRITEBYTECODE=1** 已在 run.sh 和 wsgi.py 中双重设置，从根源禁止 .pyc 生成。
- **部署铁律**：每次代码更新必须执行完整的 stop → clean pycache → start 流程，严禁跳过。
  - 推荐方式：`bash deploy.sh <source>`（一键部署，自动备份+回滚）
  - 手动方式：`bash stop.sh && bash run.sh --force`（stop.sh 已自动清除缓存）
  - **严禁**：修改 .py 后直接 `bash run.sh`（即使 --force），必须先用 stop.sh 停服清除。
- **健康检查**：部署后必须执行 `bash health_check.sh`，确认 7 项检查通过。
- run.sh 启动前自动扫描残留 __pycache__，发现即阻止启动（除非 --force）。
- wsgi.py 中的 `sys.dont_write_bytecode = True` 作为 Python 层面的最后防线。

## 自动更新系统

- `auto_update.sh` + `update_config.conf`：一键式持久化维护
- 命令：check / update / rollback / status / backup / schedule / help
- 更新源：package（默认，packages/ 目录放 tar.gz）/ git / http
- 模式：--silent（静默）/ --force（跳过确认）/ --dry-run（模拟）
- 进程检测：按端口（40045）过滤，避免多用户服务器误匹配
- 备份策略：保留最近 10 个，自动清理旧备份
- 验证：7 项检查清单（进程/端口/页面/日志/DB）
