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
        'SELECT * FROM comparison_result WHERE task_id=? ORDER BY severity DESC, id',
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
    db_path = current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')

    try:
        if fmt == 'xlsx':
            path = generate_change_notice_excel(task_id, db_path=db_path)
        else:
            path = gen_docx(task_id, db_path=db_path)

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
    db_path = current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')

    try:
        if fmt == 'xlsx':
            path = generate_change_notice_excel(task_id, db_path=db_path)
            mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        else:
            path = gen_docx(task_id, db_path=db_path)
            mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

        filename = os.path.basename(path)
        return send_file(path, mimetype=mime, as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)}), 500
