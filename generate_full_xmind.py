#!/usr/bin/env python3
"""
将 XMind 逻辑图中「物料清单」节点的叶子物料信息合并到父组件节点，
删除独立的「物料清单」节点，生成紧凑版 XMind 文件。

行为:
    修改前: 父组件 → 物料清单 → 669个叶子物料子节点
    修改后: 父组件 (notes 中包含【物料清单】共N个物料 + 完整列表)

用法:
    python generate_full_xmind.py

配置:
    默认使用脚本内置的路径配置，也可通过命令行参数覆盖:
    python generate_full_xmind.py --input <xmind_path> --db <db_path> --bom-id <id> --output <output_path>
"""

import argparse
import json
import os
import re
import sqlite3
import tempfile
import uuid
import zipfile


# ============ 默认配置 ============
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_XMIND_INPUT = os.path.join(_PROJECT_ROOT, "ogic block diagram", "P1C85Q7HXX8TB56000.xmind")
DEFAULT_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "bom_compare.db")
DEFAULT_BOM_ID = 1
DEFAULT_MAX_NOTES_ITEMS = 200  # notes 最多显示的物料数（防止过大）

# PN提取正则: 匹配标题开头的物料号 (如 N030103-022400-20X)
PN_PATTERN = re.compile(r'^([A-Z]\d{6}-\d{6}-\d{2,3}[A-Za-z0-9]*)')


def extract_pn(title):
    """从节点标题中提取PN号。

    Args:
        title: 节点标题，如 "N030103-022400-20X 主板组件 研发机贴组件"

    Returns:
        提取到的PN字符串，如 "N030103-022400-20X"；提取失败返回 None
    """
    if not title:
        return None
    match = PN_PATTERN.match(title.strip())
    return match.group(1) if match else None


def get_leaf_items(conn, bom_id, parent_pn):
    """查询指定PN下的所有叶子物料。

    叶子物料定义: part_number 不在所有 parent_pn 集合中（即该物料没有子组件）。

    Args:
        conn: SQLite 数据库连接
        bom_id: BOM ID
        parent_pn: 父组件PN号

    Returns:
        元组列表: [(part_number, part_name, quantity, unit, reference, alternative), ...]
    """
    cursor = conn.execute('''
        SELECT part_number, part_name, quantity, unit, reference, alternative
        FROM bom_item
        WHERE bom_id = ? AND parent_pn = ?
        AND part_number NOT IN (
            SELECT DISTINCT parent_pn FROM bom_item
            WHERE bom_id = ? AND parent_pn != ''
        )
        ORDER BY line_no
    ''', (bom_id, parent_pn, bom_id))
    return cursor.fetchall()


def format_material_notes(items, max_items=200):
    """将叶子物料列表格式化为父节点的 notes 内容。

    格式示例：
        【物料清单】共 54 个物料
        N010501-000032-001  贴片磁珠 0603 60Ω 3000mA...  |  1.0 PC
        N010502-000362-001  贴片功率电感 4030 4.7μH...        |  2.0 PC
        ...

    Args:
        items: 元组列表 [(part_number, part_name, quantity, unit, reference, alternative), ...]
        max_items: 最多写入的物料数（防止 notes 过大）

    Returns:
        格式化的字符串
    """
    if not items:
        return ""

    total = len(items)
    truncated = total > max_items
    display_items = items[:max_items]

    lines = [f"【物料清单】共 {total} 个物料"]
    lines.append("-" * 50)

    for part_number, part_name, quantity, unit, reference, alternative in display_items:
        qty_str = f"{quantity} {unit}" if quantity is not None else "-"
        lines.append(f"{part_number}  {part_name}  |  {qty_str}")

    if truncated:
        lines.append(f"... 省略 {total - max_items} 个物料 ...")

    return "\n".join(lines)


def merge_materials_to_parent(topic, conn, bom_id, stats):
    """递归遍历 XMind 主题树，找到「物料清单」节点，将物料信息合并到父节点 notes，
    并删除「物料清单」节点本身。

    效果：
        修改前: 父组件 → 物料清单 → (待填充)
        修改后: 父组件 (notes 中包含完整物料列表)

    Args:
        topic: 当前 XMind topic 字典
        conn: SQLite 数据库连接
        bom_id: BOM ID
        stats: 统计信息字典，会被就地修改
    """
    children = topic.get('children', {}).get('attached', [])
    new_children = []

    for child in children:
        child_title = child.get('title', '')

        if child_title == '物料清单':
            # 找到物料清单节点 → 提取当前 topic（父组件）的 PN
            parent_title = topic.get('title', '')
            pn = extract_pn(parent_title)
            if pn:
                items = get_leaf_items(conn, bom_id, pn)
                if items:
                    # 将物料信息写入父节点的 notes
                    material_notes = format_material_notes(items)
                    _append_notes(topic, material_notes)

                    stats['merged'] += 1
                    stats['total_items'] += len(items)
                    print(f"  合并: PN={pn}, 叶子物料数={len(items)}")
                else:
                    stats['empty'] += 1
                    print(f"  警告: PN={pn} 下无叶子物料")
            else:
                stats['no_pn'] += 1
                print(f"  警告: 无法从父标题提取PN: '{parent_title}'")
            # 不添加该子节点（删除「物料清单」节点）
        else:
            # 递归处理非物料清单的子节点
            merge_materials_to_parent(child, conn, bom_id, stats)
            new_children.append(child)

    # 更新子节点列表（移除所有「物料清单」节点）
    topic.setdefault('children', {})['attached'] = new_children


def _append_notes(topic, content):
    """向 XMind topic 追加 notes 内容。

    如果已有 notes，将新内容追加到末尾（用双换行分隔）。

    Args:
        topic: XMind topic 字典
        content: 要追加的文本内容
    """
    if not content:
        return

    if 'notes' not in topic:
        topic['notes'] = {'plain': {'content': ''}}

    existing = topic['notes'].get('plain', {}).get('content', '')
    if existing:
        topic['notes']['plain']['content'] = existing + '\n\n' + content
    else:
        topic['notes']['plain']['content'] = content


def read_xmind(file_path):
    """读取 XMind 文件，返回 (content_json, 其他文件条目列表)。

    Args:
        file_path: XMind 文件路径

    Returns:
        (content_list, other_entries)
        - content_list: content.json 解析后的 Python 列表
        - other_entries: [(entry_name, bytes_data), ...] 其他需要原样保存的文件
    """
    content_list = None
    other_entries = []

    with zipfile.ZipFile(file_path, 'r') as zf:
        for name in zf.namelist():
            data = zf.read(name)
            if name == 'content.json':
                content_list = json.loads(data.decode('utf-8'))
            else:
                other_entries.append((name, data))

    if content_list is None:
        raise ValueError(f"XMind 文件中未找到 content.json: {file_path}")

    return content_list, other_entries


def write_xmind(file_path, content_list, other_entries):
    """将修改后的内容写入新的 XMind 文件。

    Args:
        file_path: 输出文件路径
        content_list: content.json 数据（Python 列表）
        other_entries: [(entry_name, bytes_data), ...] 其他需要原样保存的文件
    """
    # 先写入临时文件，再重命名为目标文件（原子写入）
    dir_name = os.path.dirname(os.path.abspath(file_path))
    tmp_path = os.path.join(dir_name, f".tmp_xmind_{uuid.uuid4().hex}.xmind")

    try:
        with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # 写入修改后的 content.json
            content_bytes = json.dumps(
                content_list, ensure_ascii=False, indent=2
            ).encode('utf-8')
            zf.writestr('content.json', content_bytes)

            # 写入其他原样文件
            for name, data in other_entries:
                zf.writestr(name, data)

        # 关闭后替换目标文件
        if os.path.exists(file_path):
            os.remove(file_path)
        os.rename(tmp_path, file_path)
    except Exception:
        # 出错时清理临时文件
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def generate_output_path(input_path):
    """根据输入路径自动生成输出路径。

    在文件名后添加 '_compact' 后缀，如:
    P1C85Q7HXX8TB56000.xmind -> P1C85Q7HXX8TB56000_compact.xmind

    Args:
        input_path: 输入 XMind 文件路径

    Returns:
        输出文件路径
    """
    base, ext = os.path.splitext(input_path)
    return f"{base}_compact{ext}"


def main():
    """主函数：读取 XMind -> 查询数据库 -> 填充节点 -> 保存新文件。"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(
        description='将 XMind 逻辑图中「物料清单」叶子物料信息合并到父节点，生成紧凑版 XMind 文件'
    )
    parser.add_argument('--input', default=DEFAULT_XMIND_INPUT,
                        help='输入 XMind 文件路径')
    parser.add_argument('--db', default=DEFAULT_DB_PATH,
                        help='SQLite 数据库路径')
    parser.add_argument('--bom-id', type=int, default=DEFAULT_BOM_ID,
                        help='BOM ID（默认: 1）')
    parser.add_argument('--output', default=None,
                        help='输出 XMind 文件路径（默认: 输入文件名_compact.xmind）')
    args = parser.parse_args()

    input_path = args.input
    db_path = args.db
    bom_id = args.bom_id
    output_path = args.output or generate_output_path(input_path)

    print("=" * 60)
    print("XMind 物料信息合并工具（紧凑版）")
    print("=" * 60)
    print(f"输入文件: {input_path}")
    print(f"数据库:   {db_path}")
    print(f"BOM ID:   {bom_id}")
    print(f"输出文件: {output_path}")
    print()

    # 1. 验证输入文件
    if not os.path.exists(input_path):
        print(f"错误: XMind 文件不存在: {input_path}")
        return 1
    if not os.path.exists(db_path):
        print(f"错误: 数据库文件不存在: {db_path}")
        return 1

    # 2. 读取 XMind 文件
    print("[1/4] 读取 XMind 文件...")
    try:
        content_list, other_entries = read_xmind(input_path)
        print(f"  成功: {len(content_list)} 个 Sheet")
    except Exception as e:
        print(f"错误: 读取 XMind 文件失败: {e}")
        return 1

    # 3. 连接数据库并填充节点
    print("[2/4] 连接数据库...")
    try:
        conn = sqlite3.connect(db_path)
    except Exception as e:
        print(f"错误: 连接数据库失败: {e}")
        return 1

    print("[3/4] 合并物料信息到父节点...")
    stats = {
        'merged': 0,       # 成功合并的物料清单节点数
        'empty': 0,        # 无叶子物料的节点数
        'no_pn': 0,        # 无法提取PN的节点数
        'total_items': 0,  # 合并的总物料数
    }

    try:
        for sheet in content_list:
            root_topic = sheet.get('rootTopic')
            if root_topic:
                merge_materials_to_parent(root_topic, conn, bom_id, stats)
    finally:
        conn.close()

    print()
    print("-" * 40)
    print(f"合并完成: {stats['merged']} 个组件")
    print(f"总物料数: {stats['total_items']}")
    if stats['empty'] > 0:
        print(f"警告: {stats['empty']} 个节点无叶子物料")
    if stats['no_pn'] > 0:
        print(f"警告: {stats['no_pn']} 个节点无法提取PN")
    print("-" * 40)

    # 4. 保存新的 XMind 文件
    print("[4/4] 保存新 XMind 文件...")
    try:
        write_xmind(output_path, content_list, other_entries)
        file_size = os.path.getsize(output_path)
        print(f"  成功: {output_path}")
        print(f"  文件大小: {file_size / 1024:.1f} KB")
    except Exception as e:
        print(f"错误: 保存 XMind 文件失败: {e}")
        return 1

    print()
    print("=" * 60)
    print("处理完成!")
    print("=" * 60)
    return 0


if __name__ == '__main__':
    exit(main())
