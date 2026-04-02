from functools import wraps

from flask import abort, current_app, request
from flask_login import current_user

from app.models import Modulo, Permiso


def log_audit_event(evento: str, detalle: str) -> None:
    actor = "anonimo"
    rol = "sin_rol"
    if current_user.is_authenticated:
        actor = current_user.username
        rol = current_user.rol.nombre if current_user.rol else "sin_rol"

    ip_origen = request.headers.get("X-Forwarded-For", request.remote_addr or "-")
    current_app.logger.info(
        "AUDIT|evento=%s|actor=%s|rol=%s|ip=%s|detalle=%s",
        evento,
        actor,
        rol,
        ip_origen,
        detalle,
    )


def require_permission(modulo_nombre: str, accion: str):
    action_map = {
        "leer": "lectura",
        "crear": "escritura",
        "editar": "actualizacion",
        "desactivar": "eliminacion",
    }

    if accion not in action_map:
        raise ValueError("Accion de permiso no valida")

    field_name = action_map[accion]

    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)

            module = Modulo.query.filter_by(nombre=modulo_nombre).first()
            if not module:
                abort(403)

            permission = Permiso.query.filter_by(
                id_rol=current_user.id_rol,
                id_modulo=module.id_modulo,
            ).first()
            if not permission or not getattr(permission, field_name):
                abort(403)

            return func(*args, **kwargs)

        return wrapped

    return decorator
