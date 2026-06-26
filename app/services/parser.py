import os
import re
import json
import pandas as pd
from datetime import datetime
from flask import current_app
from app.models import db


# ==================== Date Normalization ====================

def _normalize_date(val):
    """Normalize various date formats to YYYY-MM-DD string.
    Handles: YYYY-MM-DD, YYYY/MM/DD, YYYYMMDD, DD.MM.YYYY, MM-DD-YYYY, etc.
    Returns original value as string if unparseable.
    """
    if val is None:
        return ''
    s = str(val).strip()
    if not s or s.lower() in ('nan', 'none', 'null', 'nat'):
        return ''

    # Already in YYYY-MM-DD format
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        return s

    # YYYY/MM/DD
    if re.match(r'^\d{4}/\d{2}/\d{2}$', s):
        return s.replace('/', '-')

    # YYYYMMDD
    m = re.match(r'^(\d{4})(\d{2})(\d{2})$', s)
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'

    # DD.MM.YYYY or DD-MM-YYYY
    for sep in ('.', '-'):
        m = re.match(r'^(\d{2})\\' + sep + r'(\d{2})\\' + sep + r'(\d{4})$', s)
        if m:
            return f'{m.group(3)}-{m.group(2)}-{m.group(1)}'

    # MM-DD-YYYY
    m = re.match(r'^(\d{2})-(\d{2})-(\d{4})$', s)
    if m:
        return f'{m.group(3)}-{m.group(1)}-{m.group(2)}'

    # Try datetime parse as last resort
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%d.%m.%Y', '%d/%m/%Y', '%m/%d/%Y',
                '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S'):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue

    return s  # Return original if all fail




# ==================== General Format Detection ====================

def _is_horizontal_bom(file_path):
    """检测是否为横向多列BOM格式（主数据/第一级/第二级...）"""
    try:
        with open(file_path, 'rb') as f:
            raw = f.read(65536)  # Read first 64KB
        text = raw.decode('utf-16-le', errors='ignore')
        lines = text.split('\r\n')[:5]
        for line in lines:
            cols = [c.strip() for c in line.split('\t') if c.strip()]
            if len(cols) >= 3:
                # Check if contains 主数据/第一级/第二级 keywords
                if any(kw in cols[0] for kw in ['主数据', '第一级', '第二级', '第三级']):
                    return True
        return False
    except Exception:
        return False


def detect_real_format(file_path):
    """Detect actual file format by reading file header bytes."""
    import zipfile

    # Read enough content for detection (128KB)
    with open(file_path, 'rb') as f:
        raw = f.read(131072)

    header = raw[:4096]

    # --- Check file content for SAP BOM markers (try multiple encodings) ---
    # This must be done FIRST, because SAP BOM files may have wrong extensions
    for enc in ['utf-16-le', 'gbk', 'gb18030', 'gb2312', 'utf-8-sig', 'latin-1']:
        try:
            sample = raw.decode(enc, errors='ignore')
            if 'B O M 展开表' in sample or 'BOM展开表' in sample:
                return 'sap_bom'
        except Exception:
            pass

    # Real .xlsx = ZIP archive (PK)
    if header[:4] == b'PK\x03\x04':
        return 'xlsx'

    # Real .xls = BIFF record
    if header[:2] == b'\xd0\xcf' or header[:1] == b'\x09':
        return 'xls'

    # UTF-16 LE BOM
    if header[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return 'csv'

    # HTML table
    if b'<html' in header.lower() or b'<table' in header.lower():
        return 'html'

    # Plain text / CSV
    if b'\t' in header or b',' in header:
        return 'csv'

    return 'csv'


def _is_likely_sap_bom(file_path):
    """Check if file content looks like SAP BOM (by reading text content)."""
    try:
        with open(file_path, 'rb') as f:
            raw = f.read(131072)  # Read first 128KB
        # Try UTF-16 LE (most common SAP export)
        try:
            text = raw.decode('utf-16-le')
        except UnicodeDecodeError:
            text = raw.decode('utf-8-sig', errors='ignore')
        return 'B O M 展开表' in text or 'BOM展开表' in text
    except Exception:
        return False


def read_bom_dataframe(file_path, nrows=None):
    """Read BOM file into DataFrame, auto-detecting real format."""
    fmt = detect_real_format(file_path)

    # --- Fallback check: even if detect_real_format says xlsx/xls,
    #     check if file content is actually SAP BOM text ---
    if fmt in ('xlsx', 'xls', 'csv'):
        if _is_likely_sap_bom(file_path):
            fmt = 'sap_bom'

    kwargs = dict(dtype=str, keep_default_na=False)

    if fmt == 'sap_bom':
        from app.services.bom_cleaner import clean_bom_data
        sap_metadata, sap_items, sap_stats = clean_bom_data(file_path)
        if not sap_items:
            raise ValueError('SAP BOM展开表中未找到有效数据行')
        df = pd.DataFrame(sap_items)
        if nrows:
            df = df.head(nrows)
        meta = {
            'bom_name': sap_metadata.get('bom_number', ''),
            'bom_version': sap_metadata.get('ecn', ''),
            'metadata': sap_metadata,
            'stats': sap_stats
        }
        return df, fmt

    if fmt == 'xlsx':
        if nrows:
            kwargs['nrows'] = nrows
        df = pd.read_excel(file_path, engine='openpyxl', **kwargs)
        # If all columns are Unnamed, file might be mis-detected
        if all(str(c).startswith('Unnamed:') for c in df.columns):
            # Try reading as CSV with tab separator
            try:
                df2 = pd.read_csv(file_path, sep='\t', encoding='utf-16-le', nrows=nrows, **kwargs)
                if not all(str(c).startswith('Unnamed:') for c in df2.columns):
                    return df2, 'csv'
            except Exception:
                pass
        return df, fmt

    if fmt == 'xls':
        if nrows:
            kwargs['nrows'] = nrows
        return pd.read_excel(file_path, engine='xlrd', **kwargs), fmt

    if fmt == 'html':
        dfs = pd.read_html(file_path, **kwargs)
        df = dfs[0] if dfs else pd.DataFrame()
        if nrows:
            df = df.head(nrows)
        return df, fmt

    # CSV / plain text (auto-detect separator: comma or tab)
    encodings = ['utf-8-sig', 'utf-16', 'utf-16-le', 'gbk', 'gb2312', 'gb18030', 'latin-1']
    for enc in encodings:
        try:
            if nrows:
                df = pd.read_csv(file_path, encoding=enc, sep=None, engine='python', nrows=nrows, **kwargs)
            else:
                df = pd.read_csv(file_path, encoding=enc, sep=None, engine='python', **kwargs)
            if not df.empty:
                return df, fmt
        except (UnicodeDecodeError, pd.errors.EmptyDataError, pd.errors.ParserError):
            continue
    raise ValueError('无法解析文件，尝试了多种编码均失败')


def detect_columns(headers):
    """Auto-detect column mapping from BOM headers."""
    config = current_app.config['COLUMN_MAP']
    mapping = {}

    for field, candidates in config.items():
        for header in headers:
            h_stripped = str(header).strip()
            for candidate in candidates:
                if h_stripped.lower() == candidate.lower() or h_stripped == candidate:
                    mapping[field] = header
                    break
            if field in mapping:
                break

    return mapping


def parse_bom_file(file_path, bom_name, bom_version='', column_map_json=''):
    """Parse Excel/CSV/SAP BOM file and store in database.

    Returns bom_id.
    """
    fmt = detect_real_format(file_path)

    # Fallback check for SAP BOM
    if fmt in ('xlsx', 'xls', 'csv'):
        if _is_likely_sap_bom(file_path):
            fmt = 'sap_bom'

    # SAP BOM 展开表: use bom_cleaner v3.0
    if fmt == 'sap_bom':
        from app.services.bom_cleaner import clean_bom_data
        sap_metadata, sap_items, sap_stats = clean_bom_data(file_path)
        if not sap_items:
            raise ValueError('SAP BOM展开表中未找到有效数据行')
        if not bom_name:
            bom_name = sap_metadata.get('bom_number', bom_name)
        if not bom_version:
            bom_version = sap_metadata.get('ecn', bom_version)

        # Insert bom_header with metadata
        valid_from = _normalize_date(sap_metadata.get('valid_date', ''))
        bom_number = sap_metadata.get('bom_number', '')
        ecn_val = sap_metadata.get('ecn', '')
        bom_status = sap_metadata.get('status', '')
        bom_plant = sap_metadata.get('plant', '')

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor = db.execute('''
            INSERT INTO bom_header (bom_name, bom_version, source_type, source_file,
                total_items, total_quantity, valid_from, bom_number, ecn, bom_status, bom_plant, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            bom_name,
            bom_version,
            os.path.splitext(file_path)[1].upper().replace('.', ''),
            os.path.basename(file_path),
            len(sap_items),
            len(sap_items),
            valid_from,
            bom_number,
            ecn_val,
            bom_status,
            bom_plant,
            now_str,
        ))
        bom_id = cursor.lastrowid

        # Insert bom_items directly from cleaner output
        items = []
        for idx, it in enumerate(sap_items):
            items.append((
                bom_id,
                idx + 1,
                it['level'],
                it.get('parent_pn', ''),
                it['part_number'],
                it['part_name'],
                '',  # specification (cleaner doesn't extract it)
                it.get('quantity', 0),
                it.get('unit', ''),
                it.get('reference', ''),
                it.get('ecn', ''),
                '',  # manufacturer
                '',  # mpn
                it.get('priority', ''),
            ))

        db.executemany('''
            INSERT INTO bom_item (bom_id, line_no, level, parent_pn, part_number, part_name,
                specification, quantity, unit, reference, version, manufacturer, mpn, alternative)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', items)

        return bom_id, sap_stats
    else:
        df, _ = read_bom_dataframe(file_path)

    df = df.dropna(how='all')
    if df.empty:
        raise ValueError('File is empty or all rows are blank')

    # Strip whitespace from headers
    df.columns = [str(c).strip() for c in df.columns]

    # For non-SAP files, apply column mapping
    if fmt != 'sap_bom':
        if column_map_json:
            column_map = json.loads(column_map_json)
        else:
            column_map = detect_columns(list(df.columns))

        if 'part_number' not in column_map:
            raise ValueError(
                f'无法识别物料号列。可用列名：{list(df.columns)}，请手动配置列映射。'
            )
        rename_rules = {v: k for k, v in column_map.items()}
        df = df.rename(columns=rename_rules)

    # Ensure required columns exist
    for col in ['part_number', 'quantity']:
        if col not in df.columns:
            df[col] = ''

    # Drop rows without part_number
    df = df[df['part_number'].astype(str).str.strip() != '']
    if df.empty:
        raise ValueError('过滤空物料号后无有效数据行')

    # Fill missing columns
    for col in ['part_name', 'specification', 'unit', 'reference', 'version', 'manufacturer', 'mpn', 'alternative', 'level', 'parent_pn']:
        if col not in df.columns:
            df[col] = ''

    # Ensure level is numeric
    df['level'] = pd.to_numeric(df['level'], errors='coerce').fillna(0).astype(int)

    # Calculate parent_pn by level-based stack inference
    # For SAP BOM: level hierarchy implies parent-child relationship
    # For other formats: if parent_pn already populated, keep it
    has_parent = df['parent_pn'].astype(str).str.strip() != ''
    if not has_parent.all():
        stack = []  # [(level, part_number)]
        parents = []
        for _, row in df.iterrows():
            lv = int(row['level'])
            pn = str(row['part_number']).strip()
            # Pop stack until we find the parent level
            while stack and stack[-1][0] >= lv:
                stack.pop()
            parent = stack[-1][1] if stack else ''
            stack.append((lv, pn))
            parents.append(parent)
        df['parent_pn'] = parents

    # Insert bom_header with metadata
    # Extract metadata fields for standard files (first non-empty value per column)
    meta_fields = ['valid_from', 'bom_number', 'ecn', 'bom_status', 'bom_plant']
    meta_values = {}
    for mf in meta_fields:
        if mf in df.columns:
            vals = df[mf].dropna().astype(str).str.strip()
            vals = vals[vals != '']
            meta_values[mf] = vals.iloc[0] if len(vals) > 0 else ''
        else:
            meta_values[mf] = ''
    meta_values['valid_from'] = _normalize_date(meta_values['valid_from'])

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor = db.execute('''
        INSERT INTO bom_header (bom_name, bom_version, source_type, source_file,
            total_items, total_quantity, valid_from, bom_number, ecn, bom_status, bom_plant, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        bom_name,
        bom_version,
        os.path.splitext(file_path)[1].upper().replace('.', ''),
        os.path.basename(file_path),
        len(df),
        len(df),
        meta_values['valid_from'],
        meta_values['bom_number'],
        meta_values['ecn'],
        meta_values['bom_status'],
        meta_values['bom_plant'],
        now_str,
    ))
    bom_id = cursor.lastrowid

    # Insert bom_items
    items = []
    for idx, row in df.iterrows():
        items.append((
            bom_id,
            int(idx) + 1,
            int(row.get('level', 0) or 0),
            str(row.get('parent_pn', '') or '').strip(),
            str(row.get('part_number', '') or '').strip(),
            str(row.get('part_name', '') or '').strip(),
            str(row.get('specification', '') or '').strip(),
            float(row.get('quantity', 0) or 0),
            str(row.get('unit', '') or '').strip(),
            str(row.get('reference', '') or '').strip(),
            str(row.get('version', '') or '').strip(),
            str(row.get('manufacturer', '') or '').strip(),
            str(row.get('mpn', '') or '').strip(),
            str(row.get('alternative', '') or '').strip(),
        ))

    db.executemany('''
        INSERT INTO bom_item (bom_id, line_no, level, parent_pn, part_number, part_name,
            specification, quantity, unit, reference, version, manufacturer, mpn, alternative)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', items)

    return bom_id, None


def preview_file(file_path):
    """Return first 10 rows + detected column mapping for preview."""
    df_preview, _ = read_bom_dataframe(file_path, nrows=10)
    df = df_preview

    # For total_rows, read full file
    df_full, _ = read_bom_dataframe(file_path)
    total = len(df_full)
    del df_full

    df = df.dropna(how='all')
    df.columns = [str(c).strip() for c in df.columns]

    column_map = detect_columns(list(df.columns))

    return {
        'headers': list(df.columns),
        'rows': df.head(10).to_dict('records'),
        'column_map': column_map,
        'total_rows': total
    }


def get_uploaded_boms():
    """List all uploaded BOMs."""
    rows = db.query(
        'SELECT id, bom_name, bom_version, source_type, source_file, '
        'total_items, created_at, valid_from, bom_number, ecn, bom_status, bom_plant '
        'FROM bom_header ORDER BY created_at DESC'
    )
    return [dict(r) for r in rows]
