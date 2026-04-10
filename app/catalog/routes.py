from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, logout_user

from app.catalog import catalog_bp
from app.common.security import log_audit_event, require_permission
from app.common.services import (
    agregar_producto_a_carrito,
    crear_pedido_desde_carrito,
)
from app.extensions import db
from app.models import Carrito, DetalleCarrito, Pedido, Producto, Usuario


def _guard_cliente_activo():
    usuario = Usuario.query.get(int(current_user.get_id()))
    if not usuario or not usuario.rol or usuario.rol.nombre != "Cliente":
        flash("Solo clientes pueden usar el portal de compras.", "warning")
        return redirect(url_for("catalog.catalogo"))

    if not usuario.activo:
        logout_user()
        flash(
            "Tu cuenta está inactiva. Contacta a soporte para activarla.",
            "danger",
        )
        return redirect(url_for("auth.login"))

    return None


@catalog_bp.route("/")
def home():
    destacados = (
        Producto.query.filter(
            Producto.activo.is_(True),
            (Producto.cantidad_disponible - Producto.cantidad_reservada) > 0,
        )
        .order_by(
            Producto.cantidad_disponible.desc(),
            Producto.id_producto.asc(),
        )
        .limit(6)
        .all()
    )
    return render_template("catalog/index.html", destacados=destacados)


@catalog_bp.route("/catalogo")
def catalogo():
    productos = (
        Producto.query.filter(
            Producto.activo.is_(True),
            (Producto.cantidad_disponible - Producto.cantidad_reservada) > 0,
        )
        .order_by(Producto.nombre.asc())
        .all()
    )
    return render_template(
        "catalog/catalogo_productos.html",
        productos=productos,
    )


@catalog_bp.post("/carrito/agregar")
@login_required
@require_permission("Carrito", "crear")
def carrito_agregar():
    guard = _guard_cliente_activo()
    if guard:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"success": False, "error": "No autorizado"}, 403
        return guard

    try:
        id_producto = int(request.form.get("id_producto", "0") or 0)
        cantidad = int(request.form.get("cantidad", "1") or 1)
    except ValueError:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"success": False, "error": "Datos inválidos"}, 400
        flash("Datos invalidos.", "warning")
        return redirect(url_for("catalog.catalogo"))

    try:
        agregar_producto_a_carrito(
            id_usuario=current_user.id_usuario,
            id_producto=id_producto,
            cantidad=cantidad,
        )
        log_audit_event(
            "CARRITO_AGREGAR",
            (
                f"id_usuario={current_user.id_usuario}; "
                f"id_producto={id_producto}; cantidad={cantidad}"
            ),
        )
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            carrito_db = Carrito.query.filter_by(id_usuario_cliente=current_user.id_usuario).first()
            total_items = sum(detalle.cantidad for detalle in carrito_db.detalles) if carrito_db else 0
            return {"success": True, "message": "Producto agregado.", "cart_count": total_items}

        flash("Producto agregado al carrito.", "success")
    except (ValueError, TypeError) as exc:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return {"success": False, "error": str(exc)}, 400
        flash(str(exc), "danger")
    return redirect(url_for("catalog.catalogo"))


@catalog_bp.route("/carrito", methods=["GET", "POST"])
@login_required
@require_permission("Carrito", "leer")
def carrito():
    guard = _guard_cliente_activo()
    if guard:
        return guard

    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        try:
            id_detalle = int(request.form.get("id_detalle", "0") or 0)
        except ValueError:
            id_detalle = 0

        detalle = DetalleCarrito.query.get(id_detalle) if id_detalle else None
        if not detalle:
            flash("Producto no encontrado en carrito.", "warning")
            return redirect(url_for("catalog.carrito"))

        carrito_db = Carrito.query.get(detalle.id_carrito)
        if not carrito_db or carrito_db.id_usuario_cliente != current_user.id_usuario:
            flash("No autorizado.", "danger")
            return redirect(url_for("catalog.carrito"))

        if action == "eliminar":
            db.session.delete(detalle)
            db.session.commit()
            log_audit_event(
                "CARRITO_ELIMINAR",
                (
                    f"id_usuario={current_user.id_usuario}; "
                    f"id_producto={detalle.id_producto}"
                ),
            )
            flash("Producto eliminado del carrito.", "info")
            return redirect(url_for("catalog.carrito"))

        if action == "actualizar":
            try:
                cantidad = int(
                    request.form.get("cantidad", str(detalle.cantidad))
                    or detalle.cantidad
                )
            except ValueError:
                cantidad = detalle.cantidad
            if cantidad < 1 or cantidad > 5:
                flash("La cantidad debe estar entre 1 y 5.", "warning")
                return redirect(url_for("catalog.carrito"))

            producto = detalle.producto
            if not producto or not producto.activo:
                flash("Producto no disponible.", "danger")
                return redirect(url_for("catalog.carrito"))
            stock_libre = max(
                int(producto.cantidad_disponible or 0)
                - int(producto.cantidad_reservada or 0),
                0,
            )
            if cantidad > stock_libre:
                flash(
                    "La cantidad solicitada excede el inventario disponible.",
                    "danger",
                )
                return redirect(url_for("catalog.carrito"))

            detalle.cantidad = cantidad
            db.session.commit()
            log_audit_event(
                "CARRITO_ACTUALIZAR",
                (
                    f"id_usuario={current_user.id_usuario}; "
                    f"id_producto={detalle.id_producto}; "
                    f"cantidad={cantidad}"
                ),
            )
            flash("Carrito actualizado.", "success")
            return redirect(url_for("catalog.carrito"))

    carrito_db = Carrito.query.filter_by(
        id_usuario_cliente=current_user.id_usuario
    ).first()
    detalles = carrito_db.detalles if carrito_db else []
    total = Decimal("0")
    for d in detalles:
        total += Decimal(str(d.producto.precio_venta)) * d.cantidad

    return render_template(
        "catalog/carrito_productos.html",
        carrito=carrito_db,
        detalles=detalles,
        total=total,
        today=date.today(),
    )


@catalog_bp.post("/checkout")
@login_required
@require_permission("Pedidos Clientes", "crear")
def checkout():
    guard = _guard_cliente_activo()
    if guard:
        return guard

    fecha_entrega_raw = (request.form.get("fecha_entrega") or "").strip()
    tipo_entrega = (request.form.get("tipo_entrega") or "pickup").strip().lower()

    # Regla de negocio: pedidos web se pagan en linea con tarjeta
    # y se recolectan en sucursal.
    tipo_pago_pedido = "EN_LINEA"
    tipo_pago = "TARJETA"
    referencia = (request.form.get("referencia_pago") or "").strip() or None
    if not referencia:
        referencia = (
            f"WEB-{current_user.id_usuario}-" f"{int(datetime.now().timestamp())}"
        )

    try:
        fecha_entrega = date.fromisoformat(fecha_entrega_raw)
    except ValueError:
        flash("Fecha de entrega invalida.", "warning")
        return redirect(url_for("catalog.carrito"))

    if fecha_entrega < date.today():
        flash(
            "La fecha de entrega no puede ser anterior al dia actual.",
            "warning",
        )
        return redirect(url_for("catalog.carrito"))

    if tipo_entrega != "pickup":
        flash("Los pedidos solo pueden recolectarse en sucursal.", "warning")
        return redirect(url_for("catalog.carrito"))

    try:
        pedido = crear_pedido_desde_carrito(
            id_usuario=current_user.id_usuario,
            fecha_entrega=fecha_entrega,
            tipo_pago_pedido=tipo_pago_pedido,
            tipo_pago=tipo_pago,
            referencia_pago=referencia,
            id_usuario_accion=current_user.id_usuario,
        )
        log_audit_event(
            "PEDIDO_WEB_CREADO",
            (
                f"id_usuario={current_user.id_usuario}; "
                f"id_pedido={pedido.id_pedido}; "
                f"tipo_pago={tipo_pago_pedido}"
            ),
        )
        flash(
            f"Pedido generado correctamente (ID {pedido.id_pedido}).",
            "success",
        )
        return redirect(url_for("catalog.mis_pedidos"))
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("catalog.carrito"))


@catalog_bp.route("/mis-pedidos")
@login_required
@require_permission("Pedidos Clientes", "leer")
def mis_pedidos():
    guard = _guard_cliente_activo()
    if guard:
        return guard

    pedidos = (
        Pedido.query.filter_by(id_usuario_cliente=current_user.id_usuario)
        .order_by(Pedido.id_pedido.desc())
        .all()
    )
    return render_template("catalog/mis_pedidos.html", pedidos=pedidos)
