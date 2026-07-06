"""
SAP BOM 展开表 数据清洗器 v3.0
============================
处理 SAP 导出的 UTF-16 LE Tab 分隔 BOM 展开表文件。

文件结构:
  - 13页（每页65行），每页7行表头 + 列标题行 + 数据行
  - 子件展开标题行（11列格式）标记被展开的父件
  - 数据行（25列格式）包含序号、物料号、描述、用量等

核心逻辑 v3.0（修正v2.0的层级bug）:
  - SAP BOM是扁平分段结构，每个"子件展开标题行"定义一个section
  - section内的数据行是该标题PN的**直接子件**
  - col[0]是section内的序号，不是全局BOM层级
  - 真实层级通过section嵌套推导：
    * Root BOM下的section → L1
    * L1 section中出现的子件如果有自己的section → L2
    * L2 section中出现的子件如果有自己的section → L3
  - parent_pn = 当前section的标题PN

清洗步骤:
  1. 第一遍扫描: 收集所有标题行，建立section边界
  2. 建立section父子关系: 查找每个section的子件中哪些有独立section
  3. 推导层级: 从Root向下递归推导每个section的真实BOM层级
  4. 第二遍扫描: 对每个数据行赋值正确的level和parent_pn
  5. 列清洗: 提取核心字段
"""

import re
from pathlib import Path

# ==================== 配置 ====================

PN_PATTERN = re.compile(r'^[A-Z]\d{6}-\d{6}-\d{2,3}\w*$')
TOP_BOM_PATTERN = re.compile(r'^P[A-Z0-9]{2,}\d{2}[A-Z0-9]+\d{4,}$')

HEADER_KEYWORDS = [
    '用户：', '有效日期从', '顶层BOM号', '最后更改号',
    'BOM状态', 'BOM工厂', 'B O M 展开表', '页码',
    '日期：', '时间：',
]

COL_TITLE_PATTERN = re.compile(r'组\s+件')


# ==================== 行分类 ====================

def is_header_line(line):
    """判断是否为页表头行"""
    return any(kw in line for kw in HEADER_KEYWORDS)


def is_column_title_line(line):
    """判断是否为列标题行"""
    return bool(COL_TITLE_PATTERN.search(line))


def is_title_row(cols, root_bom=None):
    """
    判断是否为子件展开标题行。
    格式: PN在col[0], 描述在col[9]或col[10]（宽列=10, 窄列=9）, 约10-11列。
    支持标准物料号(PN_PATTERN)和顶层BOM号(TOP_BOM_PATTERN)。
    root_bom 用于兜底匹配：当正则不匹配时，直接字符串比较 col[0]。
    """
    if len(cols) < 9:
        return False
    col0 = cols[0].strip()
    # 1. 正则匹配标准格式
    if PN_PATTERN.match(col0) or TOP_BOM_PATTERN.match(col0):
        col9 = cols[9].strip() if len(cols) > 9 else ''
        col10 = cols[10].strip() if len(cols) > 10 else ''
        return bool(col9 or col10)
    # 2. 兜底：直接和 root_bom 字符串比较（兼容尾数不足的正则边缘用例）
    if root_bom and col0 == root_bom:
        return True
    return False


def is_data_row(cols):
    """
    判断是否为有效数据行。
    格式: col[0]=序号(数字), col[2]=物料号, 约25列。
    """
    if len(cols) < 3:
        return False
    col0 = cols[0].strip()
    col2 = cols[2].strip()
    return col0.isdigit() and PN_PATTERN.match(col2) is not None


# ==================== 元数据提取 ====================

def extract_metadata(lines):
    """从第一页表头提取BOM元数据"""
    metadata = {}
    for line in lines[:10]:
        cols = line.split('\t')
        for i, c in enumerate(cols):
            c = c.strip()
            if '顶层BOM号' in c:
                for j in range(i + 1, len(cols)):
                    v = cols[j].strip()
                    if v:
                        metadata['bom_number'] = v
                        break
            elif '最后更改号' in c:
                for j in range(i + 1, len(cols)):
                    v = cols[j].strip()
                    if v:
                        metadata['ecn'] = v
                        break
            elif '有效日期从' in c:
                for j in range(i + 1, len(cols)):
                    v = cols[j].strip()
                    if v and '日期' not in v and '时间' not in v:
                        metadata['valid_date'] = v
                        break
            elif 'BOM工厂' in c:
                for j in range(i + 1, len(cols)):
                    v = cols[j].strip()
                    if v:
                        metadata['plant'] = v
                        break
            elif 'BOM状态' in c:
                for j in range(i + 1, len(cols)):
                    v = cols[j].strip()
                    if v:
                        metadata['status'] = v
                        break
    return metadata


# ==================== 数据行解析 ====================

def _is_ref_continuation_line(line, cols=None):
    """判断是否为位号延续行：非表头/标题/数据行/ALT行，仅含位号标记的后续行"""
    if not line.strip():
        return False
    if is_header_line(line) or is_column_title_line(line):
        return False
    if cols is None:
        cols = line.split('\t')
    if is_title_row(cols):
        return False
    if is_data_row(cols):
        return False
    # 排除 #ALT# 替代物料行，避免替代物料 PN 被误收为位号
    if cols[0].strip().startswith('#ALT#'):
        return False
    # 排除签名占位符行（页脚: 批 准: __ 审 核: __ 拟 制: __）
    line_joined = '\t'.join(cols)
    if re.search(r'(?:批\s*准|审\s*核|拟\s*制)[:：]', line_joined):
        return False
    return any(c.strip() for c in cols)


def _collect_continuation_refs(lines, start_idx):
    """从 start_idx 开始收集位号延续行中的位号，直到下一个数据行/标题行/EOF"""
    refs = []
    for k in range(start_idx, len(lines)):
        line = lines[k].rstrip('\n\r')
        if not line.strip():
            continue
        if is_header_line(line) or is_column_title_line(line):
            continue
        cols = line.split('\t')
        if is_title_row(cols) or is_data_row(cols):
            break
        if not _is_ref_continuation_line(line, cols):
            break
        for v in cols:
            v = v.strip()
            if v and v != '#':
                refs.append(v)
    return refs


def detect_sap_layout(lines):
    """Detect SAP BOM column layout variant by scanning first N data rows.

    Returns 0 for wide format (quantity at col[17]), -2 for narrow format (quantity at col[15]).
    """
    narrow_votes = 0
    wide_votes = 0
    checked = 0
    for line in lines:
        cols = line.split('\t')
        if not is_data_row(cols):
            continue
        q15 = cols[15].strip() if len(cols) > 15 else ''
        q17 = cols[17].strip() if len(cols) > 17 else ''
        n15 = False
        n17 = False
        try:
            if q15:
                float(q15)
                n15 = True
        except ValueError:
            pass
        try:
            if q17:
                float(q17)
                n17 = True
        except ValueError:
            pass
        if n15 and not n17:
            narrow_votes += 1
        elif n17 and not n15:
            wide_votes += 1
        checked += 1
        if checked >= 20:
            break
    if narrow_votes > wide_votes:
        return -2
    return 0


def extract_data_row(cols, offset=0):
    """从数据行提取核心字段（不含level和parent_pn，后续赋值）。

    offset=0  : 宽列格式（col[17]=quantity, col[19]=priority, col[22]=ECN, col[24]=unit）
    offset=-2 : 窄列格式（col[15]=quantity, col[17]=priority, col[20]=ECN, col[22]=unit）
    """
    def get(idx, default=''):
        return cols[idx].strip() if idx < len(cols) else default

    has_expand_marker = get(1) == '#'

    # 合并位号列：宽列从 col[25] 开始，窄列从 col[23] 开始
    ref_start = 25 + offset
    references = []
    for idx in range(ref_start, len(cols)):
        v = cols[idx].strip()
        if v and v != '#':
            references.append(v)

    unit = get(24 + offset, '')

    # 修复：某些 SAP 导出格式中 unit 列为空，单位值混入位号列的首位
    COMMON_UNITS = {'ST', 'PC', 'PCS', 'EA', 'M', 'MM', 'CM', 'G', 'KG', 'L', 'ML'}
    if not unit and references:
        first = references[0].strip().upper()
        if first in COMMON_UNITS:
            unit = references.pop(0)

    # 用量：宽列 col[17]/col[18]，窄列 col[15]/col[16]
    qty_idx = 17 + offset
    qty_idx2 = 18 + offset
    raw_qty = get(qty_idx) or get(qty_idx2) or '0'

    ecn_idx = 22 + offset
    priority_idx = 19 + offset

    return {
        'part_number': get(2, ''),
        'part_name': get(10, '') or get(9, ''),
        'quantity': float(raw_qty),
        'unit': unit,
        'priority': get(priority_idx, ''),
        'ecn': get(ecn_idx, ''),
        'reference': ' '.join(references),
        'has_expand_marker': has_expand_marker,
    }


# ==================== 清洗名称 ====================

def clean_part_name(name):
    """清洗物料名称：去除尾部多余的系统标记"""
    if not name:
        return name
    name = re.sub(r'\s+[NY]\s*$', '', name)
    name = re.sub(r'\s+[A-Z]\d{6}-\d{6}-\d{2,3}\w*\s*(贴片|通用|专用)\s*$', '', name)
    name = re.sub(r'\s+[A-Z]\s*$', '', name)
    name = re.sub(r'\s{2,}', ' ', name).strip()
    return name


# ==================== Section 构建与层级推导 ====================

def build_sections(lines, root_bom):
    """
    第一遍扫描：收集所有子件展开标题行，建立section映射。
    返回 sections: [(title_pn, children_pn_list), ...]
    """
    title_positions = []  # [(line_index, pn)]

    for i, line in enumerate(lines):
        cols = line.split('\t')
        if not any(c.strip() for c in cols):
            continue
        if is_header_line(line) or is_column_title_line(line):
            continue
        if is_title_row(cols, root_bom):
            title_positions.append((i, cols[0].strip()))

    # 对每个section（相邻标题行之间），收集其中的数据行PN
    sections = []
    for idx in range(len(title_positions)):
        _, title_pn = title_positions[idx]
        start = title_positions[idx][0] + 1
        end = title_positions[idx + 1][0] if idx + 1 < len(title_positions) else len(lines)

        children = []
        for i in range(start, end):
            if i >= len(lines):
                break
            cols = lines[i].split('\t')
            if not any(c.strip() for c in cols):
                continue
            if is_header_line(lines[i]) or is_column_title_line(lines[i]):
                continue
            if is_data_row(cols):
                children.append(cols[2].strip())

        sections.append((title_pn, children))

    return sections


def derive_hierarchy(sections, root_bom):
    """
    推导BOM层级。
    返回 pn_level: {pn: level} 和 pn_parent: {pn: parent_pn}

    逻辑:
    - Root BOM的第一个section列表中的标题PN → L1（parent=root_bom）
    - 如果L1 section的children中有PN拥有自己的section → 那些PN是L2
    - 如果L2 section的children中有PN拥有自己的section → 那些PN是L3
    - 依此类推
    """
    # 建立标题PN集合（哪些PN有自己的section）
    title_pn_set = set(pn for pn, _ in sections)
    # 建立 section_children: {title_pn: [child_pn_list]}
    section_children = {pn: children for pn, children in sections}

    # Root BOM下的L1直接子件 = 所有section的title PN
    # Root BOM本身的展开标题行在文件最前面，其children就是L1
    # 找到root_bom对应的section
    root_children = None
    for pn, children in sections:
        if pn == root_bom:
            root_children = children
            break

    if root_children is None:
        # Fallback: root_bom 没有独立 section 时，所有 section 标题的子件作为 L1
        root_children = []
        for title_pn, children in sections:
            for child_pn in children:
                if child_pn not in pn_level:
                    pn_level[child_pn] = 1
                    pn_parent[child_pn] = root_bom
            root_children.extend(children)

    pn_level = {}
    pn_parent = {}

    # L1: root_bom的直接子件 = root section中的children
    for child_pn in root_children:
        if child_pn not in pn_level:
            pn_level[child_pn] = 1
            pn_parent[child_pn] = root_bom

    # BFS推导更深层级
    # 如果某个PN在某个section的children中，且该PN自己有section，
    # 那么该PN的层级 = 当前section的层级 + 1
    queue = [pn for pn, lvl in pn_level.items() if pn in title_pn_set]
    while queue:
        parent_pn = queue.pop(0)
        parent_level = pn_level[parent_pn]
        # 找到parent_pn的section children
        if parent_pn in section_children:
            for child_pn in section_children[parent_pn]:
                if child_pn not in pn_level:
                    pn_level[child_pn] = parent_level + 1
                    pn_parent[child_pn] = parent_pn
                    if child_pn in title_pn_set:
                        queue.append(child_pn)

    return pn_level, pn_parent


# ==================== 主解析器 ====================

def parse_bom_clean(file_path):
    """
    SAP BOM 展开表完整清洗流程 v3.0。
    返回 (metadata, items) — items 为有序的物料行列表。
    """
    with open(file_path, 'rb') as f:
        raw = f.read()

    # Try multiple encodings (UTF-16 LE first, then GBK/GB18030 for Chinese locale SAP exports)
    text = None
    for enc in ['utf-16-le', 'gbk', 'gb18030', 'gb2312', 'utf-8-sig']:
        try:
            text = raw.decode(enc)
            if 'B O M 展开表' in text or 'BOM展开表' in text:
                break  # Found SAP BOM markers, this is the correct encoding
        except (UnicodeDecodeError, UnicodeError):
            continue
    
    if text is None:
        # Fallback: try utf-16-le with replace errors
        try:
            text = raw.decode('utf-16-le')
        except UnicodeDecodeError:
            text = raw.decode('utf-8-sig', errors='replace')

    if 'B O M 展开表' not in text and 'BOM展开表' not in text:
        raise ValueError('不是 SAP BOM 展开表格式文件')

    lines = text.split('\n')
    offset = detect_sap_layout(lines)
    metadata = extract_metadata(lines)
    root_bom = metadata.get('bom_number', '')

    # ---- 第一步：构建section并推导层级 ----
    sections = build_sections(lines, root_bom)
    pn_level, pn_parent = derive_hierarchy(sections, root_bom)

    # ---- 第二步：遍历数据行，赋值level和parent_pn ----
    items = []
    current_section_pn = root_bom  # 当前section的标题PN
    i = 0
    while i < len(lines):
        line = lines[i]
        cols = line.split('\t')
        i += 1

        if not any(c.strip() for c in cols):
            continue
        if is_header_line(line) or is_column_title_line(line):
            continue

        # 子件展开标题行 → 切换当前section
        if is_title_row(cols, root_bom):
            current_section_pn = cols[0].strip()
            continue

        # 位号延续行（由数据行收尾逻辑处理）
        if _is_ref_continuation_line(line, cols):
            continue

        # 有效数据行
        if is_data_row(cols):
            row = extract_data_row(cols, offset)
            part_pn = row['part_number']

            # level: 从hierarchy查找，找不到则用当前section的层级+1
            if part_pn in pn_level:
                row['level'] = pn_level[part_pn]
            else:
                # 没在hierarchy中的子件 → 当前section的层级+1
                parent_level = pn_level.get(current_section_pn, 0)
                row['level'] = parent_level + 1

            # parent_pn: 直接就是当前section的标题PN
            row['parent_pn'] = current_section_pn

            # 清洗名称
            row['part_name'] = clean_part_name(row['part_name'])

            # 收集位号延续行中的额外位号
            continuation_refs = _collect_continuation_refs(lines, i)
            if continuation_refs:
                existing_refs = row['reference'].split() if row['reference'] else []
                existing_refs.extend(continuation_refs)
                row['reference'] = ' '.join(existing_refs)

            items.append(row)

    return metadata, items


def clean_bom_data(file_path):
    """
    SAP BOM 展开表完整清洗流程 v3.0，额外返回统计数据。
    返回 (metadata, items, stats)
    """
    with open(file_path, 'rb') as f:
        raw = f.read()

    # Try multiple encodings (UTF-16 LE first, then GBK/GB18030 for Chinese locale SAP exports)
    text = None
    for enc in ['utf-16-le', 'gbk', 'gb18030', 'gb2312', 'utf-8-sig']:
        try:
            text = raw.decode(enc)
            if 'B O M 展开表' in text or 'BOM展开表' in text:
                break  # Found SAP BOM markers, this is the correct encoding
        except (UnicodeDecodeError, UnicodeError):
            continue
    
    if text is None:
        # Fallback: try utf-16-le with replace errors
        try:
            text = raw.decode('utf-16-le')
        except UnicodeDecodeError:
            text = raw.decode('utf-8-sig', errors='replace')

    if 'B O M 展开表' not in text and 'BOM展开表' not in text:
        raise ValueError('不是 SAP BOM 展开表格式文件')

    lines = text.split('\n')
    offset = detect_sap_layout(lines)
    metadata = extract_metadata(lines)
    root_bom = metadata.get('bom_number', '')

    sections = build_sections(lines, root_bom)
    pn_level, pn_parent = derive_hierarchy(sections, root_bom)

    items = []
    current_section_pn = root_bom
    i = 0
    while i < len(lines):
        line = lines[i]
        cols = line.split('\t')
        i += 1

        if not any(c.strip() for c in cols):
            continue
        if is_header_line(line) or is_column_title_line(line):
            continue

        if is_title_row(cols):
            current_section_pn = cols[0].strip()
            continue

        # 位号延续行（由数据行收尾逻辑处理）
        if _is_ref_continuation_line(line, cols):
            continue

        if is_data_row(cols):
            row = extract_data_row(cols, offset)
            part_pn = row['part_number']

            if part_pn in pn_level:
                row['level'] = pn_level[part_pn]
            else:
                parent_level = pn_level.get(current_section_pn, 0)
                row['level'] = parent_level + 1

            row['parent_pn'] = current_section_pn
            row['part_name'] = clean_part_name(row['part_name'])

            # 收集位号延续行中的额外位号
            continuation_refs = _collect_continuation_refs(lines, i)
            if continuation_refs:
                existing_refs = row['reference'].split() if row['reference'] else []
                existing_refs.extend(continuation_refs)
                row['reference'] = ' '.join(existing_refs)

            items.append(row)

    # 校验层级完整性
    orphans = validate_hierarchy(items, root_bom)
    if orphans:
        import logging
        logging.getLogger('bom_cleaner').warning(
            '发现 %d 条孤立记录无法追溯到根 BOM: %s',
            len(orphans),
            ', '.join(o['part_number'] for o in orphans[:10])
        )

    # ---- 统计数据 ----
    total_rows = len(items)
    unique_pns = set(it['part_number'] for it in items)
    unique_count = len(unique_pns)

    level_dist = {}
    for it in items:
        lv = it['level']
        level_dist[lv] = level_dist.get(lv, 0) + 1
    level_dist = dict(sorted(level_dist.items()))

    pn_counts = {}
    for it in items:
        pn_counts[it['part_number']] = pn_counts.get(it['part_number'], 0) + 1
    dup_pns = {k: v for k, v in pn_counts.items() if v > 1}
    dup_count = len(dup_pns)

    preview = []
    for it in items[:10]:
        preview.append({
            'level': it['level'],
            'part_number': it['part_number'],
            'part_name': it['part_name'],
            'quantity': it['quantity'],
            'unit': it['unit'],
            'parent_pn': it.get('parent_pn', ''),
        })

    stats = {
        'total_rows': total_rows,
        'unique_count': unique_count,
        'level_dist': level_dist,
        'dup_count': dup_count,
        'dup_pns': list(dup_pns.keys())[:20],
        'preview': preview,
    }

    return metadata, items, stats


# ==================== 层级校验 ====================

def validate_hierarchy(items, root_bom):
    """校验所有子件都能通过 parent_pn 链追溯到根 BOM。
    
    Args:
        items: 物料行列表，每行包含 part_number 和 parent_pn
        root_bom: 根 BOM 编号
    
    Returns:
        孤立记录列表（无法追溯到根 BOM 的记录）
    """
    parent_map = {it['part_number']: it.get('parent_pn', '') for it in items}
    orphans = []
    for it in items:
        pn = it['part_number']
        visited = set()
        current = parent_map.get(pn, '')
        # 跳过根 BOM 自身
        if pn == root_bom:
            continue
        # 沿 parent_pn 链向上追溯
        while current and current != root_bom:
            if current in visited:
                orphans.append({'part_number': pn, 'reason': '循环引用'})
                break
            visited.add(current)
            current = parent_map.get(current, '')
        if not current and pn != root_bom:
            orphans.append({'part_number': pn, 'reason': '无法追溯到根 BOM'})
    return orphans


def parse_bom_clean(file_path):
    """
    SAP BOM 展开表完整清洗流程 v3.0（兼容旧接口）。
    返回 (metadata, items)。
    """
    metadata, items, _ = clean_bom_data(file_path)
    return metadata, items


# ==================== 测试输出 ====================

def print_result(metadata, items, label=''):
    """格式化打印解析结果"""
    print(f'=== {label} ===')
    print(f'BOM号: {metadata.get("bom_number")}')
    print(f'ECN: {metadata.get("ecn")}')
    print(f'有效日期: {metadata.get("valid_date")}')
    print(f'工厂: {metadata.get("plant")}')
    print(f'状态: {metadata.get("status")}')
    print(f'总物料行数: {len(items)}')

    if not items:
        print('  (无数据)')
        return

    max_level = max(it['level'] for it in items)
    root_items = [it for it in items if it['parent_pn'] == metadata.get('bom_number', '')]
    unique_pns = set(it['part_number'] for it in items)

    print(f'最大层级: {max_level}')
    print(f'根节点（顶层子件）: {len(root_items)}种')
    print(f'物料种类（去重）: {len(unique_pns)}')

    # 各层级统计
    level_counts = {}
    for it in items:
        lv = it['level']
        level_counts[lv] = level_counts.get(lv, 0) + 1
    print(f'层级分布: {dict(sorted(level_counts.items()))}')

    print(f'\n--- 前30行数据 ---')
    print(f'{"层级":<5} {"物料号":<26} {"物料名称":<42} {"用量":>6} {"单位":<3} {"父件":<26} {"位号"}')
    print('-' * 150)
    for it in items[:30]:
        name = it['part_name'][:41].ljust(41)
        parent = (it['parent_pn'] or '-')[:25].ljust(25)
        ref = it['reference'][:15]
        print(f'{it["level"]:<5} {it["part_number"]:<25} {name} {it["quantity"]:>6.0f} {it["unit"]:<3} {parent} {ref}')

    # 统计重复
    pn_counts = {}
    for it in items:
        pn_counts[it['part_number']] = pn_counts.get(it['part_number'], 0) + 1
    dup_pns = {k: v for k, v in pn_counts.items() if v > 1}
    if dup_pns:
        print(f'\n--- 重复物料（同PN多处使用）: {len(dup_pns)}种 ---')
        sorted_dups = sorted(dup_pns.items(), key=lambda x: -x[1])
        for pn, cnt in sorted_dups[:10]:
            sample_name = next((it['part_name'] for it in items if it['part_number'] == pn), '')
            print(f'  {pn:<25} 出现{cnt}次  {sample_name[:40]}')
        if len(sorted_dups) > 10:
            print(f'  ... 共{len(sorted_dups)}种')


def main():
    base = Path(__file__).resolve().parent.parent
    files = ['1.xls', '2.xls']

    for fname in files:
        fpath = base / fname
        if not fpath.exists():
            fpath = Path(fname)
            if not fpath.exists():
                print(f'{fname} 不存在，跳过')
                continue

        metadata, items = parse_bom_clean(str(fpath))
        print_result(metadata, items, label=fname)
        print('\n' + '=' * 80 + '\n')


# ==================== PLM CSV 格式解析 ====================

# PLM CSV 特征列名
PLM_CSV_HEADERS = {'状态', '编号', '中文长描述', '数量', '位号', '单位', '物料组'}


def is_plm_csv(file_path):
    """检测文件是否为 PLM 导出的 CSV BOM 格式"""
    if not file_path.lower().endswith('.csv'):
        return False
    try:
        import csv
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            headers = [h.strip() for h in next(reader, [])]
            matched = PLM_CSV_HEADERS & set(headers)
            return len(matched) >= 5
    except Exception:
        return False


def clean_plm_csv_data(file_path):
    """解析 PLM CSV BOM 文件，输出与 clean_bom_data 相同格式。

    格式: CSV (UTF-8 BOM), 14 列，第一行标题，第二行根机型

    层级: CSV 为扁平清单不含父子关系语义，全部物料为 L1（平铺在根节点下）。
          位号展开为空格分隔，单位映射为 PC。
    """
    import csv
    import re

    with open(file_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        rows = list(reader)

    if len(rows) < 2:
        raise ValueError('CSV 文件行数不足，至少需要标题行+机型行')

    # 机型行 (row 1): MODEL_STAGE, P1C85P3HXX7T611000, ...
    root_pn = (rows[1][1] or '').strip()
    root_name = (rows[1][2] or '').strip()
    if not root_pn:
        raise ValueError('CSV 第2行未找到机型编号')

    metadata = {
        'bom_number': root_pn,
        'bom_name': root_name,
        'ecn': '',
        'valid_date': '',
        'status': '',
        'plant': '',
    }

    def _expand_ref(ref_text):
        if not ref_text:
            return ''
        result = []
        for part in ref_text.split(','):
            part = part.strip()
            if not part:
                continue
            m = re.match(r'^(.+?)(\d+)-(.+?)(\d+)$', part)
            if m and m.group(1) == m.group(3):
                base = m.group(1)
                start = int(m.group(2))
                end = int(m.group(4))
                for n in range(start, end + 1):
                    result.append(f'{base}{n}')
                continue
            result.append(part)
        return ' '.join(result)

    items = []

    for i in range(2, len(rows)):
        row = rows[i]
        if len(row) < 3:
            continue

        stage = (row[0] or '').strip()
        pn = (row[1] or '').strip()
        name = (row[2] or '').strip()
        qty_str = (row[3] or '').strip()
        ref = _expand_ref((row[4] or '').strip())
        unit_raw = (row[11] or '').strip() if len(row) > 11 else 'PC'
        unit = unit_raw if unit_raw not in ('', '是', '否', '其他') else 'PC'

        if not pn:
            continue

        try:
            qty = float(qty_str) if qty_str else 1.0
        except ValueError:
            qty = 1.0

        # 全平铺为 L1，无父组件
        items.append({
            'level': 1,
            'parent_pn': '',
            'part_number': pn,
            'part_name': name,
            'quantity': qty,
            'unit': unit,
            'reference': ref,
            'ecn': '',
            'priority': '',
        })

    return metadata, items, {'total': len(items)}


if __name__ == '__main__':
    main()
