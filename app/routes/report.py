from flask import Blueprint, request, jsonify, send_file, current_app
import os

bp = Blueprint('report', __name__)


@bp.route('/api/export/<int:task_id>')
def export_excel(task_id):
    """Export comparison result as Excel."""
    from app.services.reporter import generate_excel_report
    file_path = generate_excel_report(task_id)
    if not file_path or not os.path.exists(file_path):
        return jsonify({'ok': False, 'msg': '报告生成失败'}), 500
    return send_file(file_path, as_attachment=True)


@bp.route('/api/export-rework/<int:task_id>')
def export_rework_excel(task_id):
    """Export rework change order as Excel."""
    from app.services.reporter import generate_rework_order_excel
    file_path = generate_rework_order_excel(task_id)
    if not file_path or not os.path.exists(file_path):
        return jsonify({'ok': False, 'msg': '返工变更单生成失败'}), 500
    return send_file(file_path, as_attachment=True)


@bp.route('/api/stats/<int:task_id>')
def task_stats(task_id):
    """Get statistics for a comparison task."""
    from app.models import db
    stats = db.query('''
        SELECT 
            diff_type, diff_category, COUNT(*) as cnt
        FROM comparison_result 
        WHERE task_id=?
        GROUP BY diff_type, diff_category
        ORDER BY cnt DESC
    ''', (task_id,))
    return jsonify({'ok': True, 'stats': [dict(s) for s in stats]})
