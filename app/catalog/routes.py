from __future__ import annotations

from datetime import date
from decimal import Decimal

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.catalog import catalog_bp
from app.common.security import require_permission
from app.common.services import agregar_producto_a_carrito, crear_pedido_desde_carrito
from app.extensions import db
from app.models import Carrito, DetalleCarrito, Pedido, Producto


@catalog_bp.route("/")
def home():
    destacados = (
        Producto.query.filter(Producto.activo == True, Producto.cantidad_disponible > 0)
        .order_by(Producto.cantidad_disponible.desc(), Producto.id_producto.asc())
        .limit(6)
        .all()
    )
    return render_template("catalog/index.html", destacados=destacados)


@catalog_bp.route("/catalogo")
def catalogo():
    productos = (
        Producto.query.filter(Producto.activo == True, Producto.cantidad_disponible > 0)
        .order_by(Producto.nombre.asc())
        .all()
    )
    return render_template("catalog/catalogo_productos.html", productos=productos)


@catalog_bp.post("/carrito/agregar")
@login_required
@require_permission("Carrito", "crear")
def carrito_agregar():
    if not current_user.rol or current_user.rol.nombre != "Cliente":
        flash("Solo clientes pueden usar el carrito web.", "warning")
        return redirect(url_for("catalog.catalogo"))

    try:
        id_producto = int(request.form.get("id_producto", "0") or 0)
        cantidad = int(request.form.get("cantidad", "1") or 1)
    except ValueError:
        flash("Datos invalidos.", "warning")
        return redirect(url_for("catalog.catalogo"))

    try:
        agregar_producto_a_carrito(
            id_usuario=current_user.id_usuario,
            id_producto=id_producto,
            cantidad=cantidad,
        )
        flash("Producto agregado al carrito.", "success")
    except (ValueError, TypeError) as exc:
        flash(str(exc), "danger")
    return redirect(url_for("catalog.catalogo"))


@catalog_bp.route("/carrito", methods=["GET", "POST"])
@login_required
@require_permission("Carrito", "leer")
def carrito():
    if not current_user.rol or current_user.rol.nombre != "Cliente":
        flash("Solo clientes pueden usar el carrito web.", "warning")
        return redirect(url_for("catalog.catalogo"))

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
            flash("Producto eliminado del carrito.", "info")
            return redirect(url_for("catalog.carrito"))

        if action == "actualizar":
            try:
                cantidad = int(request.form.get("cantidad", str(detalle.cantidad)) or detalle.cantidad)
            except ValueError:
                cantidad = detalle.cantidad
            if cantidad < 1 or cantidad > 5:
                flash("La cantidad debe estar entre 1 y 5.", "warning")
                return redirect(url_for("catalog.carrito"))
            detalle.cantidad = cantidad
            db.session.commit()
            flash("Carrito actualizado.", "success")
            return redirect(url_for("catalog.carrito"))

    carrito_db = Carrito.query.filter_by(id_usuario_cliente=current_user.id_usuario).first()
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
    if not current_user.rol or current_user.rol.nombre != "Cliente":
        flash("Solo clientes pueden generar pedidos.", "warning")
        return redirect(url_for("catalog.catalogo"))

    fecha_entrega_raw = (request.form.get("fecha_entrega") or "").strip()
    tipo_pago_pedido = (request.form.get("tipo_pago_pedido") or "CONTRA_ENTREGA").strip().upper()
    tipo_pago = (request.form.get("tipo_pago") or "EFECTIVO").strip().upper()
    referencia = (request.form.get("referencia_pago") or "").strip() or None

    try:
        fecha_entrega = date.fromisoformat(fecha_entrega_raw)
    except ValueError:
        flash("Fecha de entrega invalida.", "warning")
        return redirect(url_for("catalog.carrito"))

    try:
        pedido = crear_pedido_desde_carrito(
            id_usuario=current_user.id_usuario,
            fecha_entrega=fecha_entrega,
            tipo_pago_pedido=tipo_pago_pedido,
            tipo_pago=tipo_pago,
            referencia_pago=referencia,
        )
        flash(f"Pedido generado correctamente (ID {pedido.id_pedido}).", "success")
        return redirect(url_for("catalog.mis_pedidos"))
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("catalog.carrito"))


@catalog_bp.route("/mis-pedidos")
@login_required
@require_permission("Pedidos Clientes", "leer")
def mis_pedidos():
    if not current_user.rol or current_user.rol.nombre != "Cliente":
        flash("Solo clientes pueden consultar pedidos web.", "warning")
        return redirect(url_for("catalog.catalogo"))

    pedidos = (
        Pedido.query.filter_by(id_usuario_cliente=current_user.id_usuario)
        .order_by(Pedido.id_pedido.desc())
        .all()
    )
    return render_template("catalog/mis_pedidos.html", pedidos=pedidos)
