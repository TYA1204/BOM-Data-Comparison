"""
整机清机更改通知单 — 自动填表工具
直接操作模版表格，在「更改内容」行后插入差异明细行
"""

import os
import sqlite3
from datetime import datetime

from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH


# ── Constants ──────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE_PATH = os.path.join(PROJECT_ROOT, '整机清机更改通知单.docx')
OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'reports')

DIFF_LABELS = {'added': '新增', 'removed': '删除', 'modified': '变更'}

# ── Colors ──────────────────────────────────────────────────
GREEN      = '16A34A'
RED        = 'DC2626'
AMBER      = 'CA8A04'
GREEN_BG   = 'DCFCE7'
RED_BG     = 'FEF2F2'
AMBER_BG   = 'FEF9C3'
HEADER_BG  = '1E40AF'
HEADER_FG  = 'FFFFFF'
BLACK      = '333333'
GRAY       = '999999'


# ── Data Helpers ───────────────────────────────────────────

def _g(d, key, default=None):
    try:
        return d[key]
    except (KeyError, IndexError):
        return default


def clean_material_name(name):
    """Strip redundant brand/certification/metadata, keep core name + model.

    Rules (in order):
      1. Remove parenthesized content (round & full-width brackets)
      2. Remove exact-match noise tokens (brand, certification, region, status)
      3. Remove pattern-matching noise (dimensions, temperature, internal PN)
      4. Remove trailing status tokens (0,1,2,3,A,B,C,BC)
      5. Collapse multiple spaces
    """
    import re

    if not name:
        return ""

    # Step 1: strip parenthesized content (replace with space to avoid word merge)
    name = re.sub(r"[（【].*[）】]", " ", name)
    name = re.sub(r"\([^)]*\)", " ", name)

    tokens = name.split()

    # Step 2: exact-match noise tokens
    NOISE_EXACT = {
        # Certifications
        "RoHS", "REACH", "CBU",
        # Brands
        "SKYWORTH", "coocaa", "COOCAA",
        "HKC", "WANZHONG", "wanzhong", "WZ",
        # Regions / status
        "内销", "通用", "中国", "无", "非生产打印",
        # Misc
        "彩色印刷", "CARTONBOX", "NEW", "EMMC", "64G",
        "Shenzhen", "CHUANGWEI-RGB", "Electro",
        "深圳创维RGB电子", "High-Performance",
        "陶瓷基板", "双面", "同面", "非高频膜",
        "2Point", "80g",
        # Platform codes
        "8R713", "8R710",
        # trailing status codes (also handled in step 4, belt & suspenders)
        "NMN", "L1", "A", "B", "C", "BC",
    }

    # Step 3: pattern-based noise
    def _is_noise_pattern(t):
        if re.fullmatch(r"\d+mm", t):
            return True
        if re.fullmatch(r"\d+\.\d+mm", t):
            return True
        if re.fullmatch(r"[-~]?\d+~?\d*℃?", t):
            return True
        if re.fullmatch(r"N\d{6}-\d{6}-\d{3}", t):
            return True
        if re.fullmatch(r"WZ\.\d+.*", t):
            return True
        if re.fullmatch(r"N\d{11,}", t):
            return True
        # tokens starting with underscore (leftover from paren removal)
        if t.startswith('_'):
            return True
        # internal model codes: all-caps with UNDERSCORES (not dash)
        if re.fullmatch(r"[A-Z0-9_.-]{6,}", t) and '_' in t:
            return True
        return False

    cleaned = []
    for t in tokens:
        if t in NOISE_EXACT:
            continue
        if _is_noise_pattern(t):
            continue
        # strip 8Rxxx- prefix (e.g. 8R713-100P5FP -> 100P5FP)
        t = re.sub(r"^8R71[03]-", "", t)
        cleaned.append(t)

    # Step 4: remove trailing status tokens (handles 0. 1. etc.)
    TRAILING_NOISE = {'0', '1', '2', '3', 'A', 'B', 'C', 'BC'}
    while cleaned:
        last = cleaned[-1].rstrip('.')
        if last in TRAILING_NOISE:
            cleaned.pop()
        else:
            break

    result = " ".join(cleaned).strip()
    result = re.sub(r" +", " ", result)
    return result



def get_diff_rows(conn, task_id):
    """Get all diff rows for the task, ready for table insertion."""
    diffs = conn.execute(
        '''SELECT * FROM comparison_result WHERE task_id=?
           ORDER BY CASE diff_type
               WHEN 'added' THEN 1 WHEN 'removed' THEN 2 ELSE 3
           END, COALESCE(parent_pn_a, parent_pn_b)''',
        (task_id,)
    ).fetchall()

    # Build parent_name lookup from bom_item table
    parent_lookup = {}
    all_parent_pns = set()
    for d in diffs:
        pa = (_g(d, 'parent_pn_a') or '').strip()
        pb = (_g(d, 'parent_pn_b') or '').strip()
        if pa: all_parent_pns.add(pa)
        if pb: all_parent_pns.add(pb)
    if all_parent_pns:
        placeholders = ','.join(['?' for _ in all_parent_pns])
        pns = conn.execute(
            f'SELECT part_number, part_name FROM bom_item WHERE part_number IN ({placeholders})',
            list(all_parent_pns)
        ).fetchall()
        for pn_row in pns:
            raw_name = (pn_row['part_name'] or '部件').strip()
            parent_lookup[pn_row['part_number']] = clean_material_name(raw_name)

    rows = []
    for d in diffs:
        dt = d['diff_type']
        dc = d['diff_category']

        if dt == 'added':
            pn = (_g(d, 'part_number_b') or '').strip()
            nm = clean_material_name((_g(d, 'part_name_b') or '').strip())
            qty = _g(d, 'quantity_b', 1)
            qty_str = str(int(qty)) if qty is not None else '1'
            parent_pn = (_g(d, 'parent_pn_b') or '').strip()
            parent_name = parent_lookup.get(parent_pn, '') if parent_pn else ''
            type_label = 'ADD'
        elif dt == 'removed':
            pn = (_g(d, 'part_number_a') or '').strip()
            nm = clean_material_name((_g(d, 'part_name_a') or '').strip())
            qty = _g(d, 'quantity_a', 1)
            qty_str = str(int(qty)) if qty is not None else '1'
            parent_pn = (_g(d, 'parent_pn_a') or '').strip()
            parent_name = parent_lookup.get(parent_pn, '') if parent_pn else ''
            type_label = 'DEL'
        else:
            pn = (_g(d, 'part_number_a') or _g(d, 'part_number_b') or '').strip()
            nm = clean_material_name((_g(d, 'part_name_a') or _g(d, 'part_name_b') or '').strip())
            old_qty = str(_g(d, 'old_value', '')).strip()
            new_qty = str(_g(d, 'new_value', '')).strip()
            parent_pn = (_g(d, 'parent_pn_a') or _g(d, 'parent_pn_b') or '').strip()
            parent_name = parent_lookup.get(parent_pn, '') if parent_pn else ''
            type_label = 'MOD'

        row_data = {
            'pn': pn, 'name': nm,
            'parent_pn': parent_pn,
            'parent_name': parent_name,
            'type': dt, 'type_label': type_label,
        }
        if dt == 'modified':
            row_data['old_qty'] = old_qty
            row_data['new_qty'] = new_qty
        else:
            row_data['qty'] = qty_str
        rows.append(row_data)
    return rows


def group_diffs_by_parent(diff_rows):
    """Group diff rows by parent component, preserving ADD and DEL sub-groups.

    Filters out intermediate assembly parents — only shows components whose
    items are actual leaf parts (not sub-assemblies referenced as parents elsewhere).
    """
    # Collect all parent_pn values to identify intermediate assemblies
    all_parents = set()
    for dr in diff_rows:
        pp = dr['parent_pn']
        if pp:
            all_parents.add(pp)

    groups = {}
    for dr in diff_rows:
        pk = dr['parent_pn'] or '__UNKNOWN__'
        if pk not in groups:
            groups[pk] = {
                'parent_pn': dr['parent_pn'],
                'parent_name': dr['parent_name'],
                'adds': [], 'dels': [], 'mods': [],
            }
        if dr['type'] == 'added':
            groups[pk]['adds'].append(dr)
        elif dr['type'] == 'removed':
            groups[pk]['dels'].append(dr)
        else:
            groups[pk]['mods'].append(dr)

    # Filter: keep groups where at least one item is NOT an intermediate parent
    result = []
    for pk, g in groups.items():
        all_items = g['adds'] + g['dels'] + g['mods']
        # If ALL items are themselves parent components, this is an intermediate assembly
        if all_items and all(item['pn'] in all_parents for item in all_items):
            continue
        if all_items:
            result.append(g)

    # ── Merge groups with the same non-empty component name ──
    # Use first word of parent_name as matching key (e.g., "包装组件")
    merged = {}
    for g in result:
        full_name = (g['parent_name'] or '').strip()
        pn = g['parent_pn'] or ''
        # Extract short name for matching
        short_name = full_name.split()[0] if full_name else full_name

        if not short_name:
            merged[pn] = g.copy()
            merged[pn]['merges'] = [pn]
            continue

        key = short_name
        if key not in merged:
            merged[key] = g.copy()
            merged[key]['merges'] = [pn]
            merged[key]['short_name'] = short_name
        else:
            mg = merged[key]
            mg['adds'].extend(g['adds'])
            mg['dels'].extend(g['dels'])
            mg['mods'].extend(g['mods'])
            mg['merges'].append(pn)
            if not mg['parent_pn'] or (pn and pn > mg['parent_pn']):
                mg['parent_pn'] = pn
                mg['parent_name'] = full_name

    # Filter: only keep groups with a real parent_pn
    result_groups = [g for g in merged.values() if g.get('parent_pn') and g['parent_pn'] != '__UNKNOWN__']
    return sorted(result_groups, key=lambda g: g['parent_pn'])


# ── Word Generation ────────────────────────────────────────


def _ensure_template():
    """如果模板文件不存在，自动创建一个基础模板。"""
    if os.path.exists(TEMPLATE_PATH):
        return
    doc = Document()
    # 段落0: 表单编号
    p0 = doc.add_paragraph()
    p0.add_run('SKY-RKZXS-07')
    # 段落1: 标题
    p1 = doc.add_paragraph()
    p1.add_run('更 改 通 知 单')
    # 表格: 6行 x 10列
    table = doc.add_table(rows=6, cols=10)
    # 第3行第1列作为更改内容区域
    # （模板填充逻辑依赖此结构）
    os.makedirs(os.path.dirname(TEMPLATE_PATH), exist_ok=True)
    doc.save(TEMPLATE_PATH)


def generate_change_notice(task_id: int, output_name: str = None, db_path: str = None):
    """Generate change notice by filling the official template."""
    _ensure_template()  # 确保模板存在
    if db_path is None:
        db_path = os.path.join(PROJECT_ROOT, 'data', 'bom_compare.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    task = conn.execute('SELECT * FROM comparison_task WHERE id=?', (task_id,)).fetchone()
    if not task:
        raise ValueError(f'Task #{task_id} not found')

    src_name = conn.execute('SELECT bom_name FROM bom_header WHERE id=?', (task['source_bom_id'],)).fetchone()
    tgt_name = conn.execute('SELECT bom_name FROM bom_header WHERE id=?', (task['target_bom_id'],)).fetchone()
    src_label = src_name['bom_name'] if src_name else 'N/A'
    tgt_label = tgt_name['bom_name'] if tgt_name else 'N/A'

    # Extract short model names
    src_short = _extract_model(src_label)
    tgt_short = _extract_model(tgt_label)
    machine_core = _extract_core(src_label)

    diff_rows = get_diff_rows(conn, task_id)
    conn.close()

    today_str = datetime.now().strftime('%Y-%-m-%-d') if os.name != 'nt' else datetime.now().strftime('%Y/%#m/%#d')

    # ── Load template ──
    doc = Document(TEMPLATE_PATH)

    # ── Update header paragraphs ──
    # P0: form number
    _clear_paragraph_runs(doc.paragraphs[0])
    run = doc.paragraphs[0].add_run('SKY-RKZXS-07')
    run.font.size = Pt(12)
    run.font.name = '宋体'

    # P1: title
    _clear_paragraph_runs(doc.paragraphs[1])
    run = doc.paragraphs[1].add_run('更 改 通 知 单')
    run.font.size = Pt(22)
    run.font.bold = True
    run.font.name = '宋体'
    run.font.color.rgb = RGBColor(0x1E, 0x40, 0xAF)
    doc.paragraphs[1].alignment = WD_ALIGN_PARAGRAPH.CENTER

    # ── Fill table cells ──
    table = doc.tables[0]
    _fill_template(table, machine_core, today_str, src_short, tgt_short, diff_rows)

    # ── Save ──
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if output_name is None:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_name = f'整机清机更改通知单_{src_short}_vs_{tgt_short}_{ts}'
    output_path = os.path.join(OUTPUT_DIR, f'{output_name}.docx')
    doc.save(output_path)
    return output_path


def _clear_paragraph_runs(para):
    """Remove all runs from a paragraph."""
    for r in para.runs:
        r._element.getparent().remove(r._element)


def _set_cell_text(cell, text):
    """Set value cell text, matching template style (宋体 14pt centered)."""
    for p in cell.paragraphs:
        for r in p.runs:
            r._element.getparent().remove(r._element)

    if cell.paragraphs:
        p = cell.paragraphs[0]
    else:
        p = cell.add_paragraph()

    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(text)
    run.font.size = Pt(14)
    run.font.name = '宋体'


def _fill_template(table, machine_core, today_str, src_short, tgt_short, diff_rows):
    """Fill the official template table with comparison data.

    Template structure (10-column grid, physical cells via gridSpan):
      Row 0-2: [span3-label, span3-dup, span3-dup, span2-value, span2-dup,
                span1-sep, span2-label, span2-dup, span2-value, span2-dup]
      Row 3-4: [narrow-label(span1), content(span9), dup×8]
      Row 5:   [拟制(span2), dup, 签名(span2), dup, sep, sep+dup, sep+dup,
                审核(span2), dup, 签名]

    Value cell indices (use first cell of each span group):
      Row0: [3]=机芯, [8]=日期
      Row1: [3]=机型, [8]=订单号
      Row2: [3]=阶段, [8]=数量
      Row3: [1]=更改内容 area
      Row4: [1]=说明 (keep as-is)
      Row5: [2]=拟制签名, [9]=审核签名 (keep as-is)
    """
    # ── Quantity formatter: show as int if whole number ──
    def _fmt_qty(q):
        try:
            v = float(q)
            return str(int(v)) if v == int(v) else str(v)
        except (TypeError, ValueError):
            return str(q) if q else '1'

    # ── Row 0: 机芯 + 日期 ──
    _set_cell_text(table.rows[0].cells[3], machine_core)
    _set_cell_text(table.rows[0].cells[8], today_str)

    # ── Row 1: 机型 + 订单号 ──
    _set_cell_text(table.rows[1].cells[3], tgt_short)
    _set_cell_text(table.rows[1].cells[8], '2606002KL')

    # ── Row 2: 阶段 + 数量 ──
    _set_cell_text(table.rows[2].cells[3], 'DVT')
    _set_cell_text(table.rows[2].cells[8], '1')

    # ── Row 3: 更改内容 ──
    content_cell = table.rows[3].cells[1]

    # Clear existing empty paragraphs
    for p in content_cell.paragraphs:
        for r in p.runs:
            r._element.getparent().remove(r._element)

    # Group diffs by parent component
    groups = group_diffs_by_parent(diff_rows)

    # Reuse first paragraph as spacer
    if content_cell.paragraphs:
        p0 = content_cell.paragraphs[0]
    else:
        p0 = content_cell.add_paragraph()
    p0.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = p0.add_run('')
    run.font.size = Pt(4)

    for g in groups:
        if not (g['adds'] or g['dels'] or g['mods']):
            continue

        # Component header: "在N030105-019050-001……面壳组件里"
        comp_pn = g.get('parent_pn', '')
        if not comp_pn or comp_pn == '__UNKNOWN__':
            continue
        comp_name = g.get('short_name', g['parent_name'])
        header_text = f'在{comp_pn}\u2026\u2026{comp_name}里'

        p = content_cell.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        run = p.add_run(header_text)
        run.font.size = Pt(11)
        run.font.bold = True
        run.font.name = '宋体'
        run.font.color.rgb = RGBColor(0x1E, 0x40, 0xAF)

        # ADD items
        for i, item in enumerate(g['adds']):
            qty = _fmt_qty(item['qty'])
            text = f'{item["pn"]}\u00b7\u00b7{item["name"]}\u00b7\u00b7{qty}PC'
            line = f'ADD:{text}' if i == 0 else f'     {text}'
            p = content_cell.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            run = p.add_run(line)
            run.font.size = Pt(10)
            run.font.name = '宋体'
            run.font.color.rgb = RGBColor(0x33, 0x41, 0x55)

        # DEL items
        for i, item in enumerate(g['dels']):
            qty = _fmt_qty(item['qty'])
            text = f'{item["pn"]}\u00b7\u00b7{item["name"]}\u00b7\u00b7{qty}PC'
            line = f'DEL:{text}' if i == 0 else f'     {text}'
            p = content_cell.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            run = p.add_run(line)
            run.font.size = Pt(10)
            run.font.name = '宋体'
            run.font.color.rgb = RGBColor(0x33, 0x41, 0x55)

        # MOD items
        for mi, m in enumerate(g['mods']):
            old_q = _fmt_qty(m.get('old_qty', '1'))
            new_q = _fmt_qty(m.get('new_qty', '1'))
            text = f'{m["pn"]}\u00b7\u00b7{m["name"]}\u00b7\u00b7{old_q}\u2192{new_q}'
            line = f'MOD:{text}' if mi == 0 else f'     {text}'
            p = content_cell.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            run = p.add_run(line)
            run.font.size = Pt(10)
            run.font.name = '宋体'
            run.font.color.rgb = RGBColor(0x33, 0x41, 0x55)

        # Blank line between components
        p = content_cell.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        run = p.add_run('')
        run.font.size = Pt(4)

    # Remove the two extra empty template paragraphs that remain unused
    for p in content_cell.paragraphs:
        if p == p0:
            continue
        text = p.text.strip()
        if not text and all(not r.text.strip() for r in p.runs):
            # Leave one spacer at end
            pass


def _extract_model(bom_name):
    """Extract short model name from BOM name like 'P1C100P3EM8R713001' → '100P3EM'."""
    import re
    m = re.match(r'^P1C(.+?)(\d+R\d+)(\d*)$', bom_name)
    if m:
        return m.group(1)
    m = re.search(r'(\d{3}[A-Z0-9]+)', bom_name)
    return m.group(1) if m else bom_name


def _extract_core(bom_name):
    """Extract machine core like '8R713'."""
    import re
    m = re.search(r'(\d+R\d+)', bom_name)
    return m.group(1) if m else '8R713'


# ── Excel Export ────────────────────────────────────────────

def generate_change_notice_excel(task_id: int, output_name: str = None, db_path: str = None):
    """Generate a professional Excel change notice matching the official form layout."""
    if db_path is None:
        db_path = os.path.join(PROJECT_ROOT, 'data', 'bom_compare.db')
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise ImportError('openpyxl required. pip install openpyxl')

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    task = conn.execute('SELECT * FROM comparison_task WHERE id=?', (task_id,)).fetchone()
    if not task:
        raise ValueError(f'Task #{task_id} not found')
    diff_rows = get_diff_rows(conn, task_id)
    conn.close()

    src_name = _extract_model(diff_rows[0]['pn']) if diff_rows else 'N/A'  # fallback
    # Better: get from DB
    conn2 = sqlite3.connect(db_path); conn2.row_factory = sqlite3.Row
    sn = conn2.execute('SELECT bom_name FROM bom_header WHERE id=?', (task['source_bom_id'],)).fetchone()
    tn = conn2.execute('SELECT bom_name FROM bom_header WHERE id=?', (task['target_bom_id'],)).fetchone()
    conn2.close()
    src_model = _extract_model(sn['bom_name']) if sn else 'SRC'
    tgt_model = _extract_model(tn['bom_name']) if tn else 'TGT'
    machine_core = _extract_core(sn['bom_name']) if sn else '8R713'

    added_c = sum(1 for r in diff_rows if r['type'] == 'added')
    removed_c = sum(1 for r in diff_rows if r['type'] == 'removed')
    modified_c = sum(1 for r in diff_rows if r['type'] == 'modified')
    today_str = datetime.now().strftime('%Y-%-m-%-d') if os.name != 'nt' else datetime.now().strftime('%Y/%#m/%#d')

    # ── Workbook setup ──
    wb = Workbook()
    ws = wb.active
    ws.title = '整机清机更改通知单'

    # Colors
    blue_fill     = PatternFill(start_color='1E40AF', end_color='1E40AF', fill_type='solid')
    white_font    = Font(name='宋体', bold=True, color='FFFFFF', size=10)
    thin_border   = Border(
        left=Side('thin', '999999'), right=Side('thin', '999999'),
        top=Side('thin', '999999'), bottom=Side('thin', '999999'))
    no_border     = Border()
    center_a      = Alignment(horizontal='center', vertical='center', wrap_text=True)
    left_a        = Alignment(horizontal='left', vertical='center', wrap_text=True)
    right_a       = Alignment(horizontal='right', vertical='center')
    title_font    = Font(name='宋体', bold=True, size=16, color='1E40AF')
    subtitle_font = Font(name='宋体', bold=True, size=12)
    info_font     = Font(name='宋体', size=10)
    small_font    = Font(name='宋体', size=9)
    gray_small    = Font(name='宋体', size=8, color='999999')
    bold_font     = Font(name='宋体', bold=True, size=10)
    header_font   = Font(name='宋体', bold=True, color='FFFFFF', size=10)

    green_fill  = PatternFill(start_color='DCFCE7', end_color='DCFCE7', fill_type='solid')
    red_fill    = PatternFill(start_color='FEF2F2', end_color='FEF2F2', fill_type='solid')
    amber_fill  = PatternFill(start_color='FEF9C3', end_color='FEF9C3', fill_type='solid')
    green_font  = Font(name='宋体', color='16A34A', size=9)
    red_font    = Font(name='宋体', color='DC2626', size=9)
    amber_font  = Font(name='宋体', color='CA8A04', size=9)
    light_fill  = PatternFill(start_color='F8FAFC', end_color='F8FAFC', fill_type='solid')

    # ── Page setup ──
    ws.sheet_view.zoomScale = 90
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = 9
    ws.page_margins.left = 0.5
    ws.page_margins.right = 0.5
    ws.page_margins.top = 0.4
    ws.page_margins.bottom = 0.4

    # ── Row 1: Title ──
    ws.merge_cells('A1:H1')
    c = ws['A1']
    c.value = f'SKY-RKZXS-07          整 机 清 机 更 改 通 知 单'
    c.font = title_font
    c.alignment = Alignment(horizontal='left', vertical='center')
    ws.row_dimensions[1].height = 36

    # ── Row 2: Info bar ──
    ws.merge_cells('A2:B2')
    ws['A2'].value = f'机芯：{machine_core}'
    ws['A2'].font = subtitle_font; ws['A2'].alignment = left_a
    ws.merge_cells('C2:D2')
    ws['C2'].value = f'机型：{src_model} → {tgt_model}'
    ws['C2'].font = subtitle_font; ws['C2'].alignment = left_a
    ws.merge_cells('E2:F2')
    ws['E2'].value = f'日期：{today_str}'
    ws['E2'].font = subtitle_font; ws['E2'].alignment = right_a
    ws.merge_cells('G2:H2')
    ws['G2'].value = f'阶段：整机清机'
    ws['G2'].font = subtitle_font; ws['G2'].alignment = right_a
    ws.row_dimensions[2].height = 24

    # ── Row 3: Summary ──
    ws.merge_cells('A3:H3')
    ws['A3'].value = (f'比对汇总：新增 {added_c} 项 | 删除 {removed_c} 项 | 变更 {modified_c} 项 | '
                      f'合计 {len(diff_rows)} 项差异    |    源机型：{sn["bom_name"] if sn else "N/A"}    |    目标机型：{tn["bom_name"] if tn else "N/A"}')
    ws['A3'].font = Font(name='宋体', size=9, italic=True, color='64748B')
    ws['A3'].alignment = left_a
    ws['A3'].fill = light_fill
    ws.row_dimensions[3].height = 22

    # ── Row 4: Blank spacer ──
    ws.row_dimensions[4].height = 6

    # ── Row 5: Column headers ──
    diff_headers = ['序号', '差异类型', '物料编码', '物料名称', '规格', '原用量', '新用量', '位号']
    col_widths_excel = [5, 8, 20, 36, 18, 7, 7, 14]

    for ci, (h, w) in enumerate(zip(diff_headers, col_widths_excel), 1):
        cell = ws.cell(row=5, column=ci, value=h)
        cell.font = header_font
        cell.fill = blue_fill
        cell.alignment = center_a
        cell.border = thin_border
        ws.column_dimensions[get_column_letter(ci)].width = w
    ws.row_dimensions[5].height = 24

    # ── Data rows (starting Row 6) ──
    for di, dr in enumerate(diff_rows):
        row_num = di + 6
        dt = dr['type']

        if dt == 'added':
            fill = green_fill; font = green_font
            old_qty = '-'; new_qty = str(dr.get('qty', '1'))
        elif dt == 'removed':
            fill = red_fill; font = red_font
            old_qty = str(dr.get('qty', '1')); new_qty = '-'
        else:
            fill = amber_fill; font = amber_font
            old_qty = str(dr.get('old_qty', '')); new_qty = str(dr.get('new_qty', ''))

        values_excel = [di + 1, dr['type_label'], dr['pn'], dr['name'][:120],
                        '', old_qty, new_qty, '']

        for ci, val in enumerate(values_excel, 1):
            cell = ws.cell(row=row_num, column=ci, value=val)
            cell.font = font
            cell.fill = fill
            cell.border = thin_border
            cell.alignment = center_a if ci in (1, 2, 6, 7) else left_a
        ws.row_dimensions[row_num].height = 18

    # ── Signature section ──
    sig_row = len(diff_rows) + 7
    ws.merge_cells(f'A{sig_row}:H{sig_row}')
    ws.row_dimensions[sig_row].height = 6

    sig_row += 1
    ws.merge_cells(f'A{sig_row}:C{sig_row}')
    ws[f'A{sig_row}'].value = '拟制：杨芮'
    ws[f'A{sig_row}'].font = bold_font
    ws[f'A{sig_row}'].alignment = left_a

    ws.merge_cells(f'E{sig_row}:H{sig_row}')
    ws[f'E{sig_row}'].value = '审核：程涛'
    ws[f'E{sig_row}'].font = bold_font
    ws[f'E{sig_row}'].alignment = right_a

    sig_row += 1
    ws.merge_cells(f'A{sig_row}:H{sig_row}')
    ws[f'A{sig_row}'].value = f'本通知单由 BOM 比对工具自动生成 | 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
    ws[f'A{sig_row}'].font = gray_small
    ws[f'A{sig_row}'].alignment = right_a

    # Freeze panes below header
    ws.freeze_panes = 'A6'

    # Auto-filter
    last_data_row = len(diff_rows) + 5
    ws.auto_filter.ref = f'A5:H{last_data_row}'

    # ── Save ──
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if output_name is None:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_name = f'整机清机更改通知单_{src_model}_vs_{tgt_model}_{ts}'
    output_path = os.path.join(OUTPUT_DIR, f'{output_name}.xlsx')
    wb.save(output_path)
    return output_path


# ── CLI ─────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    tid = int(sys.argv[1]) if len(sys.argv) > 1 else 45
    fmt = sys.argv[2] if len(sys.argv) > 2 else 'docx'

    print(f'生成整机清机更改通知单...')
    print(f'  任务 ID: {tid}')
    if fmt == 'xlsx':
        path = generate_change_notice_excel(tid)
    else:
        path = generate_change_notice(tid)
    print(f'  输出文件: {path}')
    print(f'  完成!')
