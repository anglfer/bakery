from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import (
    current_app,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, logout_user

from app.catalog import catalog_bp
from app.common.security import log_audit_event, require_permission
from app.common.services import (
    agregar_producto_a_carrito,
    crear_pedido_desde_carrito,
)
from app.extensions import db
from app.models import (
    Carrito,
    DetalleCarrito,
    Pedido,
    Producto,
    TicketVenta,
    Usuario,
    Venta,
)


def _validar_pago_en_linea_tarjeta(
    *,
    numero_tarjeta: str,
    expiracion: str,
    cvv: str,
) -> None:
    numero_limpio = "".join(ch for ch in (numero_tarjeta or "") if ch.isdigit())
    if len(numero_limpio) != 16:
        raise ValueError("Número de tarjeta inválido. Debe contener 16 dígitos.")

    exp_limpia = (expiracion or "").strip()
    if len(exp_limpia) != 5 or "/" not in exp_limpia:
        raise ValueError("Fecha de expiración inválida. Usa formato MM/AA.")

    mes_texto, anio_texto = exp_limpia.split("/", maxsplit=1)
    if not mes_texto.isdigit() or not anio_texto.isdigit():
        raise ValueError("Fecha de expiración inválida. Usa formato MM/AA.")

    mes = int(mes_texto)
    anio = int(anio_texto)
    if mes < 1 or mes > 12:
        raise ValueError("Mes de expiración inválido.")

    referencia = date.today()
    anio_actual_2d = referencia.year % 100
    if anio < anio_actual_2d or (anio == anio_actual_2d and mes < referencia.month):
        raise ValueError("La tarjeta ingresada está vencida.")

    cvv_limpio = (cvv or "").strip()
    if not cvv_limpio.isdigit() or len(cvv_limpio) not in {3, 4}:
        raise ValueError("CVV inválido.")


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


def _build_catalog_story(producto: Producto) -> dict[str, object]:
    receta = producto.receta_base
    ingredientes: list[str] = []
    if receta and receta.detalles:
        for detalle in receta.detalles:
            if not detalle.materia_prima or not detalle.materia_prima.nombre:
                continue

            nombre_materia = detalle.materia_prima.nombre.strip()
            if not nombre_materia or nombre_materia in ingredientes:
                continue

            ingredientes.append(nombre_materia)
            if len(ingredientes) >= 8:
                break

    resumen = (
        receta.descripcion
        if receta and receta.descripcion
        else producto.descripcion or ""
    ).strip()
    if not resumen:
        resumen = "Pastel artesanal preparado " "con estándares de calidad SoftBakery."

    pasos: list[str] = []
    descripcion_producto = (producto.descripcion or "").strip()
    if descripcion_producto:
        pasos.append("Definición del estilo del pastel: " f"{descripcion_producto}")

    descripcion_receta = (
        receta.descripcion if receta and receta.descripcion else ""
    ).strip()
    if descripcion_receta:
        pasos.append(f"Perfil de preparación: {descripcion_receta}")

    pasos.extend(
        [
            (
                "Preparación de mezcla y horneado "
                "con control de tiempos y temperatura."
            ),
            (
                "Montaje final con rellenos, cubierta y decoración "
                "según el diseño del producto."
            ),
            (
                "Revisión de calidad, reserva en inventario "
                "y programación para entrega."
            ),
        ]
    )

    pasos_unicos: list[str] = []
    for paso in pasos:
        if paso in pasos_unicos:
            continue
        pasos_unicos.append(paso)

    return {
        "resumen": resumen,
        "ingredientes": ingredientes,
        "pasos": pasos_unicos[:4],
        "tiene_receta": bool(receta),
    }


def _obtener_o_crear_ticket_cliente(venta: Venta) -> TicketVenta:
    ticket = TicketVenta.query.filter_by(id_venta=venta.id_venta).first()
    if ticket:
        return ticket

    nombre_negocio = (
        str(current_app.config.get("BUSINESS_NAME", "SoftBakery")).strip()
        or "SoftBakery"
    )
    ticket = TicketVenta()
    ticket.id_venta = venta.id_venta
    ticket.folio = f"SB-{venta.id_venta:06d}"
    ticket.nombre_negocio = nombre_negocio
    db.session.add(ticket)
    if not venta.requiere_ticket:
        venta.requiere_ticket = True
    db.session.commit()
    return ticket


def _nombre_cliente_actual() -> str:
    cliente = current_user.username
    if current_user.persona:
        cliente = (
            f"{current_user.persona.nombre} {current_user.persona.apellidos}"
        ).strip()
    return cliente


def _render_ticket_pedido_pagado(pedido: Pedido, *, download_mode: bool):
    folio_pedido = f"PED-TKT-{pedido.id_pedido:06d}"
    detalles_pedido = list(getattr(pedido, "detalles", []) or [])
    total_detalle = sum(Decimal(str(d.subtotal or 0)) for d in detalles_pedido)
    if total_detalle <= 0:
        total_detalle = Decimal(str(pedido.total or 0))

    html = render_template(
        "sales/ticket_venta.html",
        venta=None,
        ticket=None,
        pedido=pedido,
        ticket_folio=folio_pedido,
        ticket_fecha=pedido.fecha_pedido,
        nombre_negocio=(
            str(current_app.config.get("BUSINESS_NAME", "SoftBakery")).strip()
            or "SoftBakery"
        ),
        cliente=_nombre_cliente_actual(),
        medio_pago=(pedido.pago.tipo_pago if pedido.pago else pedido.tipo_pago),
        total_detalle=total_detalle,
        download_mode=download_mode,
        download_url=url_for(
            "catalog.ver_ticket_pedido_cliente",
            id_pedido=pedido.id_pedido,
            download=1,
        ),
        back_url=url_for("catalog.mis_pedidos"),
        back_label="Volver a mis pedidos",
    )

    if download_mode:
        response = make_response(html)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="ticket-{folio_pedido}.html"'
        )
        return response

    return html


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
    catalogo_story = {
        producto.id_producto: _build_catalog_story(producto) for producto in productos
    }
    return render_template(
        "catalog/catalogo_productos.html",
        productos=productos,
        catalogo_story=catalogo_story,
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
            carrito_db = Carrito.query.filter_by(
                id_usuario_cliente=current_user.id_usuario
            ).first()
            total_items = (
                sum(detalle.cantidad for detalle in carrito_db.detalles)
                if carrito_db
                else 0
            )
            return {
                "success": True,
                "message": "Producto agregado.",
                "cart_count": total_items,
            }

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
        min_delivery_date=(date.today() + timedelta(days=3)),
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
    acepta_privacidad = (request.form.get("acepta_privacidad") or "").strip()

    # Regla de negocio: pedidos web se pagan en linea con tarjeta
    # y se recolectan en sucursal.
    tipo_pago_pedido = "EN_LINEA"
    tipo_pago = "TARJETA"
    referencia = (request.form.get("referencia_pago") or "").strip() or None
    numero_tarjeta = (request.form.get("numero_tarjeta") or "").strip()
    expiracion = (request.form.get("expiracion") or "").strip()
    cvv = (request.form.get("cvv") or "").strip()
    if not referencia:
        referencia = (
            f"WEB-{current_user.id_usuario}-" f"{int(datetime.now().timestamp())}"
        )

    try:
        fecha_entrega = date.fromisoformat(fecha_entrega_raw)
    except ValueError:
        flash("Fecha de entrega invalida.", "warning")
        return redirect(url_for("catalog.carrito"))

    fecha_minima = date.today() + timedelta(days=3)
    if fecha_entrega < fecha_minima:
        flash(
            "La fecha de entrega debe ser al menos 3 días posterior a hoy.",
            "warning",
        )
        return redirect(url_for("catalog.carrito"))

    if tipo_entrega != "pickup":
        flash("Los pedidos solo pueden recolectarse en sucursal.", "warning")
        return redirect(url_for("catalog.carrito"))

    if acepta_privacidad != "1":
        flash(
            "Debes aceptar las políticas de privacidad para continuar.",
            "warning",
        )
        return redirect(url_for("catalog.carrito"))

    try:
        _validar_pago_en_linea_tarjeta(
            numero_tarjeta=numero_tarjeta,
            expiracion=expiracion,
            cvv=cvv,
        )
    except ValueError as exc:
        flash(f"Pago rechazado: {exc}", "danger")
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

    ventas_cliente = (
        Venta.query.filter(
            Venta.id_usuario_cliente == current_user.id_usuario,
            Venta.id_pedido.isnot(None),
        )
        .order_by(Venta.id_venta.desc())
        .all()
    )

    ids_venta = [venta.id_venta for venta in ventas_cliente]
    tickets = []
    if ids_venta:
        tickets = TicketVenta.query.filter(TicketVenta.id_venta.in_(ids_venta)).all()
    ticket_por_venta = {ticket.id_venta: ticket for ticket in tickets}

    venta_por_pedido = {
        venta.id_pedido: venta for venta in ventas_cliente if venta.id_pedido
    }
    tickets_por_pedido: dict[int, dict] = {}
    for venta in ventas_cliente:
        if not venta.id_pedido or venta.id_pedido in tickets_por_pedido:
            continue

        ticket = ticket_por_venta.get(venta.id_venta)
        tickets_por_pedido[venta.id_pedido] = {
            "id_venta": venta.id_venta,
            "folio": ticket.folio if ticket else None,
            "tipo": "VENTA",
        }

    for pedido in pedidos:
        if pedido.id_pedido in tickets_por_pedido:
            continue

        estado_pago = (pedido.estado_pago or "").upper()
        if estado_pago != "PAGADO":
            continue

        if pedido.id_pedido not in venta_por_pedido:
            tickets_por_pedido[pedido.id_pedido] = {
                "id_venta": None,
                "folio": f"PED-TKT-{pedido.id_pedido:06d}",
                "tipo": "PEDIDO",
                "provisional": True,
            }

    return render_template(
        "catalog/mis_pedidos.html",
        pedidos=pedidos,
        tickets_por_pedido=tickets_por_pedido,
    )


@catalog_bp.get("/mis-pedidos/<int:id_pedido>/ticket")
@login_required
@require_permission("Pedidos Clientes", "leer")
def ver_ticket_pedido_cliente(id_pedido: int):
    guard = _guard_cliente_activo()
    if guard:
        return guard

    pedido = Pedido.query.filter_by(
        id_pedido=id_pedido,
        id_usuario_cliente=current_user.id_usuario,
    ).first()
    if not pedido:
        flash("Pedido no encontrado.", "warning")
        return redirect(url_for("catalog.mis_pedidos"))

    venta = (
        Venta.query.filter(
            Venta.id_pedido == id_pedido,
            Venta.id_usuario_cliente == current_user.id_usuario,
        )
        .order_by(Venta.id_venta.desc())
        .first()
    )

    download_mode = request.args.get("download") == "1"
    if not venta and (pedido.estado_pago or "").upper() == "PAGADO":
        return _render_ticket_pedido_pagado(
            pedido,
            download_mode=download_mode,
        )

    if not venta:
        flash(
            "Aún no hay ticket disponible para ese pedido.",
            "warning",
        )
        return redirect(url_for("catalog.mis_pedidos"))

    ticket = _obtener_o_crear_ticket_cliente(venta)
    cliente = _nombre_cliente_actual()

    total_detalle = sum(Decimal(str(d.subtotal or 0)) for d in venta.detalles)
    if total_detalle <= 0:
        total_detalle = Decimal(str(venta.total or 0))

    html = render_template(
        "sales/ticket_venta.html",
        venta=venta,
        ticket=ticket,
        pedido=pedido,
        cliente=cliente,
        total_detalle=total_detalle,
        download_mode=download_mode,
        download_url=url_for(
            "catalog.ver_ticket_pedido_cliente",
            id_pedido=id_pedido,
            download=1,
        ),
        back_url=url_for("catalog.mis_pedidos"),
        back_label="Volver a mis pedidos",
    )

    if download_mode:
        response = make_response(html)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="ticket-{ticket.folio}.html"'
        )
        return response

    return html
