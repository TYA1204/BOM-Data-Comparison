import os
import sys
from flask import Flask
from flask_compress import Compress


def create_app(config_class=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class or 'app.config.Config')

    Compress(app)

    # Ensure folders exist
    for folder in [app.config['UPLOAD_FOLDER'], app.config['REPORT_FOLDER']]:
        os.makedirs(folder, exist_ok=True)

    # Init database
    from app.models import db
    db.init_app(app)
    with app.app_context():
        from app.models.bom import init_bom_tables
        init_bom_tables(db)

    # Register blueprints
    from app.routes.main import bp as main_bp
    from app.routes.upload import bp as upload_bp
    from app.routes.compare import bp as compare_bp
    from app.routes.report import bp as report_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(upload_bp, url_prefix='/upload')
    app.register_blueprint(compare_bp, url_prefix='/compare')
    app.register_blueprint(report_bp, url_prefix='/report')

    return app
