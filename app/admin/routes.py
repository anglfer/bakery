from __future__ import annotations

import importlib
import re
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from flask import (
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.admin import admin_bp
from app.admin.forms import (
    ProveedorForm,
    RolCrearForm,
    RolEditarForm,
    UsuarioCrearForm,
    UsuarioEditarForm,
)
from app.common.passwords import is_password_insecure
from app.common.security import log_audit_event, require_permission
from app.extensions import db
from app.models import (
    DetalleVenta,
    Modulo,
    Pedido,
    Permiso,
    Persona,
    Producto,
    Proveedor,
    Rol,
    Usuario,
    Venta,
    utc_today,
)

BASE_ROLES = {"Administrador", "Ventas", "Produccion"}
DASHBOARD_ALLOWED_ROLES = {"Administrador", "Ventas", "Produccion"}
DASHBOARD_ESTADOS_CONFIRMADOS = {"CONFIRMADO", "PAGADO"}
USERS_ENDPOINT = "admin.usuarios"
ROLES_ENDPOINT = "admin.roles"
SUPPLIERS_ENDPOINT = "admin.proveedores"
SUPPLIER_PHONE_RE = re.compile(r"^[0-9\s\-\(\)\+]+$")
SUPPLIER_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
LOG_LEVEL_OPTIONS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
DEFAULT_LOGS_PAGE_SIZE = 100


def _get_mongo_client_class():
    try:
        module = importlib.import_module("pymongo")
        return getattr(module, "MongoClient")
    except Exception:
        return None


def _is_admin_user() -> bool:
    if not current_user.is_authenticated:
        return False

    return bool(current_user.rol and current_user.rol.nombre == "Administrador")


def _parse_logs_date(value: str | None):
    if not value:
        return utc_today()

    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return utc_today()


def _build_logs_filter(
    *,
    selected_date,
    level_name: str,
    username: str,
    text_query: str,
) -> dict:
    start_dt = datetime.combine(selected_date, time.min, tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)

    filter_doc: dict = {
        "timestamp_utc": {"$gte": start_dt, "$lt": end_dt},
    }
    if level_name:
        filter_doc["level"] = level_name

    regex_clauses = []
    if username:
        escaped_user = re.escape(username)
        regex_clauses.extend(
            [
                {"actor": {"$regex": escaped_user, "$options": "i"}},
                {"user": {"$regex": escaped_user, "$options": "i"}},
                {
                    "message": {
                        "$regex": rf"user={escaped_user}",
                        "$options": "i",
                    }
                },
                {
                    "message": {
                        "$regex": rf"actor={escaped_user}",
                        "$options": "i",
                    }
                },
            ]
        )

    if text_query:
        escaped_text = re.escape(text_query)
        regex_clauses.extend(
            [
                {"message": {"$regex": escaped_text, "$options": "i"}},
                {
                    "formatted_message": {
                        "$regex": escaped_text,
                        "$options": "i",
                    }
                },
                {"event": {"$regex": escaped_text, "$options": "i"}},
            ]
        )

    if regex_clauses:
        filter_doc["$and"] = [{"$or": regex_clauses}]

    return filter_doc


def _query_logs_from_mongo(
    *,
    selected_date,
    level_name: str,
    username: str,
    text_query: str,
    page: int,
):
    if not current_app.config.get("MONGO_LOGS_ENABLED", False):
        return [], 0, ("El logging en MongoDB está deshabilitado " "por configuración.")

    mongo_client_class = _get_mongo_client_class()
    if mongo_client_class is None:
        return [], 0, "No se pudo cargar pymongo en el entorno actual."

    mongo_uri = str(current_app.config.get("MONGO_URI", "")).strip()
    if not mongo_uri:
        return [], 0, "MONGO_URI no está configurado."

    database_name = str(current_app.config.get("MONGO_LOGS_DB", "softbakery")).strip()
    collection_name = str(
        current_app.config.get("MONGO_LOGS_COLLECTION", "app_logs")
    ).strip()
    timeout_ms = int(current_app.config.get("MONGO_LOGS_TIMEOUT_MS", 2000))

    filter_doc = _build_logs_filter(
        selected_date=selected_date,
        level_name=level_name,
        username=username,
        text_query=text_query,
    )

    skip = (page - 1) * DEFAULT_LOGS_PAGE_SIZE
    try:
        with mongo_client_class(
            mongo_uri,
            serverSelectionTimeoutMS=timeout_ms,
        ) as client:
            collection = client[database_name][collection_name]
            total_rows = collection.count_documents(
                filter_doc,
                maxTimeMS=timeout_ms,
            )
            cursor = collection.find(
                filter_doc,
                sort=[("timestamp_utc", -1)],
                skip=skip,
                limit=DEFAULT_LOGS_PAGE_SIZE,
                max_time_ms=timeout_ms,
            )
            docs = list(cursor)
    except Exception:
        current_app.logger.exception("LOGS_VIEW_FAIL|reason=mongo_query_error")
        return [], 0, "No fue posible consultar logs en MongoDB."

    normalized = []
    for doc in docs:
        timestamp = doc.get("timestamp_utc")
        timestamp_display = "-"
        if isinstance(timestamp, datetime):
            timestamp_display = timestamp.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )

        normalized.append(
            {
                "id": str(doc.get("_id", "")),
                "timestamp_display": timestamp_display,
                "level": doc.get("level", "INFO"),
                "event": doc.get("event") or "-",
                "actor": doc.get("actor") or doc.get("user") or "-",
                "role": doc.get("role") or "-",
                "ip": doc.get("ip")
                or (doc.get("request", {}) or {}).get("remote_addr")
                or "-",
                "path": (doc.get("request", {}) or {}).get("path") or "-",
                "method": (doc.get("request", {}) or {}).get("method") or "-",
                "message": doc.get("message", ""),
            }
        )

    return normalized, total_rows, None


def _to_bool(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "on", "si", "yes"}


def _parse_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_dashboard_date(value: str | None):
    if not value:
        return utc_today()
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return utc_today()


def _can_create_user() -> bool:
    permission = (
        Permiso.query.join(Permiso.modulo)
        .filter(
            Permiso.id_rol == current_user.id_rol,
            Permiso.modulo.has(nombre="Usuarios"),
        )
        .first()
    )
    return bool(permission and permission.escritura)


def _can_create_supplier() -> bool:
    permission = (
        Permiso.query.join(Permiso.modulo)
        .filter(
            Permiso.id_rol == current_user.id_rol,
            Permiso.modulo.has(nombre="Proveedores"),
        )
        .first()
    )
    return bool(permission and permission.escritura)


def _normalize_phone_digits(telefono: str) -> str:
    return re.sub(r"\D", "", telefono)


def _is_valid_mx_phone(telefono: str) -> bool:
    if not SUPPLIER_PHONE_RE.fullmatch(telefono):
        return False

    digitos = _normalize_phone_digits(telefono)
    if len(digitos) == 10:
        return True
    return len(digitos) == 12 and digitos.startswith("52")


def _validate_supplier_payload(
    *,
    nombre_proveedor: str,
    nombre_contacto: str,
    telefono: str,
    correo: str,
    ciudad: str,
    estado: str,
    direccion: str,
) -> str | None:
    if not all(
        [
            nombre_proveedor,
            nombre_contacto,
            telefono,
            correo,
            ciudad,
            estado,
            direccion,
        ]
    ):
        return (
            "Completa los campos obligatorios: proveedor, contacto, telefono, "
            "correo, ciudad, estado y direccion."
        )

    if not SUPPLIER_EMAIL_RE.fullmatch(correo):
        return "El correo electronico del proveedor " "no tiene un formato valido."

    if not _is_valid_mx_phone(telefono):
        return (
            "El telefono debe tener formato de Mexico valido: 10 digitos "
            "(ej. 477 123 4567) o +52 con 10 digitos."
        )

    return None


@admin_bp.route("/dashboard")
@login_required
@require_permission("Dashboard", "leer")
def dashboard():
    role_name = current_user.rol.nombre if current_user.rol else ""
    if role_name not in DASHBOARD_ALLOWED_ROLES:
        flash("No tienes acceso al dashboard.", "danger")
        return redirect(url_for("catalog.home"))

    fecha = _parse_dashboard_date(request.args.get("fecha", ""))
    ventas_hoy = (
        Venta.query.filter(db.func.date(Venta.fecha) == fecha)
        .filter(Venta.estado.in_(DASHBOARD_ESTADOS_CONFIRMADOS))
        .all()
    )
    total_ventas = sum(Decimal(str(v.total)) for v in ventas_hoy)
    numero_ventas = len(ventas_hoy)

    productos_mas_vendidos = (
        db.session.query(
            Producto.nombre.label("producto"),
            db.func.sum(DetalleVenta.cantidad).label("cantidad_total"),
        )
        .join(DetalleVenta, DetalleVenta.id_producto == Producto.id_producto)
        .join(Venta, Venta.id_venta == DetalleVenta.id_venta)
        .filter(db.func.date(Venta.fecha) == fecha)
        .filter(Venta.estado.in_(DASHBOARD_ESTADOS_CONFIRMADOS))
        .group_by(Producto.id_producto, Producto.nombre)
        .order_by(
            db.func.sum(DetalleVenta.cantidad).desc(),
            Producto.nombre.asc(),
        )
        .limit(8)
        .all()
    )

    producto_mas_vendido = "Sin datos"
    producto_mas_vendido_cantidad = 0
    if productos_mas_vendidos:
        producto_mas_vendido = productos_mas_vendidos[0].producto
        producto_mas_vendido_cantidad = int(
            productos_mas_vendidos[0].cantidad_total or 0
        )

    ventas_por_hora = (
        db.session.query(
            db.func.hour(Venta.fecha).label("hora"),
            db.func.sum(Venta.total).label("total"),
            db.func.count(Venta.id_venta).label("transacciones"),
        )
        .filter(db.func.date(Venta.fecha) == fecha)
        .filter(Venta.estado.in_(DASHBOARD_ESTADOS_CONFIRMADOS))
        .group_by(db.func.hour(Venta.fecha))
        .order_by(db.func.hour(Venta.fecha).asc())
        .all()
    )

    ventas_recientes = (
        db.session.query(
            Venta.fecha,
            Producto.nombre.label("producto"),
            Producto.unidad_venta.label("presentacion"),
            DetalleVenta.cantidad,
            DetalleVenta.subtotal.label("total_linea"),
        )
        .join(DetalleVenta, DetalleVenta.id_venta == Venta.id_venta)
        .join(Producto, Producto.id_producto == DetalleVenta.id_producto)
        .filter(db.func.date(Venta.fecha) == fecha)
        .filter(Venta.estado.in_(DASHBOARD_ESTADOS_CONFIRMADOS))
        .order_by(Venta.fecha.desc(), Venta.id_venta.desc())
        .limit(20)
        .all()
    )

    presentaciones_mas_vendidas = (
        db.session.query(
            db.func.coalesce(
                Producto.unidad_venta,
                "Sin presentación",
            ).label("presentacion"),
            db.func.sum(DetalleVenta.cantidad).label("cantidad_total"),
        )
        .join(DetalleVenta, DetalleVenta.id_producto == Producto.id_producto)
        .join(Venta, Venta.id_venta == DetalleVenta.id_venta)
        .filter(db.func.date(Venta.fecha) == fecha)
        .filter(Venta.estado.in_(DASHBOARD_ESTADOS_CONFIRMADOS))
        .group_by(db.func.coalesce(Producto.unidad_venta, "Sin presentación"))
        .order_by(
            db.func.sum(DetalleVenta.cantidad).desc(),
            db.func.coalesce(Producto.unidad_venta, "Sin presentación").asc(),
        )
        .limit(8)
        .all()
    )

    low_stock = (
        Producto.query.filter(
            Producto.activo.is_(True),
            Producto.cantidad_disponible < Producto.stock_minimo,
        )
        .order_by(Producto.cantidad_disponible.asc())
        .all()
    )

    pedidos_hoy = Pedido.query.filter(db.func.date(Pedido.fecha_pedido) == fecha).all()
    pedidos_pendientes = len(
        [p for p in pedidos_hoy if p.estado_pedido in {"PENDIENTE", "CONFIRMADO"}]
    )

    stats = {
        "total_ventas_hoy": total_ventas,
        "numero_ventas_hoy": numero_ventas,
        "producto_mas_vendido": producto_mas_vendido,
        "producto_mas_vendido_cantidad": producto_mas_vendido_cantidad,
        "pedidos_pendientes": pedidos_pendientes,
        "productos_bajo_minimo": len(low_stock),
    }

    ventas_por_hora_chart = [
        {
            "hora": int(row.hora or 0),
            "total": float(row.total or 0),
            "transacciones": int(row.transacciones or 0),
        }
        for row in ventas_por_hora
    ]
    productos_mas_vendidos_chart = [
        {
            "nombre": row.producto,
            "cantidad": int(row.cantidad_total or 0),
        }
        for row in productos_mas_vendidos
    ]
    presentaciones_mas_vendidas_chart = [
        {
            "nombre": row.presentacion,
            "cantidad": int(row.cantidad_total or 0),
        }
        for row in presentaciones_mas_vendidas
    ]

    return render_template(
        "admin/dashboard.html",
        fecha_consulta=fecha,
        fecha_consulta_iso=fecha.isoformat(),
        role_name=role_name,
        stats=stats,
        low_stock=low_stock,
        ventas_por_hora=ventas_por_hora,
        productos_mas_vendidos=productos_mas_vendidos,
        ventas_recientes=ventas_recientes,
        presentaciones_mas_vendidas=presentaciones_mas_vendidas,
        ventas_por_hora_chart=ventas_por_hora_chart,
        productos_mas_vendidos_chart=productos_mas_vendidos_chart,
        presentaciones_mas_vendidas_chart=presentaciones_mas_vendidas_chart,
    )


@admin_bp.route("/logs")
@login_required
def logs_view():
    if not _is_admin_user():
        flash(
            "Solo administración puede consultar logs del sistema.",
            "danger",
        )
        return redirect(url_for("admin.dashboard"))

    selected_date = _parse_logs_date(request.args.get("fecha"))
    username = (request.args.get("usuario") or "").strip()
    text_query = (request.args.get("q") or "").strip()
    level_requested = (request.args.get("nivel") or "").strip().upper()
    level_name = level_requested if level_requested in LOG_LEVEL_OPTIONS else ""
    page = max(_parse_int(request.args.get("page", "1"), 1), 1)

    logs_rows, total_rows, error_message = _query_logs_from_mongo(
        selected_date=selected_date,
        level_name=level_name,
        username=username,
        text_query=text_query,
        page=page,
    )
    total_pages = max(
        (total_rows + DEFAULT_LOGS_PAGE_SIZE - 1) // DEFAULT_LOGS_PAGE_SIZE,
        1,
    )
    has_prev = page > 1
    has_next = page < total_pages

    current_app.logger.info(
        "LOGS_VIEW|user=%s|date=%s|level=%s|rows=%s|page=%s",
        current_user.username,
        selected_date.isoformat(),
        level_name or "ALL",
        len(logs_rows),
        page,
    )
    log_audit_event(
        "LOGS_CONSULTA",
        (
            f"fecha={selected_date.isoformat()}; nivel={level_name or 'ALL'}; "
            f"usuario={username or '-'}; q={text_query or '-'}; pagina={page}"
        ),
    )

    return render_template(
        "admin/logs.html",
        logs_rows=logs_rows,
        error_message=error_message,
        fecha_consulta_iso=selected_date.isoformat(),
        nivel_consulta=level_name,
        usuario_consulta=username,
        texto_consulta=text_query,
        level_options=LOG_LEVEL_OPTIONS,
        total_rows=total_rows,
        total_pages=total_pages,
        current_page=page,
        has_prev=has_prev,
        has_next=has_next,
    )


@admin_bp.route("/usuarios", methods=["GET", "POST"])
@login_required
@require_permission("Usuarios", "leer")
def usuarios():
    roles = Rol.query.filter_by(activo=True).order_by(Rol.nombre.asc()).all()
    form_crear = UsuarioCrearForm(prefix="crear")
    form_crear.id_rol.choices = [(r.id_rol, r.nombre) for r in roles]

    if request.method == "POST" and "crear-username" in request.form:
        if not _can_create_user():
            flash("No tienes permiso para crear usuarios.", "danger")
            return redirect(url_for(USERS_ENDPOINT))

        if form_crear.validate_on_submit():
            username = form_crear.username.data.strip()
            password = form_crear.password.data
            id_rol = form_crear.id_rol.data
            nombre = form_crear.nombre.data.strip()
            apellidos = form_crear.apellidos.data.strip()
            telefono = form_crear.telefono.data.strip()
            correo = form_crear.correo.data.strip().lower()
            direccion = (form_crear.direccion.data or "").strip() or "N/A"
            ciudad = (form_crear.ciudad.data or "").strip() or "N/A"

            if is_password_insecure(password):
                form_crear.password.errors.append(
                    "La contraseña es demasiado común o insegura. Elige una diferente."
                )
            elif Usuario.query.filter_by(username=username).first():
                form_crear.username.errors.append("El nombre de usuario ya existe.")
            elif Persona.query.filter_by(correo=correo).first():
                form_crear.correo.errors.append("El correo ya está registrado.")
            else:
                persona = Persona(
                    nombre=nombre,
                    apellidos=apellidos,
                    telefono=telefono,
                    correo=correo,
                    direccion=direccion,
                    ciudad=ciudad,
                )
                db_user = Usuario(
                    persona=persona, id_rol=id_rol, username=username, activo=True
                )
                db_user.set_password(password)
                db.session.add(persona)
                db.session.add(db_user)
                db.session.commit()
                log_audit_event(
                    "USUARIO_CREADO",
                    f"id_usuario={db_user.id_usuario}; username={db_user.username}; id_rol={db_user.id_rol}",
                )
                flash("Usuario creado correctamente.", "success")
                return redirect(url_for(USERS_ENDPOINT))

        # Validation failed — reopen modal with errors
        search = request.args.get("q", "").strip().lower()
        query = Usuario.query.join(Persona).order_by(Usuario.id_usuario.desc())
        if search:
            query = query.filter(
                (Usuario.username.ilike(f"%{search}%"))
                | (Persona.nombre.ilike(f"%{search}%"))
                | (Persona.apellidos.ilike(f"%{search}%"))
            )
        return render_template(
            "admin/usuarios.html",
            usuarios=query.all(),
            roles=roles,
            q=search,
            form_crear=form_crear,
            open_modal="modalNuevoUsuario",
        )

    search = request.args.get("q", "").strip().lower()
    query = Usuario.query.join(Persona).order_by(Usuario.id_usuario.desc())
    if search:
        query = query.filter(
            (Usuario.username.ilike(f"%{search}%"))
            | (Persona.nombre.ilike(f"%{search}%"))
            | (Persona.apellidos.ilike(f"%{search}%"))
        )

    data = query.all()
    return render_template(
        "admin/usuarios.html",
        usuarios=data,
        roles=roles,
        q=search,
        form_crear=form_crear,
    )


@admin_bp.post("/usuarios/<int:id_usuario>/editar")
@login_required
@require_permission("Usuarios", "editar")
def editar_usuario(id_usuario: int):
    user = Usuario.query.get_or_404(id_usuario)
    if user.id_usuario == current_user.id_usuario:
        flash("No puedes editarte a ti mismo desde este formulario.", "warning")
        return redirect(url_for(USERS_ENDPOINT))

    roles = Rol.query.filter_by(activo=True).order_by(Rol.nombre.asc()).all()
    form_editar = UsuarioEditarForm(prefix="editar")
    form_editar.id_rol.choices = [(r.id_rol, r.nombre) for r in roles]

    if form_editar.validate_on_submit():
        user.id_rol = form_editar.id_rol.data
        user.persona.nombre = form_editar.nombre.data.strip()
        user.persona.apellidos = form_editar.apellidos.data.strip()
        user.persona.telefono = form_editar.telefono.data.strip()
        user.persona.ciudad = (form_editar.ciudad.data or "").strip()
        user.persona.direccion = (form_editar.direccion.data or "").strip()
        db.session.commit()
        log_audit_event(
            "USUARIO_EDITADO",
            f"id_usuario={user.id_usuario}; username={user.username}; id_rol={user.id_rol}",
        )
        flash("Usuario actualizado.", "success")
        return redirect(url_for(USERS_ENDPOINT))

    # Validation failed — reopen the edit modal
    search = request.args.get("q", "").strip().lower()
    query = Usuario.query.join(Persona).order_by(Usuario.id_usuario.desc())
    form_crear = UsuarioCrearForm(prefix="crear")
    form_crear.id_rol.choices = [(r.id_rol, r.nombre) for r in roles]
    return render_template(
        "admin/usuarios.html",
        usuarios=query.all(),
        roles=roles,
        q=search,
        form_crear=form_crear,
        form_editar=form_editar,
        open_modal=f"modalEditarUsuario-{id_usuario}",
        edit_usuario_id=id_usuario,
    )


@admin_bp.post("/usuarios/<int:id_usuario>/desactivar")
@login_required
@require_permission("Usuarios", "desactivar")
def desactivar_usuario(id_usuario: int):
    user = Usuario.query.get_or_404(id_usuario)
    if user.id_usuario == current_user.id_usuario:
        flash("No puedes desactivar tu propio usuario.", "warning")
        return redirect(url_for(USERS_ENDPOINT))

    user.activo = False
    db.session.commit()
    log_audit_event(
        "USUARIO_DESACTIVADO",
        f"id_usuario={user.id_usuario}; username={user.username}",
    )
    flash("Usuario desactivado.", "success")
    return redirect(url_for(USERS_ENDPOINT))


@admin_bp.post("/usuarios/<int:id_usuario>/activar")
@login_required
@require_permission("Usuarios", "editar")
def activar_usuario(id_usuario: int):
    user = Usuario.query.get_or_404(id_usuario)
    user.activo = True
    db.session.commit()
    log_audit_event(
        "USUARIO_ACTIVADO",
        f"id_usuario={user.id_usuario}; username={user.username}",
    )
    flash("Usuario activado correctamente.", "success")
    return redirect(url_for(USERS_ENDPOINT))


@admin_bp.route("/roles", methods=["GET", "POST"])
@login_required
@require_permission("Roles", "leer")
def roles():
    form_crear = RolCrearForm(prefix="crear")

    if request.method == "POST" and "crear-nombre" in request.form:
        if form_crear.validate_on_submit():
            nombre = form_crear.nombre.data.strip()
            descripcion = form_crear.descripcion.data.strip()

            if Rol.query.filter_by(nombre=nombre).first():
                form_crear.nombre.errors.append("Ya existe un rol con ese nombre.")
            else:
                db.session.add(
                    Rol(nombre=nombre, descripcion=descripcion, es_base=False)
                )
                db.session.commit()
                role = Rol.query.filter_by(nombre=nombre).first()
                if role:
                    log_audit_event(
                        "ROL_CREADO",
                        f"id_rol={role.id_rol}; nombre={role.nombre}",
                    )
                flash("Rol creado correctamente.", "success")
                return redirect(url_for(ROLES_ENDPOINT))

        # Validation failed — reopen modal
        data = Rol.query.order_by(Rol.id_rol.asc()).all()
        modulos_data = (
            Modulo.query.filter_by(activo=True).order_by(Modulo.nombre.asc()).all()
        )
        modulos = [
            {"id_modulo": m.id_modulo, "nombre": m.nombre, "descripcion": None}
            for m in modulos_data
        ]
        usuarios_activos_por_rol = {
            r.id_rol: Usuario.query.filter_by(id_rol=r.id_rol, activo=True).count()
            for r in data
        }
        return render_template(
            "admin/roles.html",
            roles=data,
            modulos=modulos,
            usuarios_activos_por_rol=usuarios_activos_por_rol,
            form_crear=form_crear,
            open_modal="modalNuevoRol",
        )

    data = Rol.query.order_by(Rol.id_rol.asc()).all()
    modulos_data = (
        Modulo.query.filter_by(activo=True).order_by(Modulo.nombre.asc()).all()
    )
    modulos = [
        {
            "id_modulo": m.id_modulo,
            "nombre": m.nombre,
            "descripcion": None,
        }
        for m in modulos_data
    ]
    usuarios_activos_por_rol = {
        r.id_rol: Usuario.query.filter_by(id_rol=r.id_rol, activo=True).count()
        for r in data
    }

    return render_template(
        "admin/roles.html",
        roles=data,
        modulos=modulos,
        usuarios_activos_por_rol=usuarios_activos_por_rol,
        form_crear=form_crear,
    )


@admin_bp.post("/roles/<int:id_rol>/editar")
@login_required
@require_permission("Roles", "editar")
def editar_rol(id_rol: int):
    role = Rol.query.get_or_404(id_rol)
    form_editar = RolEditarForm(prefix="editar")

    if form_editar.validate_on_submit():
        role.descripcion = form_editar.descripcion.data.strip()
        if role.nombre not in BASE_ROLES:
            role.activo = _to_bool(request.form.get("activo", "on"))
        db.session.commit()
        log_audit_event(
            "ROL_EDITADO",
            f"id_rol={role.id_rol}; nombre={role.nombre}; activo={role.activo}",
        )
        flash("Rol actualizado.", "success")
        return redirect(url_for(ROLES_ENDPOINT))

    # Validation failed — reopen edit modal
    data = Rol.query.order_by(Rol.id_rol.asc()).all()
    modulos_data = (
        Modulo.query.filter_by(activo=True).order_by(Modulo.nombre.asc()).all()
    )
    modulos = [
        {"id_modulo": m.id_modulo, "nombre": m.nombre, "descripcion": None}
        for m in modulos_data
    ]
    usuarios_activos_por_rol = {
        r.id_rol: Usuario.query.filter_by(id_rol=r.id_rol, activo=True).count()
        for r in data
    }
    form_crear = RolCrearForm(prefix="crear")
    return render_template(
        "admin/roles.html",
        roles=data,
        modulos=modulos,
        usuarios_activos_por_rol=usuarios_activos_por_rol,
        form_crear=form_crear,
        form_editar=form_editar,
        open_modal=f"modalEditarRol-{id_rol}",
        edit_rol_id=id_rol,
    )


@admin_bp.post("/roles/<int:id_rol>/desactivar")
@login_required
@require_permission("Roles", "desactivar")
def desactivar_rol(id_rol: int):
    role = Rol.query.get_or_404(id_rol)
    if role.nombre in BASE_ROLES:
        flash("No puedes desactivar roles base.", "warning")
        return redirect(url_for(ROLES_ENDPOINT))

    role.activo = False
    db.session.commit()
    log_audit_event(
        "ROL_DESACTIVADO",
        f"id_rol={role.id_rol}; nombre={role.nombre}",
    )
    flash("Rol desactivado.", "success")
    return redirect(url_for(ROLES_ENDPOINT))


@admin_bp.post("/roles/<int:id_rol>/activar")
@login_required
@require_permission("Roles", "editar")
def activar_rol(id_rol: int):
    role = Rol.query.get_or_404(id_rol)
    if role.nombre in BASE_ROLES:
        flash("No puedes modificar roles base.", "warning")
        return redirect(url_for(ROLES_ENDPOINT))

    role.activo = True
    db.session.commit()
    log_audit_event(
        "ROL_ACTIVADO",
        f"id_rol={role.id_rol}; nombre={role.nombre}",
    )
    flash("Rol activado.", "success")
    return redirect(url_for(ROLES_ENDPOINT))


@admin_bp.post("/roles/<int:id_rol>/permisos")
@login_required
@require_permission("Roles", "editar")
def rol_permisos(id_rol: int):
    role = Rol.query.get_or_404(id_rol)
    if role.nombre in BASE_ROLES:
        return (
            jsonify(
                {
                    "ok": False,
                    "mensaje": "No se pueden modificar permisos de roles base.",
                }
            ),
            400,
        )

    data = request.get_json(silent=True) or {}
    permisos_nuevos = data.get("permisos", [])

    has_any_permission = any(
        bool(item.get("leer"))
        or bool(item.get("crear"))
        or bool(item.get("editar"))
        or bool(item.get("desactivar"))
        for item in permisos_nuevos
    )
    permisos_activos = sum(
        1
        for item in permisos_nuevos
        if bool(item.get("leer"))
        or bool(item.get("crear"))
        or bool(item.get("editar"))
        or bool(item.get("desactivar"))
    )
    if not has_any_permission:
        return (
            jsonify(
                {
                    "ok": False,
                    "mensaje": "El rol debe tener al menos un permiso asignado.",
                }
            ),
            400,
        )

    Permiso.query.filter_by(id_rol=role.id_rol).delete()

    for item in permisos_nuevos:
        id_modulo = _parse_int(str(item.get("id_modulo", "0")), 0)
        if id_modulo <= 0:
            continue

        modulo = Modulo.query.get(id_modulo)
        if not modulo or not modulo.activo:
            continue

        db.session.add(
            Permiso(
                id_rol=role.id_rol,
                id_modulo=id_modulo,
                lectura=bool(item.get("leer")),
                escritura=bool(item.get("crear")),
                actualizacion=bool(item.get("editar")),
                eliminacion=bool(item.get("desactivar")),
            )
        )

    db.session.commit()
    log_audit_event(
        "ROL_PERMISOS_ACTUALIZADOS",
        f"id_rol={role.id_rol}; permisos_activos={permisos_activos}",
    )
    return jsonify({"ok": True, "mensaje": "Permisos guardados correctamente."})


@admin_bp.route("/proveedores", methods=["GET", "POST"])
@login_required
@require_permission("Proveedores", "leer")
def proveedores():
    form = ProveedorForm()
    if form.validate_on_submit():
        if not _can_create_supplier():
            flash("No tienes permiso para registrar proveedores.", "danger")
            return redirect(url_for(SUPPLIERS_ENDPOINT))

        nombre = form.nombre_empresa.data.strip()
        nombre_contacto = form.nombre_contacto.data.strip()
        telefono = form.telefono.data.strip()
        correo = form.correo.data.strip().lower()
        ciudad = form.ciudad.data.strip()
        estado = form.estado.data.strip()
        direccion = form.direccion.data.strip()

        error_message = _validate_supplier_payload(
            nombre_proveedor=nombre,
            nombre_contacto=nombre_contacto,
            telefono=telefono,
            correo=correo,
            ciudad=ciudad,
            estado=estado,
            direccion=direccion,
        )
        if error_message:
            flash(error_message, "warning")
            return redirect(url_for(SUPPLIERS_ENDPOINT))

        proveedor_existente = Proveedor.query.filter(
            db.func.lower(Proveedor.nombre_empresa) == nombre.lower()
        ).first()
        if proveedor_existente:
            flash("Ya existe un proveedor con ese nombre.", "danger")
            return redirect(url_for(SUPPLIERS_ENDPOINT))

        correo_existente = Proveedor.query.filter(
            db.func.lower(Proveedor.correo) == correo.lower()
        ).first()
        if correo_existente:
            flash("El correo ya esta registrado en otro proveedor.", "danger")
            return redirect(url_for(SUPPLIERS_ENDPOINT))

        db.session.add(
            Proveedor(
                nombre_empresa=nombre,
                nombre_contacto=nombre_contacto,
                telefono=telefono,
                correo=correo,
                ciudad=ciudad,
                estado=estado,
                direccion=direccion,
                activo=True,
            )
        )
        db.session.commit()
        proveedor = Proveedor.query.filter_by(nombre_empresa=nombre).first()
        if proveedor:
            log_audit_event(
                "PROVEEDOR_CREADO",
                f"id_proveedor={proveedor.id_proveedor}; nombre_empresa={proveedor.nombre_empresa}",
            )
        flash("Proveedor registrado.", "success")
        return redirect(url_for(SUPPLIERS_ENDPOINT))

    search = request.args.get("q", "").strip().lower()
    query = Proveedor.query.order_by(Proveedor.id_proveedor.desc())
    if search:
        query = query.filter(Proveedor.nombre_empresa.ilike(f"%{search}%"))
    data = query.all()
    return render_template(
        "admin/proveedores.html",
        proveedores=data,
        q=search,
        form=form,
    )


@admin_bp.post("/proveedores/<int:id_proveedor>/editar")
@login_required
@require_permission("Proveedores", "editar")
def editar_proveedor(id_proveedor: int):
    proveedor = Proveedor.query.get_or_404(id_proveedor)
    form_editar = ProveedorForm(prefix="editar")

    if form_editar.validate_on_submit():
        nombre = form_editar.nombre_empresa.data.strip()
        nombre_contacto = form_editar.nombre_contacto.data.strip()
        telefono = form_editar.telefono.data.strip()
        correo = form_editar.correo.data.strip().lower()
        ciudad = form_editar.ciudad.data.strip()
        estado = form_editar.estado.data.strip()
        direccion = form_editar.direccion.data.strip()

        proveedor_existente = Proveedor.query.filter(
            db.func.lower(Proveedor.nombre_empresa) == nombre.lower(),
            Proveedor.id_proveedor != proveedor.id_proveedor,
        ).first()
        if proveedor_existente:
            form_editar.nombre_empresa.errors.append(
                "Ya existe un proveedor con ese nombre."
            )
        else:
            correo_existente = Proveedor.query.filter(
                db.func.lower(Proveedor.correo) == correo.lower(),
                Proveedor.id_proveedor != proveedor.id_proveedor,
            ).first()
            if correo_existente:
                form_editar.correo.errors.append(
                    "El correo ya está registrado en otro proveedor."
                )
            else:
                proveedor.nombre_empresa = nombre
                proveedor.nombre_contacto = nombre_contacto
                proveedor.telefono = telefono
                proveedor.correo = correo
                proveedor.ciudad = ciudad
                proveedor.estado = estado
                proveedor.direccion = direccion
                if "editar-activo" in request.form:
                    proveedor.activo = _to_bool(
                        request.form.get("editar-activo", "off")
                    )
                db.session.commit()
                log_audit_event(
                    "PROVEEDOR_EDITADO",
                    f"id_proveedor={proveedor.id_proveedor}; nombre_empresa={proveedor.nombre_empresa}; activo={proveedor.activo}",
                )
                flash("Proveedor actualizado.", "success")
                return redirect(url_for(SUPPLIERS_ENDPOINT))

    # Validation failed — reopen edit modal
    form_crear = ProveedorForm()
    search = request.args.get("q", "").strip().lower()
    query = Proveedor.query.order_by(Proveedor.id_proveedor.desc())
    if search:
        query = query.filter(Proveedor.nombre_empresa.ilike(f"%{search}%"))
    return render_template(
        "admin/proveedores.html",
        proveedores=query.all(),
        q=search,
        form=form_crear,
        form_editar=form_editar,
        open_modal_editar=id_proveedor,
    )


@admin_bp.post("/proveedores/<int:id_proveedor>/desactivar")
@login_required
@require_permission("Proveedores", "desactivar")
def desactivar_proveedor(id_proveedor: int):
    proveedor = Proveedor.query.get_or_404(id_proveedor)
    proveedor.activo = False
    db.session.commit()
    log_audit_event(
        "PROVEEDOR_DESACTIVADO",
        f"id_proveedor={proveedor.id_proveedor}; nombre_empresa={proveedor.nombre_empresa}",
    )
    flash("Proveedor desactivado.", "success")
    return redirect(url_for(SUPPLIERS_ENDPOINT))


@admin_bp.post("/proveedores/<int:id_proveedor>/activar")
@login_required
@require_permission("Proveedores", "editar")
def activar_proveedor(id_proveedor: int):
    proveedor = Proveedor.query.get_or_404(id_proveedor)
    proveedor.activo = True
    db.session.commit()
    log_audit_event(
        "PROVEEDOR_ACTIVADO",
        f"id_proveedor={proveedor.id_proveedor}; nombre_empresa={proveedor.nombre_empresa}",
    )
    flash("Proveedor activado.", "success")
    return redirect(url_for(SUPPLIERS_ENDPOINT))
