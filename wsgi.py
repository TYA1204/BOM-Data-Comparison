"""
WSGI entry point for production deployment (Gunicorn).

Usage:
    gunicorn -w 4 -b 0.0.0.0:5002 wsgi:app
"""
import os
from app import create_app

# 生产环境确保 debug=False
app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5002))
    app.run(host='0.0.0.0', port=port, debug=False)
