import importlib
import logging
import os
import re
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv
from flask import Flask, has_request_context, render_template, request
from flask_login import current_user
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

LOG_KEY_VALUE_RE = re.compile(r"(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)=(?P<value>[^|]+)")


def create_app(config_object: str | None = None) -> Flask:
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object or get_config_path())

    configure_logging(app)
    init_extensions(app)
    register_blueprints(app)
    register_error_handlers(app)
    register_cli_commands(app)
    configure_request_logging(app)

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
            self._collection.create_index([("event", 1), ("timestamp_utc", -1)])
            self._collection.create_index([("actor", 1), ("timestamp_utc", -1)])
            self._collection.create_index([("user", 1), ("timestamp_utc", -1)])
            self._collection.create_index([("role", 1), ("timestamp_utc", -1)])
        except Exception:
            # No bloquear el arranque por índices.
            pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            extracted_fields = _extract_fields_from_log_message(message)
            payload = {
                "timestamp_utc": datetime.fromtimestamp(
                    record.created,
                    tz=timezone.utc,
                ),
                "level": record.levelname,
                "logger": record.name,
                "message": message,
                "formatted_message": self.format(record),
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
                "path": record.pathname,
                "environment": self.environment,
                "event": extracted_fields.get("event"),
                "actor": extracted_fields.get("actor"),
                "user": extracted_fields.get("user"),
                "role": extracted_fields.get("role"),
                "ip": extracted_fields.get("ip"),
                "fields": extracted_fields.get("fields", {}),
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


def _extract_fields_from_log_message(message: str) -> dict:
    if not message:
        return {"fields": {}}

    raw_parts = [part.strip() for part in message.split("|") if part.strip()]
    fields: dict[str, str] = {}
    event = None
    for index, part in enumerate(raw_parts):
        if "=" not in part and index == 0:
            event = part
            continue

        match = LOG_KEY_VALUE_RE.fullmatch(part)
        if not match:
            continue

        key = match.group("key").lower()
        value = match.group("value").strip()
        fields[key] = value

    actor = fields.get("actor") or fields.get("usuario")
    user = fields.get("user") or fields.get("username") or actor
    role = fields.get("rol") or fields.get("role")
    ip = fields.get("ip") or fields.get("remote_addr")

    return {
        "event": event,
        "actor": actor,
        "user": user,
        "role": role,
        "ip": ip,
        "fields": fields,
    }


def _resolve_current_actor() -> tuple[str, str]:
    if not current_user.is_authenticated:
        return "anonimo", "sin_rol"

    actor = getattr(current_user, "username", "anonimo") or "anonimo"
    role_name = "sin_rol"
    if getattr(current_user, "rol", None):
        role_name = current_user.rol.nombre or "sin_rol"
    return actor, role_name


def configure_request_logging(app: Flask) -> None:
    @app.before_request
    def log_request_start() -> None:
        if request.endpoint == "static":
            return

        actor, role_name = _resolve_current_actor()
        request.environ["sb_request_start"] = time.perf_counter()
        app.logger.info(
            "REQUEST_START|method=%s|path=%s|endpoint=%s|user=%s|role=%s",
            request.method,
            request.path,
            request.endpoint or "unknown",
            actor,
            role_name,
        )

    @app.after_request
    def log_request_end(response):
        if request.endpoint == "static":
            return response

        actor, role_name = _resolve_current_actor()
        started_at = request.environ.get("sb_request_start")
        elapsed_ms = 0.0
        if isinstance(started_at, float):
            elapsed_ms = (time.perf_counter() - started_at) * 1000

        app.logger.info(
            (
                "REQUEST_END|method=%s|path=%s|endpoint=%s|status=%s|"
                "duration_ms=%.2f|user=%s|role=%s"
            ),
            request.method,
            request.path,
            request.endpoint or "unknown",
            response.status_code,
            elapsed_ms,
            actor,
            role_name,
        )
        return response


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
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
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
