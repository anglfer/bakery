from __future__ import annotations

import os
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from app.common.security import log_audit_event, require_permission
from app.common.services import (
    actualizar_estado_pedido,
    calcular_costo_producto,
    generar_venta_desde_pedido,
    pagar_compra,
    registrar_compra,
    registrar_venta_mostrador,
)
from app.extensions import db
from app.models import (
    Compra,
    CorteDiario,
    MateriaPrima,
    Pedido,
    Producto,
    Proveedor,
    Receta,
    SalidaEfectivo,
    SolicitudProduccion,
    Venta,
    utc_now,
    utc_today,
)
from app.sales import sales_bp


def _int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dec(value: str, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _to_mxn(value: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _sugerir_precio_venta(*, costo: Decimal, margen_pct: Decimal) -> Decimal:
    if costo <= 0 or margen_pct <= 0 or margen_pct >= 100:
        return Decimal("0")
    divisor = Decimal("1") - (margen_pct / Decimal("100"))
    if divisor <= 0:
        return Decimal("0")
    return _to_mxn(costo / divisor)


def _handle_image_upload(file):
    if not file or not file.filename:
        return None

    filename = secure_filename(file.filename)
    _, ext = os.path.splitext(filename.lower())
    allowed = {".jpg", ".jpeg", ".png"}
    if ext not in allowed:
        raise ValueError("Formato de imagen no permitido. Usa JPG, JPEG o PNG.")

    # Ensure filename is unique or handle overwrites if desired.
    # For simplicity, using original filename but secure.
    # Considerations: You might want to prepend ID or timestamp.

    upload_folder = os.path.join(current_app.root_path, "static", "img", "productos")
    os.makedirs(upload_folder, exist_ok=True)

    file_path = os.path.join(upload_folder, filename)
    file.save(file_path)

    return f"img/productos/{filename}"


@sales_bp.route("/producto-terminado", methods=["GET", "POST"])
@login_required
@require_permission("Producto Terminado", "leer")
def producto_terminado():
    if request.method == "POST":
        action = request.form.get("action", "")
        # Handle image upload
        image_file = request.files.get("imagen_archivo")
        try:
            image_path = _handle_image_upload(image_file)
        except ValueError as exc:
            flash(str(exc), "warning")
            return redirect(url_for("sales.producto_terminado"))

        if action == "crear":
            nombre = request.form.get("nombre", "").strip()
            descripcion = (
                request.form.get("descripcion", "").strip() or "Sin descripcion"
            )
            id_receta = _int(request.form.get("id_receta", "0"))
            precio = _dec(request.form.get("precio_venta", "0"))
            margen_objetivo_pct = _dec(
                request.form.get("margen_objetivo_pct", "25"),
                "25",
            )
            stock_minimo = _int(request.form.get("stock_minimo", "0"))
            unidad_venta = request.form.get("unidad_venta", "Pieza")

            # Use uploaded image if available, else use text input (emoji/url)
            imagen = image_path or request.form.get("imagen", "")

            receta = Receta.query.get(id_receta)
            if not nombre or precio <= 0 or not receta or not receta.activa:
                flash("Nombre, precio y receta activa son obligatorios.", "warning")
                return redirect(url_for("sales.producto_terminado"))
            if margen_objetivo_pct <= 0 or margen_objetivo_pct >= 100:
                flash("El margen objetivo debe estar entre 0 y 100.", "warning")
                return redirect(url_for("sales.producto_terminado"))

            db.session.add(
                Producto(
                    nombre=nombre,
                    descripcion=descripcion,
                    precio_venta=precio,
                    margen_objetivo_pct=margen_objetivo_pct,
                    unidad_venta=unidad_venta,
                    cantidad_disponible=0,
                    stock_minimo=max(stock_minimo, 0),
                    id_receta=receta.id_receta,
                    activo=True,
                    imagen=imagen,
                )
            )
            db.session.commit()
            producto_creado = Producto.query.filter_by(nombre=nombre).first()
            if producto_creado:
                log_audit_event(
                    "PRODUCTO_CREADO",
                    f"id_producto={producto_creado.id_producto}; nombre={producto_creado.nombre}",
                )
            flash("Producto terminado creado.", "success")
            return redirect(url_for("sales.producto_terminado"))

        id_producto = _int(request.form.get("id_producto", "0"))
        producto = Producto.query.get_or_404(id_producto)
        producto.nombre = request.form.get("nombre", producto.nombre)
        producto.descripcion = request.form.get("descripcion", producto.descripcion)
        producto.precio_venta = _dec(
            request.form.get("precio_venta", str(producto.precio_venta))
        )
        producto.margen_objetivo_pct = _dec(
            request.form.get(
                "margen_objetivo_pct",
                str(producto.margen_objetivo_pct or "25"),
            ),
            "25",
        )
        if producto.margen_objetivo_pct <= 0 or producto.margen_objetivo_pct >= 100:
            flash("El margen objetivo debe estar entre 0 y 100.", "warning")
            return redirect(url_for("sales.producto_terminado"))
        producto.stock_minimo = _int(
            request.form.get("stock_minimo", str(producto.stock_minimo)),
            producto.stock_minimo,
        )
        producto.unidad_venta = request.form.get("unidad_venta", producto.unidad_venta)
        id_receta = _int(request.form.get("id_receta", str(producto.id_receta or 0)))
        receta = Receta.query.get(id_receta)
        if not receta or not receta.activa:
            flash("Selecciona una receta activa para el producto.", "warning")
            return redirect(url_for("sales.producto_terminado"))
        producto.id_receta = receta.id_receta

        # Update image only if a new one is uploaded or text input changes
        if image_path:
            producto.imagen = image_path
        elif request.form.get("imagen"):
            # If no file uploaded, check if text input changed (e.g. emoji)
            producto.imagen = request.form.get("imagen", producto.imagen)

        producto.activo = request.form.get("activo") == "on"
        db.session.commit()
        log_audit_event(
            "PRODUCTO_EDITADO",
            f"id_producto={producto.id_producto}; nombre={producto.nombre}; activo={producto.activo}",
        )
        flash("Producto actualizado.", "success")
        return redirect(url_for("sales.producto_terminado"))

    productos = Producto.query.order_by(Producto.id_producto.desc()).all()
    metricas_producto: dict[int, dict] = {}
    for producto in productos:
        costo_unitario = Decimal(str(producto.costo_produccion_actual or 0))
        if costo_unitario <= 0 and producto.id_receta:
            try:
                costo_unitario = calcular_costo_producto(
                    id_producto=producto.id_producto,
                    cantidad=1,
                )
            except ValueError:
                costo_unitario = Decimal("0")

        precio_venta = Decimal(str(producto.precio_venta or 0))
        margen_objetivo = Decimal(str(producto.margen_objetivo_pct or 25))
        precio_sugerido = Decimal(str(producto.precio_sugerido or 0))
        if precio_sugerido <= 0:
            precio_sugerido = _sugerir_precio_venta(
                costo=_to_mxn(costo_unitario),
                margen_pct=margen_objetivo,
            )

        utilidad_unitaria = _to_mxn(precio_venta - _to_mxn(costo_unitario))
        porcentaje_utilidad = Decimal("0")
        if precio_venta > 0:
            porcentaje_utilidad = _to_mxn((utilidad_unitaria / precio_venta) * 100)

        metricas_producto[producto.id_producto] = {
            "costo_unitario": _to_mxn(costo_unitario),
            "precio_sugerido": precio_sugerido,
            "margen_objetivo_pct": _to_mxn(margen_objetivo),
            "utilidad_unitaria": utilidad_unitaria,
            "porcentaje_utilidad": porcentaje_utilidad,
        }

    recetas = Receta.query.filter_by(activa=True).order_by(Receta.nombre.asc()).all()
    return render_template(
        "sales/producto_terminado.html",
        productos=productos,
        metricas_producto=metricas_producto,
        recetas=recetas,
        utc_now=utc_now,
    )


@sales_bp.route("/solicitudes", methods=["GET", "POST"])
@login_required
@require_permission("Solicitudes", "leer")
def solicitudes():
    role_name = current_user.rol.nombre if current_user.rol else ""

    if request.method == "POST":
        if role_name != "Ventas":
            flash(
                "Solo el area de ventas puede registrar solicitudes de produccion.",
                "danger",
            )
            return redirect(url_for("sales.solicitudes"))

        id_producto = _int(request.form.get("id_producto", "0"))
        cantidad = _int(request.form.get("cantidad", "0"))
        observaciones = request.form.get("observaciones", "").strip() or None
        producto = Producto.query.get(id_producto)
        if not producto or not producto.activo or cantidad <= 0:
            flash("Producto o cantidad invalida para solicitud.", "warning")
            return redirect(url_for("sales.solicitudes"))

        receta = Receta.query.get(producto.id_receta) if producto.id_receta else None
        if not receta or not receta.activa:
            flash(
                "El producto seleccionado no tiene una receta activa para producir.",
                "warning",
            )
            return redirect(url_for("sales.solicitudes"))

        db.session.add(
            SolicitudProduccion(
                id_producto=id_producto,
                cantidad=cantidad,
                estado="PENDIENTE",
                id_usuario_solicita=current_user.id_usuario,
                observaciones=observaciones,
            )
        )
        db.session.commit()
        log_audit_event(
            "SOLICITUD_PRODUCCION_CREADA",
            f"id_usuario={current_user.id_usuario}; id_producto={id_producto}; cantidad={cantidad}",
        )
        flash("Solicitud de produccion registrada.", "success")
        return redirect(url_for("sales.solicitudes"))

    productos = (
        Producto.query.filter_by(activo=True).order_by(Producto.nombre.asc()).all()
    )
    solicitudes_data = SolicitudProduccion.query.order_by(
        SolicitudProduccion.id_solicitud.desc()
    ).all()
    return render_template(
        "sales/solicitudes.html",
        productos=productos,
        solicitudes=solicitudes_data,
    )


@sales_bp.route("/pedidos-clientes", methods=["GET", "POST"])
@login_required
@require_permission("Pedidos Clientes", "leer")
def pedidos_clientes():
    if request.method == "POST":
        action = request.form.get("action", "actualizar").strip().lower()
        id_pedido = _int(request.form.get("id_pedido", "0"))
        if action == "entregar":
            requiere_ticket = request.form.get("requiere_ticket") == "on"
            try:
                venta = generar_venta_desde_pedido(
                    id_pedido=id_pedido,
                    requiere_ticket=requiere_ticket,
                )
                log_audit_event(
                    "PEDIDO_ENTREGADO_VENTA_GENERADA",
                    f"id_pedido={id_pedido}; id_venta={venta.id_venta}; requiere_ticket={requiere_ticket}",
                )
                flash(
                    "Pedido entregado. Venta generada y stock actualizado.", "success"
                )
            except ValueError as exc:
                flash(str(exc), "danger")
            return redirect(url_for("sales.pedidos_clientes"))

        nuevo_estado = request.form.get("estado", "PENDIENTE").strip().upper()
        referencia = request.form.get("referencia_pago", "").strip() or None
        try:
            actualizar_estado_pedido(
                id_pedido=id_pedido,
                nuevo_estado=nuevo_estado,
                referencia_pago=referencia,
            )
            log_audit_event(
                "PEDIDO_ESTADO_ACTUALIZADO",
                f"id_pedido={id_pedido}; nuevo_estado={nuevo_estado}",
            )
            flash("Pedido actualizado.", "success")
        except ValueError as exc:
            flash(str(exc), "warning")
        return redirect(url_for("sales.pedidos_clientes"))

    pedidos = Pedido.query.order_by(Pedido.id_pedido.desc()).all()
    return render_template("sales/pedidos_clientes.html", pedidos=pedidos)


@sales_bp.route("/ventas", methods=["GET", "POST"])
@login_required
@require_permission("Ventas", "leer")
def ventas():
    if request.method == "POST":
        id_producto = _int(request.form.get("id_producto", "0"))
        cantidad = _int(request.form.get("cantidad", "1"), 1)
        tipo_pago = (request.form.get("tipo_pago") or "EFECTIVO").strip().upper()
        requiere_ticket = request.form.get("requiere_ticket") == "on"
        try:
            venta = registrar_venta_mostrador(
                id_producto=id_producto,
                cantidad=cantidad,
                tipo_pago=tipo_pago,
                requiere_ticket=requiere_ticket,
                id_usuario_emite=current_user.id_usuario,
            )
            detalle = venta.detalles[0] if venta.detalles else None
            log_audit_event(
                "VENTA_REGISTRADA",
                f"id_venta={venta.id_venta}; id_producto={detalle.id_producto if detalle else id_producto}; cantidad={detalle.cantidad if detalle else cantidad}; tipo_pago={tipo_pago}",
            )
            flash("Venta registrada y stock actualizado.", "success")
        except ValueError as exc:
            flash(str(exc), "warning")
        return redirect(url_for("sales.ventas"))

    data = Venta.query.order_by(Venta.id_venta.desc()).all()
    productos = (
        Producto.query.filter(
            Producto.activo.is_(True),
            (Producto.cantidad_disponible - Producto.cantidad_reservada) > 0,
        )
        .order_by(Producto.nombre.asc())
        .all()
    )
    return render_template("sales/ventas.html", ventas=data, productos=productos)


@sales_bp.route("/compras-mp", methods=["GET", "POST"])
@login_required
@require_permission("Compras MP", "leer")
def compras_mp():
    if request.method == "POST":
        id_proveedor = _int(request.form.get("id_proveedor", "0"))
        materias_rows = request.form.getlist("id_materia[]")
        cantidades_rows = request.form.getlist("cantidad_comprada[]")
        precios_rows = request.form.getlist("precio_unitario[]")

        detalles_compra: list[dict] = []
        if (
            materias_rows
            and cantidades_rows
            and precios_rows
            and len(materias_rows) == len(cantidades_rows) == len(precios_rows)
        ):
            for materia_raw, cantidad_raw, precio_raw in zip(
                materias_rows,
                cantidades_rows,
                precios_rows,
            ):
                id_materia = _int(materia_raw, 0)
                cantidad_comprada = _dec(cantidad_raw, "0")
                precio_unitario = _dec(precio_raw, "0")
                if id_materia <= 0:
                    continue
                if cantidad_comprada <= 0:
                    flash(
                        "La cantidad comprada debe ser mayor a cero en todos los renglones.",
                        "danger",
                    )
                    return redirect(url_for("sales.compras_mp"))
                if precio_unitario < 0:
                    flash(
                        "El precio unitario no puede ser negativo en ningun renglon.",
                        "danger",
                    )
                    return redirect(url_for("sales.compras_mp"))
                detalles_compra.append(
                    {
                        "id_materia_prima": id_materia,
                        "cantidad_comprada": cantidad_comprada,
                        "precio_unitario": precio_unitario,
                    }
                )

        if not detalles_compra:
            id_materia = _int(request.form.get("id_materia", "0"))
            cantidad_comprada = _dec(request.form.get("cantidad_comprada", "0"))
            precio_unitario = _dec(request.form.get("precio_unitario", "0"))
            if cantidad_comprada <= 0:
                flash("La cantidad comprada debe ser mayor a cero.", "danger")
                return redirect(url_for("sales.compras_mp"))
            if precio_unitario < 0:
                flash("El precio unitario no puede ser negativo.", "danger")
                return redirect(url_for("sales.compras_mp"))
            detalles_compra = [
                {
                    "id_materia_prima": id_materia,
                    "cantidad_comprada": cantidad_comprada,
                    "precio_unitario": precio_unitario,
                }
            ]

        proveedor = Proveedor.query.get(id_proveedor)
        if not proveedor or not proveedor.activo:
            flash(
                "Proveedor invalido o inactivo. "
                "Solo puedes comprar a proveedores activos.",
                "danger",
            )
            return redirect(url_for("sales.compras_mp"))

        for detalle_compra in detalles_compra:
            materia = MateriaPrima.query.get(detalle_compra["id_materia_prima"])
            if not materia or not materia.activa:
                flash("Materia prima invalida o inactiva.", "danger")
                return redirect(url_for("sales.compras_mp"))
            if Decimal(str(materia.factor_conversion)) <= 0:
                flash(
                    "La materia prima tiene un factor de conversion invalido.",
                    "danger",
                )
                return redirect(url_for("sales.compras_mp"))

        compra = Compra(
            id_proveedor=id_proveedor,
            id_usuario_comprador=current_user.id_usuario,
            estado_pago="PENDIENTE",
        )
        try:
            registrar_compra(compra, detalles_compra)
            log_audit_event(
                "COMPRA_MP_REGISTRADA",
                f"id_compra={compra.id_compra}; id_proveedor={id_proveedor}; detalles={len(detalles_compra)}",
            )
            flash("Compra registrada.", "success")
        except ValueError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("sales.compras_mp"))

    compras = Compra.query.order_by(Compra.id_compra.desc()).all()
    proveedores = (
        Proveedor.query.filter_by(activo=True)
        .order_by(Proveedor.nombre_empresa.asc())
        .all()
    )
    materias = (
        MateriaPrima.query.filter_by(activa=True)
        .order_by(MateriaPrima.nombre.asc())
        .all()
    )
    return render_template(
        "sales/compras_mp.html",
        compras=compras,
        proveedores=proveedores,
        materias=materias,
    )


@sales_bp.post("/compras-mp/<int:id_compra>/pagar")
@login_required
@require_permission("Compras MP", "editar")
def pagar_compra_route(id_compra: int):
    try:
        pagar_compra(id_compra=id_compra, id_usuario=current_user.id_usuario)
        log_audit_event("COMPRA_MP_PAGADA", f"id_compra={id_compra}")
        flash("Compra marcada como pagada.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("sales.compras_mp"))


@sales_bp.route("/salidas", methods=["GET", "POST"])
@login_required
@require_permission("Costos y Utilidad", "leer")
def salidas():
    if request.method == "POST":
        concepto = request.form.get("concepto", "").strip()
        monto = _dec(request.form.get("monto", "0"))
        tipo = request.form.get("tipo", "OTRO").strip().upper() or "OTRO"
        if not concepto or monto <= 0:
            flash("Concepto y monto valido son obligatorios.", "warning")
            return redirect(url_for("sales.salidas"))

        db.session.add(
            SalidaEfectivo(
                concepto=concepto,
                monto=monto,
                tipo=tipo,
                id_usuario=current_user.id_usuario,
                referencia=request.form.get("referencia", "").strip() or None,
            )
        )
        db.session.commit()
        log_audit_event(
            "SALIDA_EFECTIVO_REGISTRADA",
            f"concepto={concepto}; monto={monto}; tipo={tipo}",
        )
        flash("Salida registrada.", "success")
        return redirect(url_for("sales.salidas"))

    data = SalidaEfectivo.query.order_by(SalidaEfectivo.id_salida.desc()).all()
    return render_template("sales/salidas_efectivo.html", salidas=data)


@sales_bp.route("/cortes", methods=["GET", "POST"])
@login_required
@require_permission("Costos y Utilidad", "leer")
def cortes():
    if request.method == "POST":
        fecha = utc_today()
        ventas_hoy = Venta.query.filter(db.func.date(Venta.fecha) == fecha).all()
        total_ventas = sum(Decimal(str(v.total)) for v in ventas_hoy)
        numero_ventas = len(ventas_hoy)
        costo_produccion = Decimal("0")
        for v in ventas_hoy:
            for d in v.detalles:
                if d.costo_unitario_produccion is not None:
                    costo_produccion += Decimal(
                        str(d.costo_unitario_produccion)
                    ) * Decimal(str(d.cantidad))
                    continue

                try:
                    costo_produccion += calcular_costo_producto(
                        id_producto=d.id_producto,
                        cantidad=d.cantidad,
                    )
                except ValueError:
                    # Legacy sin snapshot de costo, mantener continuidad del corte.
                    pass
        salidas_hoy = SalidaEfectivo.query.filter(
            db.func.date(SalidaEfectivo.fecha_creacion) == fecha
        ).all()
        total_salidas = sum(Decimal(str(s.monto)) for s in salidas_hoy)
        utilidad = total_ventas - total_salidas - costo_produccion

        corte = CorteDiario(
            fecha=fecha,
            total_ventas=total_ventas,
            numero_ventas=numero_ventas,
            utilidad_diaria=utilidad,
            salida_efectivo_proveedores=total_salidas,
            id_usuario=current_user.id_usuario,
        )
        db.session.add(corte)
        db.session.commit()
        log_audit_event(
            "CORTE_DIARIO_GENERADO",
            f"fecha={fecha}; total_ventas={total_ventas}; total_salidas={total_salidas}; utilidad={utilidad}",
        )
        flash("Corte diario generado.", "success")
        return redirect(url_for("sales.cortes"))

    data = CorteDiario.query.order_by(CorteDiario.id_corte.desc()).all()
    productos = (
        Producto.query.filter_by(activo=True).order_by(Producto.nombre.asc()).all()
    )
    costos = []
    for p in productos:
        try:
            costo_unit = Decimal(str(p.costo_produccion_actual or 0))
            if costo_unit <= 0:
                costo_unit = calcular_costo_producto(
                    id_producto=p.id_producto, cantidad=1
                )

            utilidad_unit = Decimal(str(p.precio_venta)) - costo_unit
            porcentaje = (
                (utilidad_unit / Decimal(str(p.precio_venta))) * Decimal("100")
                if Decimal(str(p.precio_venta)) > 0
                else Decimal("0")
            )

            precio_sugerido = Decimal(str(p.precio_sugerido or 0))
            if precio_sugerido <= 0:
                precio_sugerido = _sugerir_precio_venta(
                    costo=_to_mxn(costo_unit),
                    margen_pct=Decimal(str(p.margen_objetivo_pct or 25)),
                )

            costos.append(
                {
                    "producto": p,
                    "costo_unitario": _to_mxn(costo_unit),
                    "utilidad_unitaria": _to_mxn(utilidad_unit),
                    "porcentaje_utilidad": _to_mxn(porcentaje),
                    "precio_sugerido": _to_mxn(precio_sugerido),
                    "margen_objetivo_pct": _to_mxn(
                        Decimal(str(p.margen_objetivo_pct or 25))
                    ),
                }
            )
        except ValueError:
            continue
    return render_template("sales/costos_utilidad.html", cortes=data, costos=costos)
