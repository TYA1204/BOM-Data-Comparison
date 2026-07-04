import os
from flask import Blueprint, request, jsonify, render_template, current_app

bp = Blueprint('compare', __name__)


@bp.route('/')
def compare_page():
    return render_template('compare.html')


@bp.route('/api/start', methods=['POST'])
def start_comparison():
    """Start a BOM comparison task."""
    data = request.get_json()
    source_bom_id = data.get('source_bom_id')
    target_bom_id = data.get('target_bom_id')
    comparison_type = data.get('comparison_type', 'version')
    compare_mode = data.get('compare_mode', 'components_only')
    selected_components = data.get('selected_components', {})
    exclude_parents = data.get('exclude_parents', [])
    exclude_leaves = data.get('exclude_leaves', False)
    skip_pns = data.get('skip_pns', [])

    if not source_bom_id or not target_bom_id:
        return jsonify({'ok': False, 'msg': '请选择来源BOM和目标BOM'}), 400

    from app.services.differ import run_comparison
    try:
        task_id = run_comparison(
            source_bom_id, target_bom_id, comparison_type,
            compare_mode=compare_mode,
            selected_components=selected_components,
            exclude_parents=exclude_parents,
            exclude_leaves=exclude_leaves,
            skip_pns=skip_pns
        )
        return jsonify({'ok': True, 'msg': '比对完成', 'task_id': task_id})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)}), 500


@bp.route('/api/result/<int:task_id>')
def get_result(task_id):
    """Get comparison result for a task."""
    from app.models import db
    task = db.query_one(
        'SELECT * FROM comparison_task WHERE id=?', (task_id,)
    )
    if not task:
        return jsonify({'ok': False, 'msg': '未找到该比对任务'}), 404

    results = db.query(
        '''SELECT * FROM comparison_result WHERE task_id=?
           ORDER BY CASE diff_type
               WHEN 'added' THEN 1 WHEN 'removed' THEN 2 ELSE 3
           END, COALESCE(line_no_b, line_no_a)''',
        (task_id,)
    )
    return jsonify({
        'ok': True,
        'task': dict(task),
        'results': [dict(r) for r in results]
    })


@bp.route('/api/history')
def task_history():
    """List all comparison tasks."""
    from app.models import db
    tasks = db.query(
        '''SELECT ct.*, 
           (SELECT COUNT(*) FROM comparison_result WHERE task_id=ct.id) as diff_count,
           sh.bom_name as source_name, th.bom_name as target_name
           FROM comparison_task ct
           LEFT JOIN bom_header sh ON ct.source_bom_id=sh.id
           LEFT JOIN bom_header th ON ct.target_bom_id=th.id
           ORDER BY ct.created_at DESC LIMIT 50'''
    )
    return jsonify({'ok': True, 'tasks': [dict(t) for t in tasks]})


@bp.route('/api/change-notice/<int:task_id>')
def generate_change_notice(task_id):
    """Generate 整机清机更改通知单 for a comparison task."""
    from flask import current_app
    from app.services.change_notice import generate_change_notice as gen_docx, generate_change_notice_excel

    fmt = request.args.get('format', 'docx')
    order_no = request.args.get('order_no', '').strip()
    stage = request.args.get('stage', '').strip()
    quantity = request.args.get('quantity', '1').strip()
    drafter = request.args.get('drafter', '').strip()
    reviewer = request.args.get('reviewer', '').strip()
    db_path = current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')

    try:
        if fmt == 'xlsx':
            path = generate_change_notice_excel(task_id, db_path=db_path,
                                                drafter=drafter, reviewer=reviewer)
        else:
            path = gen_docx(task_id, db_path=db_path,
                            order_no=order_no, stage=stage, quantity=quantity,
                            drafter=drafter, reviewer=reviewer)

        filename = os.path.basename(path)
        return jsonify({
            'ok': True,
            'file': filename,
            'path': path,
            'format': fmt,
        })
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)}), 500


@bp.route('/api/download-change-notice/<int:task_id>')
def download_change_notice(task_id):
    """Generate 整机清机更改通知单 and stream as file download."""
    from flask import send_file
    from app.services.change_notice import generate_change_notice as gen_docx, generate_change_notice_excel

    fmt = request.args.get('format', 'docx')
    order_no = request.args.get('order_no', '').strip()
    stage = request.args.get('stage', '').strip()
    quantity = request.args.get('quantity', '1').strip()
    drafter = request.args.get('drafter', '').strip()
    reviewer = request.args.get('reviewer', '').strip()
    db_path = current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')

    try:
        if fmt == 'xlsx':
            path = generate_change_notice_excel(task_id, db_path=db_path,
                                                drafter=drafter, reviewer=reviewer)
            mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        else:
            path = gen_docx(task_id, db_path=db_path,
                            order_no=order_no, stage=stage, quantity=quantity,
                            drafter=drafter, reviewer=reviewer)
            mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

        filename = os.path.basename(path)
        return send_file(path, mimetype=mime, as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)}), 500


@bp.route('/api/html-report/<int:task_id>')
def html_report(task_id):
    """Generate HTML format change notice report."""
    import sqlite3
    from datetime import datetime
    from app.services.change_notice import get_diff_rows, group_diffs_by_parent, clean_material_name

    order_no = request.args.get('order_no', '').strip()
    stage = request.args.get('stage', '').strip()
    quantity = request.args.get('quantity', '1').strip()
    drafter = request.args.get('drafter', '').strip()
    reviewer = request.args.get('reviewer', '').strip()
    db_path = current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        task = conn.execute('SELECT * FROM comparison_task WHERE id=?', (task_id,)).fetchone()
        if not task:
            return '<h1>Task not found</h1>', 404

        src = conn.execute('SELECT bom_number, bom_name FROM bom_header WHERE id=?',
                           (task['source_bom_id'],)).fetchone()
        tgt = conn.execute('SELECT bom_number, bom_name FROM bom_header WHERE id=?',
                           (task['target_bom_id'],)).fetchone()
        if not src or not tgt:
            return '<h1>BOM not found</h1>', 404

        machine_core = _extract_core((tgt['bom_number'] or tgt['bom_name']).strip())
        model = _extract_model((tgt['bom_number'] or tgt['bom_name']).strip())

        diff_rows = get_diff_rows(conn, task_id,
                                  source_bom_id=task['source_bom_id'],
                                  target_bom_id=task['target_bom_id'])
        groups = group_diffs_by_parent(diff_rows)

        # Build items for each group
        for g in groups:
            items = []
            for m in g.get('items', g.get('modified', [])):
                ref = m.get('ref', '') or ''
                ref_parts = ref.split() if ref else []
                ref_lines = []
                for i in range(0, len(ref_parts), 5):
                    ref_lines.append(' '.join(ref_parts[i:i+5]))
                items.append({
                    'type': m.get('type_label', m.get('type', 'MOD')),
                    'pn': m.get('pn', ''),
                    'name': m.get('name', ''),
                    'qty_text': m.get('qty', ''),
                    'ref_lines': ref_lines,
                })
            g['diff_items'] = items

        src_bom = (src['bom_number'] or src['bom_name']).strip()
        tgt_bom = (tgt['bom_number'] or tgt['bom_name']).strip()

        html = render_template('html_report.html',
                               machine_core=machine_core,
                               model=model,
                               date=datetime.now().strftime('%Y-%m-%d'),
                               order_no=order_no,
                               stage=stage,
                               quantity=quantity,
                               drafter=drafter,
                               reviewer=reviewer,
                               src_bom=src_bom,
                               tgt_bom=tgt_bom,
                               groups=groups)
        return html
    finally:
        conn.close()


def _extract_core(bom_code):
    """Extract machine core from BOM code like P1C100P3EM8R713001 → 8R713."""
    import re
    m = re.search(r'(\d+[A-Z]+\d+)', bom_code)
    return m.group(1) if m else bom_code


def _extract_model(bom_code):
    """Extract short model name from BOM code like P1C100P3EM8R713001 → 100P3EM."""
    import re
    m = re.search(r'\d{2,3}[A-Z]\d+[A-Z]*', bom_code)
    if m:
        parts = re.findall(r'(\d{2,3}[A-Z]\d+[A-Z]*)', bom_code)
        return parts[-1] if parts else bom_code
    return bom_code
