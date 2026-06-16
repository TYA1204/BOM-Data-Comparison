"""
XMind 导出服务模块。

将 BOM 数据生成为标准 .xmind 格式文件，支持：
  - 单个 BOM 导出（一个 Sheet）
  - 多个 BOM 导出为工作簿（每个 BOM 一个 Sheet）
  - 完整的工作簿结构、主题层级、备注等核心元素
"""

import io
import json
import os
import sqlite3
import tempfile
import uuid
import zipfile

MAX_NOTES_ITEMS = 200


# ---------- 数据库查询 ----------

def get_bom_info(conn, bom_id):
    """获取 BOM 基本信息。"""
    row = conn.execute(
        "SELECT bom_name FROM bom_header WHERE id=?", (bom_id,)
    ).fetchone()
    if not row:
        return None
    pn_row = conn.execute(
        "SELECT parent_pn FROM bom_item WHERE bom_id=? AND level=1 ORDER BY line_no LIMIT 1",
        (bom_id,)
    ).fetchone()
    root_pn = pn_row[0] if pn_row else f"P{bom_id}"
    return {"bom_name": row[0], "root_pn": root_pn}


def get_child_items(conn, bom_id, parent_pn):
    """查询指定 PN 下的所有子项。"""
    rows = conn.execute(
        "SELECT part_number, part_name, quantity, unit FROM bom_item "
        "WHERE bom_id=? AND parent_pn=? ORDER BY line_no",
        (bom_id, parent_pn)
    ).fetchall()
    return rows


def get_all_parent_pns(conn, bom_id):
    """获取所有是父件的 PN 集合。"""
    rows = conn.execute(
        "SELECT DISTINCT parent_pn FROM bom_item WHERE bom_id=? AND parent_pn!=''",
        (bom_id,)
    ).fetchall()
    return {r[0] for r in rows}


# ---------- XMind 节点构建 ----------

def make_topic(title, cls=None, notes_content=None):
    """创建 XMind topic 字典。"""
    topic = {
        "id": str(uuid.uuid4()),
        "title": title,
    }
    if cls:
        topic["class"] = cls
    if notes_content:
        topic["notes"] = {"plain": {"content": notes_content}}
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
    """递归构建 XMind topic 子树。"""
    items = get_child_items(conn, bom_id, parent_pn)
    if not items:
        return None, []

    children_topics = []
    all_leaves = []

    for part_number, part_name, quantity, unit in items:
        title = f"{part_number} {part_name}" if part_name else part_number
        if part_number in parent_pn_set:
            child_topic, child_leaves = build_topic_tree(
                conn, bom_id, part_number, title, parent_pn_set, depth + 1
            )
            if child_topic:
                children_topics.append(child_topic)
                all_leaves.extend(child_leaves)
        else:
            all_leaves.append((part_number, part_name, quantity, unit))

    topic_cls = "minorTopic" if depth >= 2 else None
    topic = make_topic(title_prefix, cls=topic_cls)

    if children_topics:
        topic["children"] = {"attached": children_topics}

    notes = format_notes(all_leaves)
    if notes:
        topic["notes"] = {"plain": {"content": notes}}

    return topic, all_leaves


# ---------- XMind 文件操作 ----------

def read_template(xmind_path):
    """读取模板 XMind 文件的 sheet 结构和附件。"""
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


def build_xmind_sheets(conn, bom_ids, template_path):
    """
    根据指定的 BOM ID 列表，构建 XMind 工作簿（多个 Sheet）。

    Args:
        conn: 数据库连接
        bom_ids: BOM ID 列表
        template_path: 模板 XMind 文件路径

    Returns:
        (sheets, other_entries) — 完整的 XMind 数据
    """
    sheets_template, other_entries = read_template(template_path)
    template_sheet = sheets_template[0] if sheets_template else {}

    sheets = []

    for i, bom_id in enumerate(bom_ids):
        bom_info = get_bom_info(conn, bom_id)
        if not bom_info:
            continue

        bom_name = bom_info["bom_name"]
        root_pn = bom_info["root_pn"]
        parent_pn_set = get_all_parent_pns(conn, bom_id)

        root_title = f"BOM主数据：{bom_name}"
        root_topic, all_leaves = build_topic_tree(
            conn, bom_id, root_pn, root_title, parent_pn_set
        )

        if root_topic is None:
            continue

        # 根节点样式
        root_topic["structureClass"] = template_sheet.get("rootTopic", {}).get(
            "structureClass", "org.xmind.ui.logic.right"
        )
        root_topic["class"] = "topic"

        # 构建 Sheet
        sheet = {
            "id": str(uuid.uuid4()),
            "class": "sheet",
            "title": f"逻辑图-{bom_name}",
            "rootTopic": root_topic,
        }

        # 复制模板 Sheet 的其他字段（theme、extensions 等）
        for key in ("theme", "extensions", "arrangeableLayerOrder",
                     "revisionId", "zones", "topicOverlapping"):
            if key in template_sheet:
                sheet[key] = template_sheet[key]

        sheets.append(sheet)

    if not sheets:
        raise ValueError("没有可导出的 BOM 数据")

    return sheets, other_entries


def generate_xmind_bytes(conn, bom_ids, template_path):
    """
    生成 XMind 文件的字节流（不落盘，直接用于 HTTP 响应）。

    Args:
        conn: 数据库连接
        bom_ids: BOM ID 列表
        template_path: 模板 XMind 文件路径

    Returns:
        bytes — 完整的 .xmind 文件内容
    """
    sheets, other_entries = build_xmind_sheets(conn, bom_ids, template_path)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        content_bytes = json.dumps(
            sheets, ensure_ascii=False, indent=2
        ).encode('utf-8')
        zf.writestr('content.json', content_bytes)
        for name, data in other_entries:
            zf.writestr(name, data)

    return buf.getvalue()


def generate_xmind_file(conn, bom_ids, template_path, output_path):
    """
    生成 XMind 文件并保存到磁盘。

    Args:
        conn: 数据库连接
        bom_ids: BOM ID 列表
        template_path: 模板 XMind 文件路径
        output_path: 输出文件路径

    Returns:
        str — 保存的文件路径
    """
    data = generate_xmind_bytes(conn, bom_ids, template_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(data)
    return output_path
