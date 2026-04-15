import importlib
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv
from flask import Flask, has_request_context, render_template, request
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException

try:
    MongoClient = getattr(importlib.import_module("pymongo"), "MongoClient")
except Exception:
    MongoClient = None

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


class MongoDBLogHandler(logging.Handler):
    def __init__(
        self,
        *,
        mongo_uri: str,
        database_name: str,
        collection_name: str,
        environment: str,
        timeout_ms: int,
    ) -> None:
        super().__init__()
        if MongoClient is None:
            raise RuntimeError("pymongo no está instalado")

        self.database_name = database_name
        self.collection_name = collection_name
        self.environment = environment

        self._client = MongoClient(
            mongo_uri,
            serverSelectionTimeoutMS=timeout_ms,
        )
        self._collection = self._client[database_name][collection_name]
        try:
            self._collection.create_index([("timestamp_utc", -1)])
            self._collection.create_index([("level", 1), ("timestamp_utc", -1)])
        except Exception:
            # No bloquear el arranque por índices.
            pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = {
                "timestamp_utc": datetime.fromtimestamp(
                    record.created,
                    tz=timezone.utc,
                ),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "formatted_message": self.format(record),
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
                "path": record.pathname,
                "environment": self.environment,
            }

            if record.exc_info:
                formatter = self.formatter or logging.Formatter()
                payload["exception"] = formatter.formatException(record.exc_info)

            if has_request_context():
                payload["request"] = {
                    "path": request.path,
                    "method": request.method,
                    "remote_addr": request.remote_addr,
                    "forwarded_for": request.headers.get("X-Forwarded-For"),
                    "user_agent": request.user_agent.string,
                }

            self._collection.insert_one(payload)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            self._client.close()
        finally:
            super().close()


def configure_logging(app: Flask) -> None:
    _remove_file_handlers(app)
    app.logger.setLevel(logging.INFO)
    configure_file_logging(app)
    configure_mongo_logging(app)


def _remove_file_handlers(app: Flask) -> None:
    handlers_to_remove = [
        handler
        for handler in app.logger.handlers
        if isinstance(handler, logging.FileHandler)
    ]
    for handler in handlers_to_remove:
        app.logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


def configure_file_logging(app: Flask) -> None:
    log_path = os.path.join(app.instance_path, "logs", "app.log")
    log_dir = os.path.dirname(log_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    file_handler_exists = any(
        isinstance(handler, logging.FileHandler)
        and os.path.abspath(getattr(handler, "baseFilename", "")) == log_path
        for handler in app.logger.handlers
    )
    if file_handler_exists:
        return

    file_handler = RotatingFileHandler(
        filename=log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )
    )
    app.logger.addHandler(file_handler)


def configure_mongo_logging(app: Flask) -> None:
    if not app.config.get("MONGO_LOGS_ENABLED", False):
        return

    if MongoClient is None:
        app.logger.warning(
            "MONGO_LOGS_ENABLED=True pero pymongo no está instalado; "
            "no se habilitó logging en MongoDB."
        )
        return

    mongo_uri = str(app.config.get("MONGO_URI", "")).strip()
    if not mongo_uri:
        app.logger.warning(
            "MONGO_LOGS_ENABLED=True pero MONGO_URI está vacío; "
            "no se habilitó logging en MongoDB."
        )
        return

    database_name = str(app.config.get("MONGO_LOGS_DB", "softbakery")).strip()
    collection_name = str(app.config.get("MONGO_LOGS_COLLECTION", "app_logs")).strip()
    timeout_ms = int(app.config.get("MONGO_LOGS_TIMEOUT_MS", 2000))
    environment = str(
        app.config.get("FLASK_ENV", os.getenv("FLASK_ENV", "development"))
    )

    mongo_handler_exists = any(
        isinstance(handler, MongoDBLogHandler)
        and getattr(handler, "database_name", None) == database_name
        and getattr(handler, "collection_name", None) == collection_name
        for handler in app.logger.handlers
    )
    if mongo_handler_exists:
        return

    try:
        mongo_handler = MongoDBLogHandler(
            mongo_uri=mongo_uri,
            database_name=database_name,
            collection_name=collection_name,
            environment=environment,
            timeout_ms=timeout_ms,
        )
        mongo_handler.setLevel(logging.INFO)
        mongo_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        app.logger.addHandler(mongo_handler)
        app.logger.info(
            "Logging MongoDB habilitado en %s.%s",
            database_name,
            collection_name,
        )
    except Exception:
        app.logger.exception("No se pudo habilitar el logging en MongoDB.")


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
