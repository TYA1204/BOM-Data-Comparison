# BOM Data Comparison — 项目记忆

## Git 操作规范

- **每完成一个完整功能立即 commit**，不要积压未提交的变更。
- 禁止 `git stash → git pull --rebase → git stash pop` 这条链路，冲突时极易丢失本地修改。
- 有未提交修改需要拉取时：先 `git commit -m "wip"` 暂存，拉取后再 `git reset HEAD~1` 继续改。
- rebase 冲突时，必须仔细核对 `git status`，不要盲目 `git checkout --ours`。

## 项目背景

- 跨机型 BOM 比对工具，核心目标：确认叶子物料差异（物料新增/删除/用量变更），结构变更无业务价值，已移除。
- 差异类型仅保留：`material`（物料）、`quantity`（数量）、`unit`（单位）、`version`（版本）。
- `severity`（严重度）字段已彻底清除，不在任何文件中出现。
- WORD 导出：只展示 P3EM 父节点，H5F 父节点隐藏，H5F 叶子数据按功能键合并到对应 P3EM 父节点下。
- WORD 导出归组逻辑：按叶子节点的直接上级父组件归组，中间层级节点不输出。

## 关键技术决策

- `differ.py`：Step 3c（同 PN 不同父件的结构变更）已删除。
- `change_notice.py`：`group_diffs_by_parent()` 按叶子判定（PN ∉ parent_pn_set）重新归组，无 self-referencing。
- `compare.html`：无严重度筛选器/列，无 structure 相关 UI。
- `reporter.py`：Excel 报告无严重度列/着色，`structure` 已从分类标签中移除。
