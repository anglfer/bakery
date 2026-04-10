import logging
import os
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv
from flask import Flask, render_template, request
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException

from app.admin import admin_bp
from app.auth import auth_bp
from app.catalog import catalog_bp
from app.extensions import bcrypt, csrf, db, login_manager, migrate
from app.production import production_bp
from app.sales import sales_bp
from app.seed_data import seed_full_data
from instance.config import get_config_path


def create_app(config_object: str | None = None) -> Flask:
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object or get_config_path())

    configure_logging(app)
    init_extensions(app)
    register_blueprints(app)
    register_error_handlers(app)
    register_cli_commands(app)

    if app.config.get("AUTO_DB_INIT", False):
        with app.app_context():
            from app import models

            db.create_all()
            models.seed_base_catalog_data()

    return app


def configure_logging(app: Flask) -> None:
    os.makedirs(app.instance_path, exist_ok=True)
    log_dir = os.path.join(app.instance_path, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "app.log")

    handler_exists = any(
        isinstance(handler, RotatingFileHandler)
        and getattr(handler, "baseFilename", "") == log_file
        for handler in app.logger.handlers
    )
    if handler_exists:
        return

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=1_048_576,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )

    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)


def init_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    bcrypt.init_app(app)
    csrf.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Inicia sesión para continuar."


def register_blueprints(app: Flask) -> None:
    app.register_blueprint(catalog_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(production_bp, url_prefix="/production")
    app.register_blueprint(sales_bp, url_prefix="/sales")


def register_error_handlers(app: Flask) -> None:
    def _render_http_error(
        *,
        status_code: int,
        title: str,
        message: str,
        details: str | None = None,
    ):
        return (
            render_template(
                "errors/http_error.html",
                status_code=status_code,
                title=title,
                message=message,
                details=details,
            ),
            status_code,
        )

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error: CSRFError):
        return _render_http_error(
            status_code=400,
            title="Solicitud inválida",
            message=(
                "Tu sesión o formulario expiró. "
                "Recarga la página e intenta de nuevo."
            ),
            details=error.description,
        )

    @app.errorhandler(400)
    def handle_bad_request(error):
        return _render_http_error(
            status_code=400,
            title="Solicitud inválida",
            message="No se pudo procesar la solicitud.",
            details=getattr(error, "description", None),
        )

    @app.errorhandler(401)
    def handle_unauthorized(error):
        return _render_http_error(
            status_code=401,
            title="No autorizado",
            message="Debes iniciar sesión para continuar.",
            details=getattr(error, "description", None),
        )

    @app.errorhandler(403)
    def handle_forbidden(error):
        return _render_http_error(
            status_code=403,
            title="Acceso denegado",
            message="No tienes permisos para acceder a este recurso.",
            details=getattr(error, "description", None),
        )

    @app.errorhandler(404)
    def handle_not_found(error):
        return _render_http_error(
            status_code=404,
            title="Página no encontrada",
            message="La ruta que buscaste no existe o fue movida.",
            details=getattr(error, "description", None),
        )

    @app.errorhandler(405)
    def handle_method_not_allowed(error):
        return _render_http_error(
            status_code=405,
            title="Método no permitido",
            message=("La operación solicitada " "no está permitida para esta ruta."),
            details=getattr(error, "description", None),
        )

    @app.errorhandler(500)
    def handle_internal_server_error(error):
        app.logger.exception("Error interno 500 en ruta %s", request.path)
        return _render_http_error(
            status_code=500,
            title="Error interno del servidor",
            message=(
                "Ocurrió un error inesperado. " "Intenta nuevamente en unos minutos."
            ),
            details=getattr(error, "description", None),
        )

    @app.errorhandler(Exception)
    def handle_unexpected_exception(error: Exception):
        if isinstance(error, HTTPException):
            return error

        app.logger.exception(
            "Excepción no controlada en ruta %s",
            request.path,
        )
        return _render_http_error(
            status_code=500,
            title="Error interno del servidor",
            message=(
                "Ocurrió un error inesperado. " "Intenta nuevamente en unos minutos."
            ),
            details=str(error) if app.debug else None,
        )


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
