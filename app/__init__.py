# app/__init__.py
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_bootstrap import Bootstrap5
from flask_wtf.csrf import CSRFProtect
from log_config import setup_logger
from async_worker import async_worker # Import instance global
from flask_migrate import Migrate # Thêm import
from .config import Config
import os





db = SQLAlchemy()
bootstrap = Bootstrap5()
csrf = CSRFProtect() # <-- Khởi tạo đối tượng CSRFProtect
migrate = Migrate() # Khởi tạo đối tượng Migrate




def create_app(config_class=Config):
    app = Flask(__name__)
    logger = setup_logger(__name__)
    
    app.config.from_object(config_class)

    db.init_app(app)
    migrate.init_app(app, db) # Gắn Migrate vào app và db
    bootstrap.init_app(app)

    # Khởi tạo IEC104Manager với app context
    # Cách tốt hơn là truy cập thông qua app.extensions hoặc một cơ chế quản lý global khác
    global iec104_manager
    from .iec104_manager import IEC104Manager # Import bên trong create_app
    iec104_manager = IEC104Manager(app)

    csrf.init_app(app) # <-- Kích hoạt CSRF protection cho app

    if not async_worker.loop or not async_worker.loop.is_running():
        app.logger.info("Starting AsyncWorker from create_app...")
        async_worker.start()
    else:
        app.logger.info("AsyncWorker already running.")

    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
             # Chỉ chạy khi là tiến trình chính của Werkzeug hoặc không ở chế độ debug
             from .opcua_client import try_auto_reconnect_servers # Import ở đây để tránh circular
             try_auto_reconnect_servers(app)
    elif app.debug:
             app.logger.info("Chế độ debug, bỏ qua auto-reconnect trong create_app để tránh chạy nhiều lần do reloader.")

    # Đăng ký các routes
    from .routes import register_routes # Import hàm đăng ký routes
    register_routes(app) # Gọi hàm để đăng ký các route đã định nghĩa vào app instance hiện tại

    from .mappings_routes import mappings_bp # THÊM IMPORT
    app.register_blueprint(mappings_bp) # ĐĂNG KÝ BLUEPRINT

    from .iec104_routes import iec104_bp
    app.register_blueprint(iec104_bp, url_prefix='/iec104')

    return app