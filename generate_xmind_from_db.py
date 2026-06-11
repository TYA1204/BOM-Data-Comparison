#!/usr/bin/env python3
"""
从数据库 BOM 数据生成 XMind 逻辑图（紧凑版）。

特性:
  - 右向逻辑图 (org.xmind.ui.logic.right)
  - 组件层级树形展开
  - 叶子物料信息合并写入父组件 notes（不创建独立「物料清单」节点）

用法:
    python generate_xmind_from_db.py --bom-id 2

默认输出:
    ogic block diagram/{bom_name}_compact.xmind
"""

import argparse
import json
import os
import sqlite3
import tempfile
import uuid
import zipfile

# ============ 默认配置 ============
DEFAULT_TEMPLATE_XMIND = r"D:\BOM Data Comparison\ogic block diagram\P1C85Q7HXX8TB56000.xmind"
DEFAULT_DB_PATH = r"D:\BOM Data Comparison\data\bom_compare.db"
DEFAULT_BOM_ID = 2
DEFAULT_OUTPUT_DIR = r"D:\BOM Data Comparison\ogic block diagram"
MAX_NOTES_ITEMS = 200  # notes 中最多显示的物料数


# ---------- 数据库查询 ----------

def get_bom_info(conn, bom_id):
    """获取 BOM 基本信息。

    Returns:
        dict: {"bom_name": ..., "root_pn": ...} 或 None
    """
    row = conn.execute(
        "SELECT bom_name FROM bom_header WHERE id=?", (bom_id,)
    ).fetchone()
    if not row:
        return None

    # 根PN = level=1 记录的 parent_pn（数据中没有 level=0 的记录）
    pn_row = conn.execute(
        "SELECT parent_pn FROM bom_item WHERE bom_id=? AND level=1 ORDER BY line_no LIMIT 1",
        (bom_id,)
    ).fetchone()
    root_pn = pn_row[0] if pn_row else f"P{bom_id}"
    return {"bom_name": row[0], "root_pn": root_pn}


def get_child_items(conn, bom_id, parent_pn):
    """查询指定 PN 下的所有子项。返回带分类的列表。"""
    rows = conn.execute(
        "SELECT part_number, part_name, quantity, unit FROM bom_item "
        "WHERE bom_id=? AND parent_pn=? ORDER BY line_no",
        (bom_id, parent_pn)
    ).fetchall()
    return rows


def get_all_parent_pns(conn, bom_id):
    """获取所有是父件的 PN 集合（用于区分组件/物料）。"""
    rows = conn.execute(
        "SELECT DISTINCT parent_pn FROM bom_item WHERE bom_id=? AND parent_pn!=''",
        (bom_id,)
    ).fetchall()
    return {r[0] for r in rows}


# ---------- XMind 节点构建 ----------

def make_topic(title, cls=None, notes_content=None):
    """创建 XMind topic 字典。

    Args:
        title: 节点标题
        cls: XMind 样式类 ('minorTopic' 用于黄色子节点)
        notes_content: notes 文本内容

    Returns:
        XMind topic 字典
    """
    topic = {
        "id": str(uuid.uuid4()),
        "title": title,
    }
    if cls:
        topic["class"] = cls

    if notes_content:
        topic["notes"] = {
            "plain": {"content": notes_content}
        }

    return topic


def format_notes(items, max_items=MAX_NOTES_ITEMS):
    """将物料列表格式化为 notes 内容。"""
    if not items:
        return ""

    total = len(items)
    truncated = total > max_items
    display = items[:max_items]

    lines = [f"【物料清单】共 {total} 个物料"]
    lines.append("-" * 50)
    for pn, name, qty, unit in display:
        qty_str = f"{qty} {unit}" if qty is not None else "-"
        lines.append(f"{pn}  {name}  |  {qty_str}")

    if truncated:
        lines.append(f"... 省略 {total - max_items} 个物料 ...")

    return "\n".join(lines)


def build_topic_tree(conn, bom_id, parent_pn, title_prefix, parent_pn_set, depth=0):
    """递归构建 XMind topic 子树。

    逻辑：
      - 查询 parent_pn 下的所有子项
      - 子项分两类:
        a) 组件（其 PN 是某些记录的 parent_pn）→ 递归构建子树
        b) 叶子物料 → 收集起来，最终写入当前节点的 notes
      - 不创建「物料清单」子节点

    Args:
        conn: 数据库连接
        bom_id: BOM ID
        parent_pn: 当前父件 PN
        title_prefix: 节点标题
        parent_pn_set: 所有父件 PN 集合（用于快速分类）
        depth: 当前深度（用于样式控制，0=根, 1=L1, 2+=L2子节点）

    Returns:
        (topic_dict, leaf_items_list)
    """
    items = get_child_items(conn, bom_id, parent_pn)
    if not items:
        return None, []

    children_topics = []
    all_leaves = []

    for part_number, part_name, quantity, unit in items:
        title = f"{part_number} {part_name}" if part_name else part_number

        if part_number in parent_pn_set:
            # 子组件 → 递归
            child_topic, child_leaves = build_topic_tree(
                conn, bom_id, part_number, title, parent_pn_set, depth + 1
            )
            if child_topic:
                children_topics.append(child_topic)
                all_leaves.extend(child_leaves)
        else:
            # 叶子物料
            all_leaves.append((part_number, part_name, quantity, unit))

    # 构建当前节点
    # L2+ 节点使用 minorTopic 样式（黄色），L1 和根节点不设置
    topic_cls = "minorTopic" if depth >= 2 else None
    topic = make_topic(title_prefix, cls=topic_cls)

    if children_topics:
        topic["children"] = {"attached": children_topics}

    # 叶子物料写入 notes
    notes = format_notes(all_leaves)
    if notes:
        topic["notes"] = {"plain": {"content": notes}}

    return topic, all_leaves


# ---------- XMind 文件操作 ----------

def read_template(xmind_path):
    """读取模板 XMind 文件的 sheet 结构和附件。

    Returns:
        (sheets_list, other_entries)
    """
    with zipfile.ZipFile(xmind_path, 'r') as zf:
        sheets = None
        other = []
        for name in zf.namelist():
            data = zf.read(name)
            if name == 'content.json':
                sheets = json.loads(data.decode('utf-8'))
            else:
                other.append((name, data))

    if sheets is None:
        raise ValueError(f"模板 XMind 中未找到 content.json: {xmind_path}")
    return sheets, other


def write_xmind(file_path, sheets, other_entries):
    """写入 XMind 文件（原子操作）。"""
    dir_name = os.path.dirname(os.path.abspath(file_path))
    tmp_path = os.path.join(dir_name, f".tmp_xmind_{uuid.uuid4().hex}.xmind")

    try:
        with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            content_bytes = json.dumps(
                sheets, ensure_ascii=False, indent=2
            ).encode('utf-8')
            zf.writestr('content.json', content_bytes)
            for name, data in other_entries:
                zf.writestr(name, data)

        if os.path.exists(file_path):
            os.remove(file_path)
        os.rename(tmp_path, file_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


# ---------- 主流程 ----------

def main():
    parser = argparse.ArgumentParser(
        description='从数据库 BOM 数据生成 XMind 逻辑图（紧凑版）'
    )
    parser.add_argument('--template', default=DEFAULT_TEMPLATE_XMIND,
                        help='模板 XMind 文件路径')
    parser.add_argument('--db', default=DEFAULT_DB_PATH,
                        help='SQLite 数据库路径')
    parser.add_argument('--bom-id', type=int, default=DEFAULT_BOM_ID,
                        help='BOM ID（默认: 2）')
    parser.add_argument('--output', default=None,
                        help='输出 XMind 文件路径')
    args = parser.parse_args()

    print("=" * 60)
    print("XMind 逻辑图生成工具（紧凑版）")
    print("=" * 60)

    # 1. 读取模板
    print("[1/5] 读取模板 XMind...")
    if not os.path.exists(args.template):
        print(f"错误: 模板文件不存在: {args.template}")
        return 1
    sheets, other_entries = read_template(args.template)
    print(f"  模板: {args.template}")

    # 2. 连接数据库
    print("[2/5] 连接数据库...")
    if not os.path.exists(args.db):
        print(f"错误: 数据库不存在: {args.db}")
        return 1
    conn = sqlite3.connect(args.db)

    try:
        # 3. 获取 BOM 信息
        print("[3/5] 读取 BOM 数据...")
        bom_info = get_bom_info(conn, args.bom_id)
        if not bom_info:
            print(f"错误: 数据库中无 BOM id={args.bom_id}")
            return 1

        bom_name = bom_info["bom_name"]
        root_pn = bom_info["root_pn"]
        print(f"  BOM: {bom_name}")
        print(f"  根PN: {root_pn}")

        # 统计总物料数
        total = conn.execute(
            "SELECT COUNT(*) FROM bom_item WHERE bom_id=?", (args.bom_id,)
        ).fetchone()[0]
        print(f"  总记录: {total} 条")

        # 获取所有父件PN集合
        parent_pn_set = get_all_parent_pns(conn, args.bom_id)
        print(f"  组件PN数: {len(parent_pn_set)}")

        # 4. 构建节点树
        print("[4/5] 构建节点树...")
        root_title = f"BOM主数据：{bom_name}"

        root_topic, all_leaves = build_topic_tree(
            conn, args.bom_id, root_pn, root_title, parent_pn_set
        )

        if root_topic is None:
            print(f"错误: 无法构建节点树（根PN={root_pn} 无子项）")
            return 1

        # 根节点设置样式
        root_topic["structureClass"] = sheets[0]["rootTopic"].get(
            "structureClass", "org.xmind.ui.logic.right"
        )
        root_topic["class"] = "topic"
        del root_topic["class"]  # rootTopic 不需要 class
        # 重新设置为 topic class
        root_topic["class"] = "topic"

        # 替换 sheet 的 rootTopic
        sheets[0]["rootTopic"] = root_topic
        # 更新 sheet 标题
        sheets[0]["title"] = f"逻辑图-{bom_name}"

        # 统计信息
        l1_count = len(root_topic.get("children", {}).get("attached", []))
        leaf_count = len(all_leaves)
        print(f"  L1组件: {l1_count} 个")
        print(f"  叶子物料: {leaf_count} 个")

        # 5. 保存文件
        print("[5/5] 保存文件...")
        if args.output:
            output_path = args.output
        else:
            output_path = os.path.join(
                DEFAULT_OUTPUT_DIR, f"{bom_name}_compact.xmind"
            )

        write_xmind(output_path, sheets, other_entries)
        file_size = os.path.getsize(output_path)
        print(f"  成功: {output_path}")
        print(f"  文件大小: {file_size / 1024:.1f} KB")

    finally:
        conn.close()

    print()
    print("=" * 60)
    print("处理完成!")
    print("=" * 60)
    return 0


if __name__ == '__main__':
    exit(main())
