import io
import os
import sqlite3
from flask import Blueprint, request, jsonify, render_template, current_app, send_file
from app.models import db as db_module
from app.services.xmind_export import generate_xmind_bytes as export_xmind_bytes

bp = Blueprint('upload', __name__)


def _get_db_conn():
    """获取直接 sqlite3 连接（XMind 导出需要）。"""
    db_path = current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
    return sqlite3.connect(db_path)


def _get_xmind_template_path():
    """获取 XMind 模板路径。"""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    return os.path.join(project_root, 'ogic block diagram', 'P1C85Q7HXX8TB56000.xmind')


@bp.route('/')
def upload_page():
    return render_template('upload.html')


@bp.route('/api', methods=['POST'])
def upload_file():
    """Handle BOM file upload, parse and store."""
    if 'file' not in request.files:
        return jsonify({'ok': False, 'msg': '未上传文件'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'ok': False, 'msg': '文件名不能为空'}), 400

    # Save file
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ('.xlsx', '.xls', '.csv'):
        return jsonify({'ok': False, 'msg': f'不支持的文件格式：{ext}'}), 400
    save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], f.filename)
    f.save(save_path)

    # Parse BOM
    from app.services.parser import parse_bom_file
    bom_name = (request.form.get('bom_name', '') or '').strip() or os.path.splitext(f.filename)[0]
    bom_version = request.form.get('bom_version', '')
    column_map_json = request.form.get('column_map', '')

    try:
        result = parse_bom_file(save_path, bom_name, bom_version, column_map_json)
        if isinstance(result, tuple):
            bom_id, stats = result
        else:
            bom_id, stats = result, None
        return jsonify({'ok': True, 'msg': '上传成功', 'bom_id': bom_id, 'stats': stats})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)}), 500


@bp.route('/api/list')
def list_boms():
    """List all uploaded BOMs."""
    from app.services.parser import get_uploaded_boms
    return jsonify({'ok': True, 'boms': get_uploaded_boms()})


@bp.route('/api/export/<int:bom_id>')
def export_bom(bom_id):
    """导出单份清洗后的BOM数据为Excel。"""
    from app.services.reporter import generate_cleaned_bom_excel
    file_path = generate_cleaned_bom_excel(bom_id)
    if not file_path or not os.path.exists(file_path):
        return jsonify({'ok': False, 'msg': '导出失败，未找到BOM数据'}), 404
    return send_file(file_path, as_attachment=True)


@bp.route('/api/export-dual')
def export_dual_boms():
    """导出两份清洗后的BOM数据到同一Excel。"""
    bom_a = request.args.get('bom_a', type=int)
    bom_b = request.args.get('bom_b', type=int)
    if not bom_a or not bom_b:
        return jsonify({'ok': False, 'msg': '请指定两份BOM的ID'}), 400

    from app.services.reporter import generate_cleaned_bom_dual_excel
    file_path = generate_cleaned_bom_dual_excel(bom_a, bom_b)
    if not file_path or not os.path.exists(file_path):
        return jsonify({'ok': False, 'msg': '导出失败'}), 500
    return send_file(file_path, as_attachment=True)


@bp.route('/api/components/<int:bom_id>')
def get_components(bom_id):
    """返回BOM中所有组件列表（基于层级结构：有子件的物料即为组件）。"""
    # 查询所有出现在 parent_pn 中的 part_number（即有子件的组件）
    rows = db_module.query(
        '''SELECT DISTINCT i.part_number, i.part_name, i.unit, i.quantity
           FROM bom_item i
           WHERE i.bom_id=?
             AND EXISTS (
               SELECT 1 FROM bom_item sub
               WHERE sub.bom_id=? AND sub.parent_pn=i.part_number
             )
           ORDER BY i.line_no''',
        (bom_id, bom_id)
    )

    # 如果上面查不到（parent_pn 为空的情况），回退到查 unit='ST'
    if not rows:
        rows = db_module.query(
            'SELECT part_number, part_name, unit, quantity FROM bom_item WHERE bom_id=? AND unit=? ORDER BY line_no',
            (bom_id, 'ST')
        )

    components = []
    for r in rows:
        pn = r['part_number']
        children_count = db_module.query_one(
            'SELECT COUNT(*) as cnt FROM bom_item WHERE bom_id=? AND parent_pn=?',
            (bom_id, pn)
        )['cnt']
        components.append({
            'part_number': pn,
            'part_name': r['part_name'],
            'unit': r['unit'] or 'ST',
            'quantity': r['quantity'] or 1,
            'children_count': children_count
        })
    return jsonify({'ok': True, 'components': components})


@bp.route('/api/bom-tree/<int:bom_id>')
def get_bom_tree(bom_id):
    """返回BOM完整层级树结构（用于前端折叠展示）。"""
    items = db_module.query(
        'SELECT part_number, part_name, unit, quantity, level, parent_pn, line_no '
        'FROM bom_item WHERE bom_id=? ORDER BY line_no',
        (bom_id,)
    )
    items = [dict(r) for r in items]
    if not items:
        return jsonify({'ok': True, 'tree': []})

    # Build parent -> children map
    children_map = {}
    for it in items:
        ppn = (it['parent_pn'] or '').strip()
        if ppn not in children_map:
            children_map[ppn] = []
        children_map[ppn].append(it)

    # Identify root items: level=1 or parent not in items list
    all_pns = set(it['part_number'].strip() for it in items)
    root_items = [it for it in items
                  if int(it.get('level', 0)) == 1
                  or (it.get('parent_pn', '') or '').strip() not in all_pns]

    # 按组件名称类别排序（电子 → 大配管/大配置 → 结构 → 专利），保证跨BOM对齐
    _CATEGORY_ORDER = {'电子': 1, '大配管': 2, '大配置': 2, '结构': 3, '专利': 4}
    def _category_key(it):
        name = it.get('part_name', '') or ''
        for kw, idx in _CATEGORY_ORDER.items():
            if kw in name:
                return idx
        return 99
    root_items.sort(key=_category_key)

    def build_node(it):
        pn = it['part_number'].strip()
        kids = children_map.get(pn, [])
        return {
            'part_number': it['part_number'],
            'part_name': it.get('part_name', ''),
            'quantity': it.get('quantity', 0),
            'unit': it.get('unit', ''),
            'level': it.get('level', 0),
            'line_no': it.get('line_no', 0),
            'children_count': len(kids),
            'children': [build_node(c) for c in kids]
        }

    tree = [build_node(r) for r in root_items]
    resp = jsonify({'ok': True, 'tree': tree, 'total_items': len(items)})
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@bp.route('/api/export-components', methods=['GET', 'POST'])
def export_components():
    """按选中的组件导出Excel（组件+子件）。支持 GET/POST。"""
    if request.method == 'POST':
        bom_id = request.form.get('bom_id', type=int) or request.json.get('bom_id', type=int) if request.is_json else None
        pns = request.form.getlist('components') or (request.json.get('components', []) if request.is_json else [])
    else:
        bom_id = request.args.get('bom_id', type=int)
        pns = request.args.getlist('components')
    if not bom_id or not pns:
        return jsonify({'ok': False, 'msg': '请指定BOM ID和组件'}), 400

    from app.services.reporter import generate_components_export_excel
    file_path = generate_components_export_excel(bom_id, pns)
    if not file_path or not os.path.exists(file_path):
        return jsonify({'ok': False, 'msg': '导出失败'}), 500
    return send_file(file_path, as_attachment=True)


@bp.route('/api/export-all-components/<int:bom_id>')
def export_all_components(bom_id):
    """导出BOM中所有组件及其子件到Excel（基于层级结构识别）。"""
    # 查询所有有子件的 part_number
    items = db_module.query(
        '''SELECT DISTINCT i.part_number FROM bom_item i
           WHERE i.bom_id=?
             AND EXISTS (
               SELECT 1 FROM bom_item sub
               WHERE sub.bom_id=? AND sub.parent_pn=i.part_number
             )
           ORDER BY i.line_no''',
        (bom_id, bom_id)
    )
    pns = [it['part_number'] for it in items]

    # 回退：如果上面查不到，尝试用 unit='ST'
    if not pns:
        items = db_module.query(
            'SELECT part_number FROM bom_item WHERE bom_id=? AND unit=? ORDER BY line_no',
            (bom_id, 'ST')
        )
        pns = [it['part_number'] for it in items]

    if not pns:
        return jsonify({'ok': False, 'msg': '该BOM没有可导出的组件'}), 400

    from app.services.reporter import generate_components_export_excel
    file_path = generate_components_export_excel(bom_id, pns)
    if not file_path or not os.path.exists(file_path):
        return jsonify({'ok': False, 'msg': '导出失败'}), 500
    return send_file(file_path, as_attachment=True)


@bp.route('/api/bom/<int:bom_id>', methods=['DELETE'])
def delete_bom(bom_id):
    """删除单个BOM及其所有关联数据。"""
    try:
        db_module.execute('DELETE FROM bom_item WHERE bom_id=?', (bom_id,))
        db_module.execute('DELETE FROM bom_header WHERE id=?', (bom_id,))
        return jsonify({'ok': True, 'msg': '已删除'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)}), 500


@bp.route('/api/boms/delete', methods=['POST'])
def batch_delete_boms():
    """批量删除BOM。"""
    data = request.get_json()
    ids = data.get('ids', []) if data else []
    if not ids:
        return jsonify({'ok': False, 'msg': '请指定要删除的BOM'}), 400

    try:
        placeholders = ','.join(['?'] * len(ids))
        db_module.execute(f'DELETE FROM bom_item WHERE bom_id IN ({placeholders})', tuple(ids))
        db_module.execute(f'DELETE FROM bom_header WHERE id IN ({placeholders})', tuple(ids))
        return jsonify({'ok': True, 'msg': f'已删除 {len(ids)} 个BOM'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)}), 500


@bp.route('/api/clear-database', methods=['POST'])
def clear_database():
    """一键清除所有 BOM 数据、比对结果和上传文件。"""
    try:
        # 统计清除前的数据量
        bom_count = len(db_module.query('SELECT id FROM bom_header'))
        task_count = len(db_module.query('SELECT id FROM comparison_task'))
        result_count = len(db_module.query('SELECT id FROM comparison_result'))

        # 清除比对结果
        db_module.execute('DELETE FROM comparison_result')
        # 清除比对任务
        db_module.execute('DELETE FROM comparison_task')
        # 清除 BOM 明细
        db_module.execute('DELETE FROM bom_item')
        # 清除 BOM 主表
        db_module.execute('DELETE FROM bom_header')

        # 重置所有表的自增ID计数器（SQLite 的 sqlite_sequence 表）
        db_module.execute("DELETE FROM sqlite_sequence")

        # 清除上传文件
        upload_folder = current_app.config.get('UPLOAD_FOLDER', '')
        if upload_folder and os.path.exists(upload_folder):
            for f in os.listdir(upload_folder):
                file_path = os.path.join(upload_folder, f)
                if os.path.isfile(file_path):
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass

        # 清除报告文件
        report_folder = current_app.config.get('REPORT_FOLDER', '')
        if report_folder and os.path.exists(report_folder):
            for f in os.listdir(report_folder):
                file_path = os.path.join(report_folder, f)
                if os.path.isfile(file_path):
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass

        return jsonify({
            'ok': True,
            'msg': f'已清除 {bom_count} 个BOM、{task_count} 个比对任务、{result_count} 条差异记录',
            'details': {
                'boms': bom_count,
                'tasks': task_count,
                'results': result_count
            }
        })
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)}), 500


# ---------- XMind 导出 ----------

@bp.route('/api/export-xmind/<int:bom_id>')
def export_xmind_single(bom_id):
    """导出单个 BOM 为 XMind 逻辑图。"""
    filename = request.args.get('filename', '').strip()
    template_path = _get_xmind_template_path()

    if not os.path.exists(template_path):
        return jsonify({'ok': False, 'msg': 'XMind 模板文件不存在'}), 500

    try:
        conn = _get_db_conn()
        try:
            data = export_xmind_bytes(conn, [bom_id], template_path)
        finally:
            conn.close()

        if not filename:
            bom_name = db_module.query_one(
                'SELECT bom_name FROM bom_header WHERE id=?', (bom_id,)
            )
            name = bom_name['bom_name'] if bom_name else f'BOM_{bom_id}'
            filename = f'{name}_逻辑图.xmind'

        return send_file(
            io.BytesIO(data),
            mimetype='application/vnd.xmind.workbook',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'XMind 导出失败：{str(e)}'}), 500


@bp.route('/api/export-xmind-batch', methods=['POST'])
def export_xmind_batch():
    """批量导出多个 BOM 为 XMind 工作簿（每个 BOM 一个 Sheet）。"""
    payload = request.get_json() or {}
    bom_ids = payload.get('bom_ids', [])
    filename = payload.get('filename', '').strip()

    if not bom_ids:
        return jsonify({'ok': False, 'msg': '请选择至少一个 BOM'}), 400

    template_path = _get_xmind_template_path()
    if not os.path.exists(template_path):
        return jsonify({'ok': False, 'msg': 'XMind 模板文件不存在'}), 500

    try:
        conn = _get_db_conn()
        try:
            data = export_xmind_bytes(conn, bom_ids, template_path)
        finally:
            conn.close()

        if not filename:
            filename = f'BOM工作簿_{len(bom_ids)}份.xmind'

        return send_file(
            io.BytesIO(data),
            mimetype='application/vnd.xmind.workbook',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'XMind 导出失败：{str(e)}'}), 500
