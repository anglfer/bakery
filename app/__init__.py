from dotenv import load_dotenv
from flask import Flask

from app.admin import admin_bp
from app.auth import auth_bp
from app.catalog import catalog_bp
from app.extensions import bcrypt, db, login_manager, migrate
from app.production import production_bp
from app.sales import sales_bp
from app.seed_data import seed_full_data
from instance.config import get_config_path


def create_app(config_object: str | None = None) -> Flask:
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object or get_config_path())

    init_extensions(app)
    register_blueprints(app)
    register_cli_commands(app)

    if app.config.get("AUTO_DB_INIT", False):
        with app.app_context():
            from app import models

            db.create_all()
            models.seed_base_catalog_data()

    return app


def init_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    bcrypt.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Inicia sesión para continuar."


def register_blueprints(app: Flask) -> None:
    app.register_blueprint(catalog_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(production_bp, url_prefix="/production")
    app.register_blueprint(sales_bp, url_prefix="/sales")


def register_cli_commands(app: Flask) -> None:
    @app.cli.command("seed-base")
    def seed_base_command() -> None:
        with app.app_context():
            from app import models

            db.create_all()
            models.seed_base_catalog_data()
            db.session.commit()
            print("Seed base ejecutada.")

    @app.cli.command("seed-full")
    def seed_full_command() -> None:
        with app.app_context():
            from app import models

            db.create_all()
            models.seed_base_catalog_data()
            seed_full_data()
            print("Seed completa ejecutada.")
