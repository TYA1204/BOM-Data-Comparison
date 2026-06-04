import os
from flask import Blueprint, request, jsonify, render_template, current_app, send_file
from app.models import db

bp = Blueprint('upload', __name__)


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
    bom_name = request.form.get('bom_name', os.path.splitext(f.filename)[0])
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
    """返回BOM中所有组件（单位=ST）列表。"""
    items = db.query(
        'SELECT part_number, part_name FROM bom_item WHERE bom_id=? AND unit=? ORDER BY line_no',
        (bom_id, 'ST')
    )
    components = []
    for it in items:
        pn = it['part_number']
        children_count = db.query_one(
            'SELECT COUNT(*) as cnt FROM bom_item WHERE bom_id=? AND parent_pn=?',
            (bom_id, pn)
        )['cnt']
        components.append({
            'part_number': pn,
            'part_name': it['part_name'],
            'children_count': children_count
        })
    return jsonify({'ok': True, 'components': components})


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
