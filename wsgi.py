"""
WSGI entry point for production deployment (Gunicorn).

Usage:
    gunicorn -w 4 -b 0.0.0.0:5002 wsgi:app
"""
import os
import sys

# ── 禁止 Python 运行时生成 .pyc 字节码文件 ──
# 必须在所有 import 之前设置，从根源避免 __pycache__ 缓存问题
os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
sys.dont_write_bytecode = True

from app import create_app
from flask import jsonify

# ── 版本信息 ──
# 部署时间: 由部署脚本自动更新
__deploy_time__ = os.environ.get('DEPLOY_TIME', 'unknown')
__deploy_commit__ = os.environ.get('DEPLOY_COMMIT', 'unknown')

# 生产环境确保 debug=False
app = create_app()

# 注入版本信息到 app config，供健康检查使用
app.config.setdefault('DEPLOY_TIME', __deploy_time__)
app.config.setdefault('DEPLOY_COMMIT', __deploy_commit__)

# 添加健康检查端点
@app.route('/health')
def health_check():
    """轻量健康检查端点，供负载均衡和监控使用"""
    import time
    module_mtime = int(os.path.getmtime(__file__)) if os.path.exists(__file__) else 0
    return jsonify({
        'status': 'ok',
        'timestamp': int(time.time()),
        'version': {
            'deploy_time': app.config.get('DEPLOY_TIME', 'unknown'),
            'deploy_commit': app.config.get('DEPLOY_COMMIT', 'unknown'),
            'pycache_disabled': sys.dont_write_bytecode,
        },
        'modules': {
            'wsgi_mtime': module_mtime,
        }
    })

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5002))
    app.run(host='0.0.0.0', port=port, debug=False)
