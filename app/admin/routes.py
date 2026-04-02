from __future__ import annotations

from decimal import Decimal

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.admin import admin_bp
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
USERS_ENDPOINT = "admin.usuarios"
ROLES_ENDPOINT = "admin.roles"
SUPPLIERS_ENDPOINT = "admin.proveedores"


def _to_bool(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "on", "si", "yes"}


def _parse_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


@admin_bp.route("/dashboard")
@login_required
@require_permission("Dashboard", "leer")
def dashboard():
    role_name = current_user.rol.nombre if current_user.rol else ""
    if role_name not in {"Administrador", "Ventas", "Produccion"}:
        flash("No tienes acceso al dashboard.", "danger")
        return redirect(url_for("catalog.home"))

    fecha = utc_today()
    ventas_hoy = Venta.query.filter(db.func.date(Venta.fecha) == fecha).all()
    total_ventas = sum(Decimal(str(v.total)) for v in ventas_hoy)
    numero_ventas = len(ventas_hoy)

    producto_mas_vendido = None
    producto_mas_vendido_id = None
    if ventas_hoy:
        rows = (
            db.session.query(
                DetalleVenta.id_producto, db.func.sum(DetalleVenta.cantidad)
            )
            .join(Venta, Venta.id_venta == DetalleVenta.id_venta)
            .filter(db.func.date(Venta.fecha) == fecha)
            .group_by(DetalleVenta.id_producto)
            .order_by(db.func.sum(DetalleVenta.cantidad).desc())
            .all()
        )
        if rows:
            producto_mas_vendido_id = rows[0][0]
            producto_mas_vendido = Producto.query.get(producto_mas_vendido_id)

    ventas_por_hora = (
        db.session.query(
            db.func.hour(Venta.fecha).label("hora"),
            db.func.sum(Venta.total).label("total"),
            db.func.count(Venta.id_venta).label("transacciones"),
        )
        .filter(db.func.date(Venta.fecha) == fecha)
        .group_by(db.func.hour(Venta.fecha))
        .order_by(db.func.hour(Venta.fecha).asc())
        .all()
    )

    ventas_recientes = (
        db.session.query(
            Venta.id_venta,
            Venta.fecha,
            Producto.nombre,
            DetalleVenta.cantidad,
            DetalleVenta.subtotal,
        )
        .join(DetalleVenta, DetalleVenta.id_venta == Venta.id_venta)
        .join(Producto, Producto.id_producto == DetalleVenta.id_producto)
        .filter(db.func.date(Venta.fecha) == fecha)
        .order_by(Venta.fecha.desc(), Venta.id_venta.desc())
        .limit(20)
        .all()
    )

    presentaciones_mas_vendidas = (
        db.session.query(
            Producto.unidad_venta,
            db.func.sum(DetalleVenta.cantidad).label("cantidad_total"),
        )
        .join(DetalleVenta, DetalleVenta.id_producto == Producto.id_producto)
        .join(Venta, Venta.id_venta == DetalleVenta.id_venta)
        .filter(db.func.date(Venta.fecha) == fecha)
        .group_by(Producto.unidad_venta)
        .order_by(db.func.sum(DetalleVenta.cantidad).desc())
        .all()
    )

    historial_ventas_productos = (
        db.session.query(
            Venta.fecha,
            Producto.nombre,
            DetalleVenta.cantidad,
        )
        .join(DetalleVenta, DetalleVenta.id_venta == Venta.id_venta)
        .join(Producto, Producto.id_producto == DetalleVenta.id_producto)
        .filter(db.func.date(Venta.fecha) == fecha)
        .order_by(Venta.fecha.desc(), Venta.id_venta.desc())
        .limit(20)
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
        "producto_mas_vendido": (
            producto_mas_vendido.nombre if producto_mas_vendido else "Sin datos"
        ),
        "producto_mas_vendido_id": producto_mas_vendido_id,
        "pedidos_pendientes": pedidos_pendientes,
        "productos_bajo_minimo": len(low_stock),
    }
    return render_template(
        "admin/dashboard.html",
        role_name=role_name,
        stats=stats,
        low_stock=low_stock,
        ventas_por_hora=ventas_por_hora,
        ventas_recientes=ventas_recientes,
        presentaciones_mas_vendidas=presentaciones_mas_vendidas,
        historial_ventas_productos=historial_ventas_productos,
    )


@admin_bp.route("/usuarios", methods=["GET", "POST"])
@login_required
@require_permission("Usuarios", "leer")
def usuarios():
    if request.method == "POST":
        if not _can_create_user():
            flash("No tienes permiso para crear usuarios.", "danger")
            return redirect(url_for(USERS_ENDPOINT))

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        id_rol = _parse_int(request.form.get("id_rol", "0"))
        nombre = request.form.get("nombre", "").strip()
        apellidos = request.form.get("apellidos", "").strip()
        telefono = request.form.get("telefono", "").strip()
        correo = request.form.get("correo", "").strip().lower()
        direccion = request.form.get("direccion", "").strip() or "N/A"
        ciudad = request.form.get("ciudad", "").strip() or "N/A"

        if not all([username, password, nombre, apellidos, telefono, correo, id_rol]):
            flash("Completa todos los campos obligatorios.", "warning")
            return redirect(url_for(USERS_ENDPOINT))

        if is_password_insecure(password):
            flash(
                "La contraseña es demasiado común o insegura. Elige una diferente.",
                "danger",
            )
            return redirect(url_for(USERS_ENDPOINT))

        if Usuario.query.filter_by(username=username).first():
            flash("El usuario ya existe.", "danger")
            return redirect(url_for(USERS_ENDPOINT))

        if Persona.query.filter_by(correo=correo).first():
            flash("El correo ya esta registrado.", "danger")
            return redirect(url_for(USERS_ENDPOINT))

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

    search = request.args.get("q", "").strip().lower()
    query = Usuario.query.join(Persona).order_by(Usuario.id_usuario.desc())
    if search:
        query = query.filter(
            (Usuario.username.ilike(f"%{search}%"))
            | (Persona.nombre.ilike(f"%{search}%"))
            | (Persona.apellidos.ilike(f"%{search}%"))
        )

    data = query.all()
    roles = Rol.query.filter_by(activo=True).order_by(Rol.nombre.asc()).all()
    return render_template(
        "admin/usuarios.html",
        usuarios=data,
        roles=roles,
        q=search,
    )


@admin_bp.post("/usuarios/<int:id_usuario>/editar")
@login_required
@require_permission("Usuarios", "editar")
def editar_usuario(id_usuario: int):
    user = Usuario.query.get_or_404(id_usuario)
    if user.id_usuario == current_user.id_usuario:
        flash("No puedes editarte a ti mismo desde este formulario.", "warning")
        return redirect(url_for(USERS_ENDPOINT))

    user.id_rol = _parse_int(request.form.get("id_rol", str(user.id_rol)), user.id_rol)
    user.activo = _to_bool(request.form.get("activo", "on"))
    user.persona.nombre = request.form.get("nombre", user.persona.nombre).strip()
    user.persona.apellidos = request.form.get(
        "apellidos", user.persona.apellidos
    ).strip()
    user.persona.telefono = request.form.get("telefono", user.persona.telefono).strip()
    user.persona.ciudad = request.form.get("ciudad", user.persona.ciudad).strip()
    user.persona.direccion = request.form.get(
        "direccion", user.persona.direccion
    ).strip()
    db.session.commit()
    log_audit_event(
        "USUARIO_EDITADO",
        f"id_usuario={user.id_usuario}; username={user.username}; id_rol={user.id_rol}; activo={user.activo}",
    )
    flash("Usuario actualizado.", "success")
    return redirect(url_for(USERS_ENDPOINT))


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


@admin_bp.route("/roles", methods=["GET", "POST"])
@login_required
@require_permission("Roles", "leer")
def roles():
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        descripcion = request.form.get("descripcion", "").strip()
        if not nombre or not descripcion:
            flash("Nombre y descripcion son obligatorios.", "warning")
            return redirect(url_for(ROLES_ENDPOINT))

        if Rol.query.filter_by(nombre=nombre).first():
            flash("El rol ya existe.", "danger")
            return redirect(url_for(ROLES_ENDPOINT))

        db.session.add(Rol(nombre=nombre, descripcion=descripcion, es_base=False))
        db.session.commit()
        role = Rol.query.filter_by(nombre=nombre).first()
        if role:
            log_audit_event(
                "ROL_CREADO",
                f"id_rol={role.id_rol}; nombre={role.nombre}",
            )
        flash("Rol creado correctamente.", "success")
        return redirect(url_for(ROLES_ENDPOINT))

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
    )


@admin_bp.post("/roles/<int:id_rol>/editar")
@login_required
@require_permission("Roles", "editar")
def editar_rol(id_rol: int):
    role = Rol.query.get_or_404(id_rol)
    role.descripcion = request.form.get("descripcion", role.descripcion).strip()
    if role.nombre not in BASE_ROLES:
        role.activo = _to_bool(request.form.get("activo", "on"))
    db.session.commit()
    log_audit_event(
        "ROL_EDITADO",
        f"id_rol={role.id_rol}; nombre={role.nombre}; activo={role.activo}",
    )
    flash("Rol actualizado.", "success")
    return redirect(url_for(ROLES_ENDPOINT))


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
    if request.method == "POST":
        nombre = request.form.get("nombre_empresa", "").strip()
        telefono = request.form.get("telefono", "").strip()
        correo = request.form.get("correo", "").strip().lower()
        direccion = request.form.get("direccion", "").strip()
        if not all([nombre, telefono, correo, direccion]):
            flash("Completa todos los campos del proveedor.", "warning")
            return redirect(url_for(SUPPLIERS_ENDPOINT))

        if "@" not in correo or "." not in correo:
            flash("El correo del proveedor no tiene un formato valido.", "warning")
            return redirect(url_for(SUPPLIERS_ENDPOINT))

        if Proveedor.query.filter_by(nombre_empresa=nombre).first():
            flash("Ya existe un proveedor con ese nombre.", "danger")
            return redirect(url_for(SUPPLIERS_ENDPOINT))

        db.session.add(
            Proveedor(
                nombre_empresa=nombre,
                telefono=telefono,
                correo=correo,
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
    )


@admin_bp.post("/proveedores/<int:id_proveedor>/editar")
@login_required
@require_permission("Proveedores", "editar")
def editar_proveedor(id_proveedor: int):
    proveedor = Proveedor.query.get_or_404(id_proveedor)
    proveedor.nombre_empresa = request.form.get(
        "nombre_empresa", proveedor.nombre_empresa
    ).strip()
    proveedor.telefono = request.form.get("telefono", proveedor.telefono).strip()
    proveedor.correo = request.form.get("correo", proveedor.correo).strip().lower()
    proveedor.direccion = request.form.get("direccion", proveedor.direccion).strip()
    proveedor.activo = _to_bool(request.form.get("activo", "on"))
    db.session.commit()
    log_audit_event(
        "PROVEEDOR_EDITADO",
        f"id_proveedor={proveedor.id_proveedor}; nombre_empresa={proveedor.nombre_empresa}; activo={proveedor.activo}",
    )
    flash("Proveedor actualizado.", "success")
    return redirect(url_for(SUPPLIERS_ENDPOINT))


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
