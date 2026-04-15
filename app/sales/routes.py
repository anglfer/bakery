from __future__ import annotations

import json
import os
from datetime import datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from flask import (
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload
from werkzeug.utils import secure_filename

from app.common.security import log_audit_event, require_permission
from app.common.services import (
    actualizar_estado_pedido,
    calcular_costo_producto,
    calcular_costo_unitario_producto,
    cancelar_venta_mostrador,
    generar_venta_desde_pedido,
    pagar_compra,
    recalcular_costo_y_precio_sugerido_producto,
    registrar_compra,
    registrar_venta_mostrador_detallada,
)
from app.extensions import db
from app.models import (
    Compra,
    CorteDiario,
    DetalleCompra,
    DetallePedido,
    DetalleReceta,
    DetalleVenta,
    MateriaPrima,
    Modulo,
    MovimientoInventarioProducto,
    Pedido,
    PedidoEstadoHistorial,
    Permiso,
    Persona,
    Producto,
    Proveedor,
    Receta,
    SalidaEfectivo,
    SolicitudProduccion,
    TicketVenta,
    Usuario,
    Venta,
    utc_now,
    utc_today,
)
from app.sales import sales_bp
from app.sales.forms import (
    ProductoTerminadoForm,
    SalidaEfectivoForm,
    SolicitudVentasCrearForm,
    SolicitudVentasEditarForm,
)


def _can_manage_producto_terminado() -> bool:
    if not current_user.is_authenticated:
        return False

    role_name = current_user.rol.nombre if current_user.rol else ""
    if role_name in {"Administrador", "Ventas"}:
        return True

    modulo = Modulo.query.filter_by(nombre="Producto Terminado").first()
    if not modulo:
        return False

    permiso = Permiso.query.filter_by(
        id_rol=current_user.id_rol,
        id_modulo=modulo.id_modulo,
    ).first()
    if not permiso:
        return False

    return bool(permiso.escritura or permiso.actualizacion or permiso.eliminacion)


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


def _parse_fecha_compra(value: str | None) -> datetime:
    if not value:
        return datetime.combine(utc_today(), time.min)
    try:
        fecha = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return datetime.combine(utc_today(), time.min)
    return datetime.combine(fecha.date(), time.min)


def _solicitudes_pedido_activas(pedido: Pedido) -> list[SolicitudProduccion]:
    return [
        solicitud
        for solicitud in (pedido.solicitudes_produccion or [])
        if solicitud.estado in {"PENDIENTE", "APROBADA"}
    ]


def _produccion_pedido_lista(pedido: Pedido) -> bool:
    solicitudes = pedido.solicitudes_produccion or []
    if not solicitudes:
        return True

    for solicitud in solicitudes:
        if solicitud.estado != "APROBADA":
            return False

        ordenes_vigentes = [
            orden for orden in solicitud.ordenes if orden.estado != "CANCELADO"
        ]
        if not ordenes_vigentes:
            return False
        if not any(orden.estado == "FINALIZADO" for orden in ordenes_vigentes):
            return False

    return True


def _to_mxn(value: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def _precio_sugerido_desde_costo(costo: Decimal, margen_pct: Decimal) -> Decimal:
    if costo <= 0 or margen_pct <= 0 or margen_pct >= 100:
        return Decimal("0")
    divisor = Decimal("1") - (margen_pct / Decimal("100"))
    if divisor <= 0:
        return Decimal("0")
    return _to_mxn(costo / divisor)


def _costo_receta_producto(receta: Receta) -> Decimal:
    if not receta or not receta.activa:
        return Decimal("0")

    total = Decimal("0")
    for detalle in receta.detalles:
        materia = detalle.materia_prima
        if not materia or not materia.activa:
            continue
        base = Decimal(str(detalle.cantidad_base or 0))
        merma = Decimal(str(materia.porcentaje_merma or 0)) / Decimal("100")
        real = base * (Decimal("1") + merma)
        total += real * Decimal(str(materia.costo_unitario or 0))

    rendimiento = Decimal(str(receta.rendimiento_base or 0))
    if rendimiento <= 0:
        return Decimal("0")
    return _to_mxn(total / rendimiento)


def _nombre_unidad(unidad) -> str:
    if not unidad:
        return ""
    return unidad.abreviatura


def _detalle_compra_payload(detalle: DetalleCompra) -> dict:
    materia = detalle.materia_prima
    unidad_base = materia.unidad_base if materia else None
    unidad_compra = detalle.unidad_compra
    if not unidad_compra and materia:
        unidad_compra = materia.unidad_compra

    nombre_materia = "Materia prima"
    if materia:
        nombre_materia = materia.nombre

    return {
        "id_detalle": detalle.id_detalle,
        "materia_prima": nombre_materia,
        "id_materia_prima": detalle.id_materia_prima,
        "unidad_compra": _nombre_unidad(unidad_compra),
        "id_unidad_compra": detalle.id_unidad_compra,
        "cantidad_comprada": float(detalle.cantidad_comprada),
        "cantidad_base": float(detalle.cantidad_base),
        "unidad_base": _nombre_unidad(unidad_base),
        "precio_unitario": float(detalle.precio_unitario),
        "subtotal": float(detalle.subtotal),
    }


def _compra_payload(compra: Compra) -> dict:
    return {
        "id_compra": compra.id_compra,
        "folio": compra.folio_formateado,
        "proveedor": (compra.proveedor.nombre_empresa if compra.proveedor else ""),
        "fecha": compra.fecha.isoformat() if compra.fecha else None,
        "estado_pago": compra.estado_pago,
        "total": float(compra.total or 0),
        "registrado_por": (compra.comprador.username if compra.comprador else ""),
        "detalles": [_detalle_compra_payload(detalle) for detalle in compra.detalles],
    }


def _sugerir_precio_venta(*, costo: Decimal, margen_pct: Decimal) -> Decimal:
    if costo <= 0 or margen_pct <= 0 or margen_pct >= 100:
        return Decimal("0")
    divisor = Decimal("1") - (margen_pct / Decimal("100"))
    if divisor <= 0:
        return Decimal("0")
    return _to_mxn(costo / divisor)


def _clasificar_margen(porcentaje_utilidad: Decimal) -> dict:
    if porcentaje_utilidad >= Decimal("35"):
        return {
            "clave": "alto",
            "etiqueta": "Alta rentabilidad",
            "clase": "success",
        }
    if porcentaje_utilidad >= Decimal("20"):
        return {
            "clave": "medio",
            "etiqueta": "Rentabilidad media",
            "clase": "warning",
        }
    return {
        "clave": "bajo",
        "etiqueta": "Rentabilidad baja",
        "clase": "danger",
    }


def _snapshot_rf12_base(producto: Producto, receta: Receta | None) -> dict:
    rendimiento = Decimal(str(receta.rendimiento_base or 0)) if receta else Decimal("0")
    return {
        "id_producto": producto.id_producto,
        "codigo": f"P-{int(producto.id_producto):03d}",
        "producto": producto,
        "receta": receta,
        "receta_nombre": receta.nombre if receta else "Sin receta",
        "receta_version": receta.version if receta else None,
        "rendimiento_base": rendimiento,
        "unidad_produccion": (receta.unidad_produccion if receta else "pieza")
        or "pieza",
        "precio_venta": _to_mxn(Decimal(str(producto.precio_venta or 0))),
        "costo_produccion": Decimal("0"),
        "costo_unitario": Decimal("0"),
        "utilidad_unitaria": Decimal("0"),
        "porcentaje_utilidad": Decimal("0"),
        "estado_margen": {
            "clave": "sin_datos",
            "etiqueta": "Sin datos",
            "clase": "neutral",
        },
        "ingredientes": [],
        "mensajes": [],
        "es_calculable": True,
    }


def _marcar_snapshot_no_calculable(payload: dict, mensaje: str) -> None:
    payload["es_calculable"] = False
    payload["mensajes"].append(mensaje)


def _acumular_ingrediente_rf12(payload: dict, detalle: DetalleReceta) -> Decimal:
    materia = detalle.materia_prima
    if not materia or not materia.activa:
        _marcar_snapshot_no_calculable(
            payload,
            f"Ingrediente inactivo o inexistente en receta: #{detalle.id_detalle}.",
        )
        return Decimal("0")

    cantidad_requerida = Decimal(str(detalle.cantidad_base or 0))
    porcentaje_merma = Decimal(str(materia.porcentaje_merma or 0))
    costo_unitario_mp = Decimal(str(materia.costo_unitario or 0))

    if cantidad_requerida <= 0:
        _marcar_snapshot_no_calculable(
            payload,
            f"Cantidad inválida para {materia.nombre} en la receta.",
        )
        return Decimal("0")

    if costo_unitario_mp <= 0:
        _marcar_snapshot_no_calculable(
            payload,
            f"La materia prima {materia.nombre} no tiene costo unitario definido.",
        )

    cantidad_real = cantidad_requerida * (
        Decimal("1") + (porcentaje_merma / Decimal("100"))
    )
    costo_ingrediente = cantidad_real * costo_unitario_mp
    payload["ingredientes"].append(
        {
            "id_materia": materia.id_materia,
            "materia": materia.nombre,
            "unidad": materia.unidad_base.abreviatura if materia.unidad_base else "",
            "cantidad_requerida": cantidad_requerida,
            "porcentaje_merma": _to_mxn(porcentaje_merma),
            "cantidad_real": cantidad_real,
            "costo_unitario": _to_mxn(costo_unitario_mp),
            "costo_ingrediente": _to_mxn(costo_ingrediente),
        }
    )
    return costo_ingrediente


def _finalizar_snapshot_rf12(payload: dict) -> dict:
    costo_unitario = Decimal("0")
    if payload["rendimiento_base"] > 0:
        costo_unitario = _to_mxn(
            payload["costo_produccion"] / payload["rendimiento_base"]
        )

    utilidad = _to_mxn(payload["precio_venta"] - costo_unitario)
    porcentaje_utilidad = (
        _to_mxn((utilidad / payload["precio_venta"]) * Decimal("100"))
        if payload["precio_venta"] > 0
        else Decimal("0")
    )

    payload["costo_unitario"] = costo_unitario
    payload["utilidad_unitaria"] = utilidad
    payload["porcentaje_utilidad"] = porcentaje_utilidad
    payload["estado_margen"] = (
        _clasificar_margen(porcentaje_utilidad)
        if payload["es_calculable"]
        else {
            "clave": "sin_datos",
            "etiqueta": "Información incompleta",
            "clase": "neutral",
        }
    )
    return payload


def _calcular_snapshot_rf12(producto: Producto) -> dict:
    receta = producto.receta_base
    payload = _snapshot_rf12_base(producto, receta)

    if not receta or not receta.activa:
        _marcar_snapshot_no_calculable(
            payload,
            "El producto no tiene una receta activa asociada.",
        )
        return payload

    rendimiento = Decimal(str(receta.rendimiento_base or 0))
    if rendimiento <= 0:
        _marcar_snapshot_no_calculable(
            payload,
            "La receta tiene rendimiento base inválido.",
        )
        return payload

    costo_total = Decimal("0")
    detalles = receta.detalles or []
    if not detalles:
        _marcar_snapshot_no_calculable(
            payload,
            "La receta no tiene ingredientes registrados.",
        )
        return payload

    for detalle in detalles:
        costo_total += _acumular_ingrediente_rf12(payload, detalle)

    payload["costo_produccion"] = _to_mxn(costo_total)
    return _finalizar_snapshot_rf12(payload)


def _contexto_costos_utilidad(*, q: str, filtro_margen: str, orden: str) -> dict:
    productos = (
        Producto.query.options(
            selectinload(Producto.receta_base)
            .selectinload(Receta.detalles)
            .selectinload(DetalleReceta.materia_prima)
            .selectinload(MateriaPrima.unidad_base)
        )
        .filter_by(activo=True)
        .order_by(Producto.nombre.asc())
        .all()
    )

    snapshots = [_calcular_snapshot_rf12(producto) for producto in productos]
    if q:
        snapshots = [
            row
            for row in snapshots
            if q in row["producto"].nombre.lower() or q in row["codigo"].lower()
        ]

    if filtro_margen != "todos":
        snapshots = [
            row for row in snapshots if row["estado_margen"]["clave"] == filtro_margen
        ]

    if orden == "margen_asc":
        snapshots.sort(key=lambda row: row["porcentaje_utilidad"])
    elif orden == "utilidad_desc":
        snapshots.sort(key=lambda row: row["utilidad_unitaria"], reverse=True)
    elif orden == "utilidad_asc":
        snapshots.sort(key=lambda row: row["utilidad_unitaria"])
    elif orden == "costo_desc":
        snapshots.sort(key=lambda row: row["costo_unitario"], reverse=True)
    elif orden == "costo_asc":
        snapshots.sort(key=lambda row: row["costo_unitario"])
    elif orden == "nombre_asc":
        snapshots.sort(key=lambda row: row["producto"].nombre.lower())
    else:
        snapshots.sort(key=lambda row: row["porcentaje_utilidad"], reverse=True)

    filas_calculables = [row for row in snapshots if row["es_calculable"]]
    margen_promedio = Decimal("0")
    if filas_calculables:
        total_margen = sum(row["porcentaje_utilidad"] for row in filas_calculables)
        margen_promedio = _to_mxn(total_margen / Decimal(str(len(filas_calculables))))

    return {
        "costos": snapshots,
        "resumen": {
            "total_productos": len(snapshots),
            "calculables": len(filas_calculables),
            "incompletos": len(snapshots) - len(filas_calculables),
            "margen_promedio": margen_promedio,
        },
        "filtros": {"q": q, "margen": filtro_margen, "orden": orden},
    }


def _generar_corte_diario(id_usuario: int) -> None:
    fecha = utc_today()
    ventas_hoy = (
        Venta.query.filter(db.func.date(Venta.fecha) == fecha)
        .filter(Venta.estado != "CANCELADO")
        .all()
    )
    total_ventas = sum(Decimal(str(venta.total)) for venta in ventas_hoy)
    numero_ventas = len(ventas_hoy)
    total_efectivo = sum(
        Decimal(str(venta.total))
        for venta in ventas_hoy
        if (venta.tipo_pago or "").upper() == "EFECTIVO"
    )
    total_tarjeta = sum(
        Decimal(str(venta.total))
        for venta in ventas_hoy
        if (venta.tipo_pago or "").upper() == "TARJETA"
    )
    costo_produccion = Decimal("0")

    for venta in ventas_hoy:
        for detalle in venta.detalles:
            if detalle.costo_unitario_produccion is not None:
                costo_produccion += Decimal(
                    str(detalle.costo_unitario_produccion)
                ) * Decimal(str(detalle.cantidad))
                continue

            try:
                costo_produccion += calcular_costo_producto(
                    id_producto=detalle.id_producto,
                    cantidad=detalle.cantidad,
                )
            except ValueError:
                # Legacy sin snapshot de costo, mantener continuidad del corte.
                pass

    salidas_hoy = SalidaEfectivo.query.filter(
        db.func.date(SalidaEfectivo.fecha_creacion) == fecha
    ).all()
    total_salidas = sum(Decimal(str(salida.monto)) for salida in salidas_hoy)
    utilidad = total_ventas - total_salidas - costo_produccion

    corte = CorteDiario(
        fecha=fecha,
        total_ventas=_to_mxn(total_ventas),
        numero_ventas=numero_ventas,
        total_transacciones=numero_ventas,
        total_efectivo=_to_mxn(total_efectivo),
        total_tarjeta=_to_mxn(total_tarjeta),
        total_salidas=_to_mxn(total_salidas),
        costo_produccion=_to_mxn(costo_produccion),
        utilidad_diaria=_to_mxn(utilidad),
        salida_efectivo_proveedores=_to_mxn(total_salidas),
        id_usuario=id_usuario,
    )

    existente = CorteDiario.query.filter_by(fecha=fecha).first()
    if existente:
        existente.total_ventas = corte.total_ventas
        existente.numero_ventas = corte.numero_ventas
        existente.total_transacciones = corte.total_transacciones
        existente.total_efectivo = corte.total_efectivo
        existente.total_tarjeta = corte.total_tarjeta
        existente.total_salidas = corte.total_salidas
        existente.costo_produccion = corte.costo_produccion
        existente.utilidad_diaria = corte.utilidad_diaria
        existente.salida_efectivo_proveedores = corte.salida_efectivo_proveedores
        existente.id_usuario = id_usuario
    else:
        db.session.add(corte)

    db.session.commit()

    log_audit_event(
        "CORTE_DIARIO_GENERADO",
        (
            f"fecha={fecha}; total_ventas={total_ventas}; "
            f"total_salidas={total_salidas}; utilidad={utilidad}"
        ),
    )


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

    upload_folder = os.path.join(
        current_app.root_path,
        "static",
        "img",
        "productos",
    )
    os.makedirs(upload_folder, exist_ok=True)

    file_path = os.path.join(upload_folder, filename)
    file.save(file_path)

    return f"img/productos/{filename}"


def _serializar_movimiento_producto(
    movimiento: MovimientoInventarioProducto,
) -> dict:
    return {
        "id_movimiento": movimiento.id_movimiento,
        "tipo": movimiento.tipo,
        "cantidad": int(movimiento.cantidad or 0),
        "stock_anterior": int(movimiento.stock_anterior or 0),
        "stock_posterior": int(movimiento.stock_posterior or 0),
        "fecha": (
            movimiento.fecha_creacion.isoformat() if movimiento.fecha_creacion else ""
        ),
        "fecha_texto": (
            movimiento.fecha_creacion.strftime("%d/%m/%Y %H:%M")
            if movimiento.fecha_creacion
            else ""
        ),
        "referencia_id": movimiento.referencia_id or "",
        "usuario": (movimiento.usuario.username if movimiento.usuario else ""),
    }


def _serializar_receta_para_producto(receta: Receta) -> dict:
    costo_unitario = _costo_receta_producto(receta)
    return {
        "id_receta": receta.id_receta,
        "nombre": receta.nombre,
        "version": receta.version,
        "producto_nombre": receta.producto.nombre if receta.producto else "",
        "costo_unitario": float(costo_unitario),
        "rendimiento_base": float(receta.rendimiento_base or 0),
        "unidad_produccion": receta.unidad_produccion or "pieza",
    }


def _obtener_o_crear_ticket_venta(venta: Venta) -> TicketVenta:
    ticket = TicketVenta.query.filter_by(id_venta=venta.id_venta).first()
    if ticket:
        return ticket

    nombre_negocio = (
        str(current_app.config.get("BUSINESS_NAME", "SoftBakery")).strip()
        or "SoftBakery"
    )
    ticket = TicketVenta(
        id_venta=venta.id_venta,
        folio=f"SB-{venta.id_venta:06d}",
        nombre_negocio=nombre_negocio,
    )
    db.session.add(ticket)
    if not venta.requiere_ticket:
        venta.requiere_ticket = True
    db.session.commit()
    return ticket


def _serializar_producto_terminado(
    producto: Producto,
    metrica: dict,
) -> dict:
    costo_unitario = Decimal(str(metrica.get("costo_unitario", 0)))
    precio_sugerido = Decimal(str(metrica.get("precio_sugerido", 0)))
    precio_venta = Decimal(str(producto.precio_venta or 0))
    utilidad_unitaria = Decimal(str(metrica.get("utilidad_unitaria", 0)))
    porcentaje_utilidad = Decimal(str(metrica.get("porcentaje_utilidad", 0)))

    return {
        "id_producto": producto.id_producto,
        "nombre": producto.nombre,
        "descripcion": producto.descripcion or "",
        "precio_venta": float(precio_venta),
        "unidad_venta": producto.unidad_venta,
        "cantidad_disponible": int(producto.cantidad_disponible or 0),
        "stock_minimo": int(producto.stock_minimo or 0),
        "activo": bool(producto.activo),
        "imagen": producto.imagen or "",
        "id_receta": producto.id_receta or 0,
        "margen_objetivo_pct": float(producto.margen_objetivo_pct or 25),
        "costo_produccion_actual": float(costo_unitario),
        "precio_sugerido": float(precio_sugerido),
        "utilidad_unitaria": float(utilidad_unitaria),
        "porcentaje_utilidad": float(porcentaje_utilidad),
        "estado_stock": producto.estado_stock,
        "esta_bajo_stock": producto.esta_bajo_stock,
        "fecha_actualizacion": (
            producto.fecha_actualizacion.isoformat()
            if producto.fecha_actualizacion
            else ""
        ),
        "fecha_creacion": (
            producto.fecha_creacion.isoformat() if producto.fecha_creacion else ""
        ),
    }


@sales_bp.route("/producto-terminado", methods=["GET", "POST"])
@login_required
@require_permission("Producto Terminado", "leer")
def producto_terminado():
    can_manage = _can_manage_producto_terminado()
    form_producto = ProductoTerminadoForm(prefix="producto")

    # Choices for id_receta are only populated for the form so that WTForms validation works.
    recetas_activas_all = (
        Receta.query.filter_by(activa=True)
        .order_by(Receta.id_producto.asc(), Receta.version.desc())
        .all()
    )
    # We add 0 as a default option just in case
    form_producto.id_receta.choices = [(0, "Sin receta vinculada")] + [
        (r.id_receta, f"v{r.version} · {r.nombre}") for r in recetas_activas_all
    ]

    def _resolve_producto_action() -> str:
        """Resuelve action considerando campos duplicados/prefijados del form."""
        action_values: list[str] = []

        for key in ("action", form_producto.action.name):
            try:
                values = request.form.getlist(key)
            except Exception:
                values = [request.form.get(key)]

            for value in values:
                if isinstance(value, str) and value.strip():
                    action_values.append(value.strip().lower())

        form_action_data = getattr(form_producto.action, "data", None)
        if isinstance(form_action_data, str) and form_action_data.strip():
            action_values.append(form_action_data.strip().lower())

        action = action_values[-1] if action_values else ""

        # Fallback: si viene el formulario prefijado de producto pero sin action,
        # asumimos creación para poder mostrar errores correctamente.
        if not action and any(k.startswith("producto-") for k in request.form.keys()):
            action = "crear"

        return action

    if request.method == "POST":
        if not can_manage:
            flash("No tienes permisos para modificar productos.", "warning")
            return redirect(url_for("sales.producto_terminado"))

        action = _resolve_producto_action()
        image_file = request.files.get("imagen_archivo")
        try:
            image_path = _handle_image_upload(image_file)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("sales.producto_terminado"))

        if action == "crear":
            if form_producto.validate_on_submit():
                try:
                    nombre = form_producto.nombre.data.strip()
                    descripcion = (
                        form_producto.descripcion.data.strip() or "Sin descripcion"
                    )
                    precio = form_producto.precio_venta.data
                    margen_objetivo_pct = (
                        form_producto.margen_objetivo_pct.data or Decimal("25")
                    )
                    stock_inicial = form_producto.stock_inicial.data or 0
                    stock_minimo = form_producto.stock_minimo.data or 10
                    unidad_venta = form_producto.unidad_venta.data
                    id_receta = form_producto.id_receta.data or 0
                    imagen = image_path or (form_producto.imagen.data or "").strip()

                    if not nombre or not unidad_venta:
                        raise ValueError("Nombre y unidad de venta son obligatorios.")
                    if margen_objetivo_pct <= 0 or margen_objetivo_pct >= 100:
                        margen_objetivo_pct = Decimal("25")
                    if id_receta > 0:
                        raise ValueError(
                            "Primero crea el producto y luego asocia "
                            "su receta desde edición o el módulo de Recetas."
                        )

                    receta = None
                    costo_receta = Decimal("0")

                    if precio <= 0:
                        raise ValueError("El precio de venta debe ser mayor a cero.")

                    existe = Producto.query.filter(
                        db.func.lower(Producto.nombre) == nombre.lower()
                    ).first()
                    if existe:
                        raise ValueError("Ya existe un producto con ese nombre.")

                    producto = Producto(
                        nombre=nombre,
                        descripcion=descripcion,
                        precio_venta=precio,
                        unidad_venta=unidad_venta,
                        cantidad_disponible=stock_inicial,
                        stock_minimo=stock_minimo,
                        costo_produccion_actual=costo_receta,
                        margen_objetivo_pct=margen_objetivo_pct,
                        precio_sugerido=_precio_sugerido_desde_costo(
                            costo_receta, margen_objetivo_pct
                        ),
                        fecha_costo_actualizado=utc_now() if receta else None,
                        id_receta=receta.id_receta if receta else None,
                        activo=True,
                        imagen=imagen or None,
                    )
                    db.session.add(producto)
                    db.session.flush()

                    if stock_inicial > 0:
                        db.session.add(
                            MovimientoInventarioProducto(
                                id_producto=producto.id_producto,
                                tipo="ENTRADA",
                                cantidad=stock_inicial,
                                stock_anterior=0,
                                stock_posterior=stock_inicial,
                                referencia_id=f"ALTA-PROD-{producto.id_producto}",
                                id_usuario=current_user.id_usuario,
                            )
                        )

                    if producto.id_receta:
                        try:
                            recalcular_costo_y_precio_sugerido_producto(
                                id_producto=producto.id_producto
                            )
                        except ValueError:
                            pass

                    db.session.commit()
                    log_audit_event(
                        "PRODUCTO_CREADO",
                        f"id_producto={producto.id_producto}; nombre={producto.nombre}",
                    )
                    flash("Producto terminado creado.", "success")
                    return redirect(url_for("sales.producto_terminado"))
                except ValueError as exc:
                    db.session.rollback()
                    flash(str(exc), "danger")
            else:
                for field_errors in form_producto.errors.values():
                    for error in field_errors:
                        flash(error, "warning")

            action = "crear_fallido"

        if action == "editar" or action == "editar_fallido":
            # Using same fallback handling:
            if not getattr(request, "_editar_handled", False):
                id_producto = form_producto.id_producto.data or _int(
                    request.form.get("id_producto", "0")
                )
                producto = Producto.query.get_or_404(id_producto)

                if form_producto.validate_on_submit():
                    try:
                        nombre = form_producto.nombre.data.strip()
                        descripcion = form_producto.descripcion.data.strip()
                        precio = form_producto.precio_venta.data
                        margen_objetivo_pct = (
                            form_producto.margen_objetivo_pct.data or Decimal("25")
                        )
                        stock_minimo = max(form_producto.stock_minimo.data or 0, 0)
                        unidad_venta = form_producto.unidad_venta.data

                        id_receta_raw = form_producto.id_receta.data
                        receta = producto.receta_base
                        receta_auto_asignada = False
                        if id_receta_raw and id_receta_raw > 0:
                            receta = Receta.query.get(id_receta_raw)
                        elif not producto.id_receta:
                            receta_candidata = (
                                Receta.query.filter_by(
                                    id_producto=producto.id_producto,
                                    activa=True,
                                )
                                .order_by(Receta.version.desc())
                                .first()
                            )
                            if receta_candidata:
                                receta = receta_candidata
                                receta_auto_asignada = True

                        if receta:
                            if not receta.activa:
                                raise ValueError(
                                    "Selecciona una receta activa para el producto."
                                )
                            if int(receta.id_producto) != int(producto.id_producto):
                                raise ValueError(
                                    "La receta seleccionada no corresponde al producto."
                                )

                        if not nombre or not unidad_venta:
                            raise ValueError(
                                "Nombre y unidad de venta son obligatorios."
                            )
                        if margen_objetivo_pct <= 0 or margen_objetivo_pct >= 100:
                            margen_objetivo_pct = Decimal("25")

                        producto.nombre = nombre
                        producto.descripcion = descripcion or producto.descripcion
                        producto.precio_venta = (
                            precio if precio > 0 else producto.precio_venta
                        )
                        producto.margen_objetivo_pct = margen_objetivo_pct
                        producto.stock_minimo = stock_minimo
                        producto.unidad_venta = unidad_venta
                        producto.id_receta = receta.id_receta if receta else None

                        if image_path:
                            producto.imagen = image_path
                        elif (form_producto.imagen.data or "").strip():
                            producto.imagen = (form_producto.imagen.data or "").strip()

                        producto.activo = form_producto.activo.data == "on"

                        if producto.id_receta:
                            recalcular_costo_y_precio_sugerido_producto(
                                id_producto=producto.id_producto
                            )
                        else:
                            producto.costo_produccion_actual = Decimal("0")
                            producto.precio_sugerido = None
                            producto.fecha_costo_actualizado = None

                        db.session.commit()
                        log_audit_event(
                            "PRODUCTO_EDITADO",
                            f"id_producto={producto.id_producto}; nombre={producto.nombre}; activo={producto.activo}",
                        )
                        if receta_auto_asignada:
                            flash(
                                "Producto actualizado. Se vinculó automáticamente "
                                "la receta activa más reciente.",
                                "success",
                            )
                        else:
                            flash("Producto actualizado.", "success")
                        return redirect(url_for("sales.producto_terminado"))
                    except ValueError as exc:
                        db.session.rollback()
                        flash(str(exc), "danger")
                action = "editar_fallido"

    productos = Producto.query.order_by(Producto.id_producto.desc()).all()
    recetas_activas = (
        Receta.query.filter_by(activa=True)
        .order_by(Receta.id_producto.asc(), Receta.version.desc())
        .all()
    )
    recetas_por_producto: dict[int, list[dict]] = {}
    for receta in recetas_activas:
        recetas_por_producto.setdefault(receta.id_producto, []).append(
            {
                "id_receta": receta.id_receta,
                "version": receta.version,
                "nombre": receta.nombre,
                "rendimiento_base": float(receta.rendimiento_base or 0),
                "unidad_produccion": receta.unidad_produccion or "pieza",
                "costo_unitario": float(_costo_receta_producto(receta)),
            }
        )

    metricas_producto: dict[int, dict] = {}
    for producto in productos:
        costo_unitario = Decimal(str(producto.costo_produccion_actual or 0))
        if costo_unitario <= 0 and producto.id_receta:
            try:
                costo_unitario = calcular_costo_unitario_producto(
                    id_producto=producto.id_producto
                )
            except ValueError:
                costo_unitario = Decimal("0")

        precio_venta = Decimal(str(producto.precio_venta or 0))
        margen_objetivo = Decimal(str(producto.margen_objetivo_pct or 25))
        precio_sugerido = Decimal(str(producto.precio_sugerido or 0))
        if precio_sugerido <= 0:
            precio_sugerido = _precio_sugerido_desde_costo(
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

    productos_payload = [
        _serializar_producto_terminado(
            producto, metricas_producto[producto.id_producto]
        )
        for producto in productos
    ]
    stock_bajo = [
        producto
        for producto in productos
        if producto.activo and producto.esta_bajo_stock
    ]
    resumen_stock = {
        "activos": sum(1 for producto in productos if producto.activo),
        "inactivos": sum(1 for producto in productos if not producto.activo),
        "bajo": len(stock_bajo),
        "ok": sum(
            1
            for producto in productos
            if producto.activo and not producto.esta_bajo_stock
        ),
    }
    open_modal = None
    if request.method == "POST":
        action = _resolve_producto_action()
        if action == "crear" and not form_producto.validate():
            open_modal = "modalProducto_crear"
        elif action == "editar" and not form_producto.validate():
            open_modal = "modalProducto_editar"

    return render_template(
        "sales/producto_terminado.html",
        productos=productos,
        productos_payload=productos_payload,
        metricas_producto=metricas_producto,
        resumen_stock=resumen_stock,
        alertas_stock=stock_bajo[:6],
        can_manage=can_manage,
        recetas_por_producto=recetas_por_producto,
        utc_now=utc_now,
        form_producto=form_producto,
        open_modal=open_modal,
    )


@sales_bp.get("/producto-terminado/<int:id_producto>/movimientos")
@login_required
@require_permission("Producto Terminado", "leer")
def producto_terminado_movimientos(id_producto: int):
    producto = Producto.query.get_or_404(id_producto)
    movimientos = (
        MovimientoInventarioProducto.query.filter_by(id_producto=producto.id_producto)
        .order_by(MovimientoInventarioProducto.id_movimiento.desc())
        .limit(12)
        .all()
    )
    return jsonify(
        {
            "producto": {
                "id_producto": producto.id_producto,
                "nombre": producto.nombre,
                "cantidad_disponible": int(producto.cantidad_disponible or 0),
                "stock_minimo": int(producto.stock_minimo or 0),
                "estado_stock": producto.estado_stock,
            },
            "movimientos": [
                _serializar_movimiento_producto(movimiento)
                for movimiento in movimientos
            ],
        }
    )


@sales_bp.route("/solicitudes", methods=["GET", "POST"])
@login_required
@require_permission("Solicitudes", "leer")
def solicitudes():
    role_name = current_user.rol.nombre if current_user.rol else ""
    form_crear = SolicitudVentasCrearForm(prefix="crear")
    form_editar = SolicitudVentasEditarForm(prefix="editar")

    # Rellenar opciones de id_producto para validar
    productos_activos = (
        Producto.query.filter_by(activo=True).order_by(Producto.nombre.asc()).all()
    )
    form_crear.id_producto.choices = [
        (p.id_producto, p.nombre) for p in productos_activos if p.id_receta
    ]

    if request.method == "POST":
        if role_name not in {"Ventas", "Administrador"}:
            flash(
                "Solo ventas y administración pueden registrar solicitudes.",
                "danger",
            )
            return redirect(url_for("sales.solicitudes"))

        action = (request.form.get("action") or "crear").strip().lower()

        if action == "editar" or action == "editar_fallido":
            # Si no ha pasado por un "editar" normal
            id_solicitud = _int(
                request.form.get("id_solicitud", "0") or form_editar.id_solicitud.data
            )
            solicitud = SolicitudProduccion.query.get_or_404(id_solicitud)

            if form_editar.validate_on_submit():
                if solicitud.id_usuario_solicita != current_user.id_usuario:
                    flash("Solo puedes editar tus propias solicitudes.", "danger")
                    return redirect(url_for("sales.solicitudes"))

                if solicitud.estado != "PENDIENTE":
                    flash(
                        "Solo se pueden editar solicitudes en estado PENDIENTE.",
                        "warning",
                    )
                    return redirect(url_for("sales.solicitudes"))

                cantidad = form_editar.cantidad.data
                observaciones = form_editar.observaciones.data or None
                if cantidad <= 0:
                    flash("La cantidad debe ser mayor a cero.", "warning")
                    return redirect(url_for("sales.solicitudes"))

                solicitud.cantidad = cantidad
                solicitud.observaciones = observaciones
                db.session.commit()
                log_audit_event(
                    "SOLICITUD_PRODUCCION_EDITADA",
                    f"id_solicitud={solicitud.id_solicitud}; id_usuario={current_user.id_usuario}; cantidad={cantidad}",
                )
                flash("Solicitud actualizada correctamente.", "success")
                return redirect(url_for("sales.solicitudes"))

            action = "editar_fallido"

        if action == "crear":
            if form_crear.validate_on_submit():
                id_producto = form_crear.id_producto.data
                cantidad = form_crear.cantidad.data
                observaciones = form_crear.observaciones.data or None
                producto = Producto.query.get(id_producto)
                if not producto or not producto.activo or cantidad <= 0:
                    flash("Producto o cantidad invalida para solicitud.", "warning")
                    return redirect(url_for("sales.solicitudes"))

                receta = (
                    Receta.query.get(producto.id_receta) if producto.id_receta else None
                )
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

            action = "crear_fallido"

    estado_filtro = (request.args.get("estado") or "TODOS").strip().upper()
    busqueda = (request.args.get("q") or "").strip()

    productos = (
        Producto.query.filter_by(activo=True).order_by(Producto.nombre.asc()).all()
    )

    solicitudes_query = SolicitudProduccion.query.options(
        selectinload(SolicitudProduccion.producto),
        selectinload(SolicitudProduccion.pedido),
        selectinload(SolicitudProduccion.usuario_solicita),
        selectinload(SolicitudProduccion.usuario_resuelve),
        selectinload(SolicitudProduccion.ordenes),
    )

    # Administrador ve todas, Ventas ve solo sus propias
    if role_name != "Administrador":
        solicitudes_query = solicitudes_query.filter(
            SolicitudProduccion.id_usuario_solicita == current_user.id_usuario
        )
    if estado_filtro in {"PENDIENTE", "APROBADA", "RECHAZADA"}:
        solicitudes_query = solicitudes_query.filter(
            SolicitudProduccion.estado == estado_filtro
        )
    if busqueda:
        term = f"%{busqueda}%"
        solicitudes_query = solicitudes_query.join(Producto).filter(
            db.or_(
                Producto.nombre.ilike(term),
                db.cast(SolicitudProduccion.id_solicitud, db.String).ilike(term),
            )
        )

    solicitudes_data = solicitudes_query.order_by(
        SolicitudProduccion.id_solicitud.desc()
    ).all()

    productos_bajo_stock = {
        producto.id_producto: int(producto.cantidad_disponible or 0)
        < int(producto.stock_minimo or 0)
        for producto in productos
    }
    open_modal = None
    if request.method == "POST":
        action = request.form.get("action", "") or "crear"
        if action == "crear_fallido":
            open_modal = "modalNuevaSolicitud"
        elif action == "editar_fallido":
            open_modal = "modalEditarSolicitud"

    return render_template(
        "sales/solicitudes.html",
        solicitudes=solicitudes_data,
        estado_filtro=estado_filtro,
        busqueda=busqueda,
        productos=productos,
        role_name=role_name,
        form_crear=form_crear,
        form_editar=form_editar,
        open_modal=open_modal,
        productos_bajo_stock=productos_bajo_stock,
    )


@sales_bp.route("/pedidos-clientes", methods=["GET", "POST"])
@login_required
@require_permission("Pedidos Clientes", "leer")
def pedidos_clientes():
    estados_validos = {"PENDIENTE", "CONFIRMADO", "PAGADO", "ENTREGADO", "CANCELADO"}
    transiciones_permitidas = {
        "PENDIENTE": ["CONFIRMADO", "CANCELADO"],
        "CONFIRMADO": ["PAGADO"],
        "PAGADO": [],
        "ENTREGADO": [],
        "CANCELADO": [],
    }

    if request.method == "POST":
        action = request.form.get("action", "actualizar").strip().lower()
        id_pedido = _int(request.form.get("id_pedido", "0"))
        if action == "solicitar_produccion":
            pedido = (
                Pedido.query.options(
                    selectinload(Pedido.detalles).selectinload(DetallePedido.producto)
                )
                .filter(Pedido.id_pedido == id_pedido)
                .first()
            )
            if not pedido:
                flash("Pedido no encontrado.", "warning")
                return redirect(url_for("sales.pedidos_clientes"))

            if (pedido.estado_pago or "").upper() != "PAGADO":
                flash(
                    "Solo puedes solicitar producción para pedidos pagados.",
                    "warning",
                )
                return redirect(url_for("sales.pedidos_clientes"))

            if (pedido.estado_pedido or "").upper() in {"CANCELADO", "ENTREGADO"}:
                flash("El pedido ya no permite solicitudes de producción.", "warning")
                return redirect(url_for("sales.pedidos_clientes"))

            creadas = 0
            for detalle in pedido.detalles:
                producto = detalle.producto
                if not producto or not producto.activo:
                    continue

                receta_activa = (
                    Receta.query.filter_by(
                        id_producto=producto.id_producto,
                        activa=True,
                    )
                    .order_by(Receta.version.desc())
                    .first()
                )
                if not receta_activa:
                    continue

                solicitud_existente = (
                    SolicitudProduccion.query.filter_by(
                        id_pedido=pedido.id_pedido,
                        id_producto=producto.id_producto,
                    )
                    .filter(SolicitudProduccion.estado.in_(["PENDIENTE", "APROBADA"]))
                    .first()
                )
                if solicitud_existente:
                    continue

                db.session.add(
                    SolicitudProduccion(
                        id_producto=producto.id_producto,
                        id_pedido=pedido.id_pedido,
                        cantidad=int(detalle.cantidad or 0),
                        estado="PENDIENTE",
                        id_usuario_solicita=current_user.id_usuario,
                        observaciones=(
                            f"Solicitud automática desde pedido "
                            f"PED-{int(pedido.id_pedido):04d}"
                        ),
                    )
                )
                creadas += 1

            if creadas <= 0:
                flash(
                    "No se generaron solicitudes: ya existen o no hay receta activa.",
                    "warning",
                )
                return redirect(url_for("sales.pedidos_clientes"))

            db.session.commit()
            log_audit_event(
                "PEDIDO_SOLICITUD_PRODUCCION_CREADA",
                f"id_pedido={pedido.id_pedido}; solicitudes={creadas}",
            )
            flash(
                f"Se generaron {creadas} solicitud(es) de producción.",
                "success",
            )
            return redirect(url_for("sales.pedidos_clientes"))

        if action == "entregar":
            requiere_ticket = request.form.get("requiere_ticket") == "on"
            try:
                venta = generar_venta_desde_pedido(
                    id_pedido=id_pedido,
                    requiere_ticket=requiere_ticket,
                    id_usuario_accion=current_user.id_usuario,
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
                id_usuario_accion=current_user.id_usuario,
            )
            log_audit_event(
                "PEDIDO_ESTADO_ACTUALIZADO",
                f"id_pedido={id_pedido}; nuevo_estado={nuevo_estado}",
            )
            flash("Estado de pedido actualizado correctamente.", "success")
        except ValueError as exc:
            flash(str(exc), "warning")
        return redirect(url_for("sales.pedidos_clientes"))

    q = (request.args.get("q") or "").strip().lower()
    estado_filtro = (request.args.get("estado") or "TODOS").strip().upper()

    pedidos_query = Pedido.query.options(
        selectinload(Pedido.usuario_cliente).selectinload(Usuario.persona),
        selectinload(Pedido.detalles).selectinload(DetallePedido.producto),
        selectinload(Pedido.solicitudes_produccion).selectinload(
            SolicitudProduccion.ordenes
        ),
        selectinload(Pedido.historial_estados).selectinload(
            PedidoEstadoHistorial.usuario
        ),
    ).order_by(Pedido.id_pedido.desc())

    if estado_filtro in estados_validos:
        pedidos_query = pedidos_query.filter(Pedido.estado_pedido == estado_filtro)

    if q:
        term = f"%{q}%"
        pedidos_query = pedidos_query.filter(
            db.or_(
                db.cast(Pedido.id_pedido, db.String).ilike(term),
                Pedido.usuario_cliente.has(Usuario.username.ilike(term)),
                Pedido.usuario_cliente.has(
                    Usuario.persona.has(
                        db.or_(
                            Persona.nombre.ilike(term),
                            Persona.apellidos.ilike(term),
                        )
                    )
                ),
            )
        )

    pedidos = pedidos_query.all()

    resumen = {
        "total": len(pedidos),
        "pendiente": 0,
        "confirmado": 0,
        "pagado": 0,
        "entregado": 0,
        "cancelado": 0,
    }
    pedidos_payload: list[dict] = []

    for pedido in pedidos:
        estado_actual = (pedido.estado_pedido or "PENDIENTE").upper()
        resumen[estado_actual.lower()] = resumen.get(estado_actual.lower(), 0) + 1

        cliente_nombre = "Cliente"
        if pedido.usuario_cliente:
            persona = pedido.usuario_cliente.persona
            if persona:
                cliente_nombre = f"{persona.nombre} {persona.apellidos}".strip()
            else:
                cliente_nombre = pedido.usuario_cliente.username

        historial_payload = []
        for evento in pedido.historial_estados:
            historial_payload.append(
                {
                    "estado_anterior": evento.estado_anterior,
                    "estado_nuevo": evento.estado_nuevo,
                    "detalle": evento.detalle,
                    "usuario": evento.usuario.username if evento.usuario else "Sistema",
                    "fecha": (
                        evento.fecha_cambio.strftime("%d/%m/%Y %H:%M")
                        if evento.fecha_cambio
                        else ""
                    ),
                }
            )

        solicitudes_activas = _solicitudes_pedido_activas(pedido)
        produccion_lista = _produccion_pedido_lista(pedido)

        pedidos_payload.append(
            {
                "id_pedido": pedido.id_pedido,
                "folio": f"PED-{int(pedido.id_pedido):04d}",
                "cliente": cliente_nombre,
                "fecha_pedido": (
                    pedido.fecha_pedido.strftime("%d/%m/%Y")
                    if pedido.fecha_pedido
                    else ""
                ),
                "fecha_entrega": (
                    pedido.fecha_entrega.strftime("%d/%m/%Y")
                    if pedido.fecha_entrega
                    else ""
                ),
                "estado_pedido": estado_actual,
                "estado_pago": (pedido.estado_pago or "PENDIENTE").upper(),
                "referencia_pago": pedido.referencia_pago or "",
                "total": float(pedido.total or 0),
                "productos": [
                    {
                        "nombre": (
                            detalle.producto.nombre if detalle.producto else "Producto"
                        ),
                        "cantidad": int(detalle.cantidad or 0),
                        "precio_unitario": float(detalle.precio_unitario or 0),
                        "subtotal": float(detalle.subtotal or 0),
                    }
                    for detalle in pedido.detalles
                ],
                "historial": historial_payload,
                "acciones": {
                    "puede_entregar": (
                        estado_actual in {"CONFIRMADO", "PAGADO"}
                        and (pedido.estado_pago or "").upper() == "PAGADO"
                        and produccion_lista
                    ),
                    "puede_solicitar_produccion": (
                        estado_actual in {"CONFIRMADO", "PAGADO"}
                        and (pedido.estado_pago or "").upper() == "PAGADO"
                        and not solicitudes_activas
                    ),
                    "produccion_lista": produccion_lista,
                    "solicitudes_activas": len(solicitudes_activas),
                    "estados_siguientes": transiciones_permitidas.get(
                        estado_actual, []
                    ),
                },
            }
        )

    return render_template(
        "sales/pedidos_clientes.html",
        pedidos=pedidos,
        pedidos_payload=pedidos_payload,
        resumen=resumen,
        filtros={"q": q, "estado": estado_filtro},
        estados_validos=sorted(estados_validos),
    )


@sales_bp.route("/ventas", methods=["GET", "POST"])
@login_required
@require_permission("Ventas", "leer")
def ventas():
    tab = (request.args.get("tab") or "nueva").strip().lower()
    if tab not in {"nueva", "historial", "salidas", "corte"}:
        tab = "nueva"

    form_salida = SalidaEfectivoForm()

    if request.method == "POST":
        action = (request.form.get("action") or "registrar_venta").strip().lower()
        if action == "registrar_venta":
            tipo_pago = (request.form.get("tipo_pago") or "EFECTIVO").strip().upper()
            requiere_ticket = request.form.get("requiere_ticket") == "on"
            detalles_json = request.form.get("detalles_json", "").strip()

            try:
                if detalles_json:
                    detalles = json.loads(detalles_json)
                else:
                    detalles = [
                        {
                            "id_producto": _int(request.form.get("id_producto", "0")),
                            "cantidad": _int(request.form.get("cantidad", "1"), 1),
                        }
                    ]

                venta = registrar_venta_mostrador_detallada(
                    items=detalles,
                    tipo_pago=tipo_pago,
                    requiere_ticket=requiere_ticket,
                    id_usuario_emite=current_user.id_usuario,
                )
                log_audit_event(
                    "VENTA_REGISTRADA",
                    (
                        f"id_venta={venta.id_venta}; "
                        f"detalles={len(venta.detalles)}; "
                        f"tipo_pago={tipo_pago}; "
                        f"requiere_ticket={requiere_ticket}"
                    ),
                )
                flash("Venta registrada y stock actualizado.", "success")
            except (ValueError, json.JSONDecodeError) as exc:
                flash(str(exc), "warning")

            return redirect(url_for("sales.ventas", tab="nueva"))

        if action == "cancelar_venta":
            id_venta = _int(request.form.get("id_venta", "0"))
            try:
                venta = cancelar_venta_mostrador(
                    id_venta=id_venta,
                    id_usuario=current_user.id_usuario,
                )
                log_audit_event(
                    "VENTA_CANCELADA",
                    f"id_venta={venta.id_venta}; id_usuario={current_user.id_usuario}",
                )
                flash("Venta cancelada y stock restablecido.", "success")
            except ValueError as exc:
                flash(str(exc), "warning")

            return redirect(url_for("sales.ventas", tab="historial"))

        if action == "registrar_salida":
            if form_salida.validate_on_submit():
                concepto = form_salida.concepto.data.strip()
                monto = _dec(str(form_salida.monto.data))
                tipo = form_salida.tipo.data.strip().upper()
                referencia_tipo = (
                    request.form.get("referencia_tipo", "MANUAL").strip().upper()
                )
                referencia_id = _int(request.form.get("referencia_id", "0"), 0)
                if not concepto or monto <= 0:
                    flash("Concepto y monto válido son obligatorios.", "warning")
                    return redirect(url_for("sales.ventas", tab="salidas"))

                db.session.add(
                    SalidaEfectivo(
                        concepto=concepto,
                        monto=_to_mxn(monto),
                        tipo=tipo,
                        id_usuario=current_user.id_usuario,
                        referencia=request.form.get("referencia", "").strip() or None,
                        referencia_tipo=referencia_tipo or None,
                        referencia_id=referencia_id if referencia_id > 0 else None,
                    )
                )
                db.session.commit()
                log_audit_event(
                    "SALIDA_EFECTIVO_REGISTRADA",
                    f"concepto={concepto}; monto={monto}; tipo={tipo}",
                )
                flash("Salida registrada correctamente.", "success")
                return redirect(url_for("sales.ventas", tab="salidas"))

        elif action == "generar_corte":
            rol_nombre = current_user.rol.nombre if current_user.rol else ""
            if rol_nombre != "Administrador":
                flash("Solo administración puede generar el corte diario.", "warning")
                return redirect(url_for("sales.ventas", tab="corte"))

            _generar_corte_diario(current_user.id_usuario)
            flash("Corte diario generado correctamente.", "success")
            return redirect(url_for("sales.ventas", tab="corte"))

        elif action not in ["registrar_venta", "cancelar_venta", "registrar_salida"]:
            flash("Acción no reconocida en módulo de ventas.", "warning")
            return redirect(url_for("sales.ventas", tab=tab))

    productos = (
        Producto.query.filter(
            Producto.activo.is_(True),
            (Producto.cantidad_disponible - Producto.cantidad_reservada) > 0,
        )
        .order_by(Producto.nombre.asc())
        .all()
    )

    q = (request.args.get("q") or "").strip().lower()
    filtro_pago = (request.args.get("pago") or "TODOS").strip().upper()
    filtro_estado = (request.args.get("estado") or "TODOS").strip().upper()

    ventas_query = Venta.query.options(
        selectinload(Venta.detalles).selectinload(DetalleVenta.producto),
        selectinload(Venta.usuario_cliente),
    ).order_by(Venta.id_venta.desc())
    if q:
        term = f"%{q}%"
        ventas_query = ventas_query.filter(
            db.or_(
                db.cast(Venta.id_venta, db.String).ilike(term),
                db.cast(Venta.total, db.String).ilike(term),
            )
        )
    if filtro_pago in {"EFECTIVO", "TARJETA"}:
        ventas_query = ventas_query.filter(Venta.tipo_pago == filtro_pago)
    if filtro_estado in {"CONFIRMADO", "CANCELADO", "EN_PROCESO_PRODUCCION"}:
        ventas_query = ventas_query.filter(Venta.estado == filtro_estado)

    data = ventas_query.limit(200).all()
    ids_venta = [venta.id_venta for venta in data]
    tickets = []
    if ids_venta:
        tickets = TicketVenta.query.filter(TicketVenta.id_venta.in_(ids_venta)).all()
    tickets_por_venta = {ticket.id_venta: ticket for ticket in tickets}

    fecha_hoy = utc_today()
    ventas_hoy = Venta.query.filter(db.func.date(Venta.fecha) == fecha_hoy).all()
    ventas_hoy_activas = [venta for venta in ventas_hoy if venta.estado != "CANCELADO"]
    total_ventas_hoy = sum(
        Decimal(str(venta.total or 0)) for venta in ventas_hoy_activas
    )
    total_efectivo_hoy = sum(
        Decimal(str(venta.total or 0))
        for venta in ventas_hoy_activas
        if (venta.tipo_pago or "").upper() == "EFECTIVO"
    )
    total_tarjeta_hoy = sum(
        Decimal(str(venta.total or 0))
        for venta in ventas_hoy_activas
        if (venta.tipo_pago or "").upper() == "TARJETA"
    )
    costo_produccion_hoy = Decimal("0")
    for venta in ventas_hoy_activas:
        for detalle in venta.detalles:
            if detalle.costo_unitario_produccion is not None:
                costo_produccion_hoy += Decimal(
                    str(detalle.costo_unitario_produccion or 0)
                ) * Decimal(str(detalle.cantidad or 0))
                continue
            try:
                costo_produccion_hoy += calcular_costo_producto(
                    id_producto=detalle.id_producto,
                    cantidad=detalle.cantidad,
                )
            except ValueError:
                continue

    salidas_hoy = (
        SalidaEfectivo.query.filter(
            db.func.date(SalidaEfectivo.fecha_creacion) == fecha_hoy
        )
        .order_by(SalidaEfectivo.id_salida.desc())
        .all()
    )
    total_salidas_hoy = sum(Decimal(str(salida.monto or 0)) for salida in salidas_hoy)
    utilidad_hoy = total_ventas_hoy - total_salidas_hoy - costo_produccion_hoy

    cortes_previos = (
        CorteDiario.query.order_by(
            CorteDiario.fecha.desc(), CorteDiario.id_corte.desc()
        )
        .limit(7)
        .all()
    )

    return render_template(
        "sales/ventas.html",
        ventas=data,
        productos=productos,
        tickets_por_venta=tickets_por_venta,
        tab=tab,
        filtros={"q": q, "pago": filtro_pago, "estado": filtro_estado},
        resumen_dia={
            "fecha": fecha_hoy,
            "total_ventas": _to_mxn(total_ventas_hoy),
            "total_efectivo": _to_mxn(total_efectivo_hoy),
            "total_tarjeta": _to_mxn(total_tarjeta_hoy),
            "total_salidas": _to_mxn(total_salidas_hoy),
            "costo_produccion": _to_mxn(costo_produccion_hoy),
            "utilidad": _to_mxn(utilidad_hoy),
            "transacciones": len(ventas_hoy_activas),
        },
        salidas_hoy=salidas_hoy,
        cortes_previos=cortes_previos,
        puede_generar_corte=(
            current_user.rol is not None and current_user.rol.nombre == "Administrador"
        ),
        form_salida=form_salida,
    )


@sales_bp.get("/ventas/<int:id_venta>/ticket")
@login_required
@require_permission("Ventas", "leer")
def ver_ticket_venta(id_venta: int):
    venta = (
        Venta.query.options(
            selectinload(Venta.detalles).selectinload(DetalleVenta.producto),
            selectinload(Venta.usuario_cliente).selectinload(Usuario.persona),
        )
        .filter(Venta.id_venta == id_venta)
        .first_or_404()
    )
    ticket = _obtener_o_crear_ticket_venta(venta)

    cliente = "Mostrador"
    if venta.usuario_cliente:
        if venta.usuario_cliente.persona:
            cliente = (
                f"{venta.usuario_cliente.persona.nombre} "
                f"{venta.usuario_cliente.persona.apellidos}"
            ).strip()
        else:
            cliente = venta.usuario_cliente.username

    total_detalle = sum(Decimal(str(d.subtotal or 0)) for d in venta.detalles)
    if total_detalle <= 0:
        total_detalle = Decimal(str(venta.total or 0))

    html = render_template(
        "sales/ticket_venta.html",
        venta=venta,
        ticket=ticket,
        cliente=cliente,
        total_detalle=total_detalle,
        download_mode=request.args.get("download") == "1",
    )

    if request.args.get("download") == "1":
        response = make_response(html)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="ticket-{ticket.folio}.html"'
        )
        return response

    return html


@sales_bp.route("/compras-mp", methods=["GET", "POST"])
@login_required
@require_permission("Compras MP", "leer")
def compras_mp():
    if request.method == "POST":
        id_proveedor = _int(request.form.get("id_proveedor", "0"))
        estado_pago_form = request.form.get("estado_pago", "PENDIENTE").strip().upper()
        if estado_pago_form not in {"PENDIENTE", "PAGADO"}:
            flash("Estado de pago invalido.", "danger")
            return redirect(url_for("sales.compras_mp"))

        fecha_compra = _parse_fecha_compra(request.form.get("fecha"))
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
            fecha=fecha_compra,
        )
        try:
            registrar_compra(compra, detalles_compra)
            if estado_pago_form == "PAGADO":
                pagar_compra(
                    id_compra=compra.id_compra,
                    id_usuario=current_user.id_usuario,
                )
                log_audit_event("COMPRA_MP_PAGADA", f"id_compra={compra.id_compra}")
            log_audit_event(
                "COMPRA_MP_REGISTRADA",
                f"id_compra={compra.id_compra}; id_proveedor={id_proveedor}; detalles={len(detalles_compra)}",
            )
            flash(
                (
                    "Compra registrada y pagada."
                    if estado_pago_form == "PAGADO"
                    else "Compra registrada."
                ),
                "success",
            )
        except ValueError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("sales.compras_mp"))

    compras = (
        Compra.query.options(
            selectinload(Compra.proveedor),
            selectinload(Compra.comprador),
            selectinload(Compra.detalles).selectinload(DetalleCompra.materia_prima),
        )
        .order_by(Compra.id_compra.desc())
        .all()
    )
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
        fecha_hoy=utc_today().isoformat(),
    )


@sales_bp.get("/compras-mp/<int:id_compra>/detalle")
@login_required
@require_permission("Compras MP", "leer")
def compra_mp_detalle(id_compra: int):
    compra = (
        Compra.query.options(
            selectinload(Compra.proveedor),
            selectinload(Compra.comprador),
            selectinload(Compra.detalles).selectinload(DetalleCompra.materia_prima),
            selectinload(Compra.detalles).selectinload(DetalleCompra.unidad_compra),
        )
        .filter(Compra.id_compra == id_compra)
        .first_or_404()
    )
    return jsonify(_compra_payload(compra))


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
        referencia_tipo = request.form.get("referencia_tipo", "MANUAL").strip().upper()
        referencia_id = _int(request.form.get("referencia_id", "0"), 0)
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
                referencia_tipo=referencia_tipo or None,
                referencia_id=referencia_id if referencia_id > 0 else None,
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
@sales_bp.route("/costos-utilidad", methods=["GET", "POST"])
@login_required
@require_permission("Costos y Utilidad", "leer")
def cortes():
    if request.method == "POST":
        _generar_corte_diario(current_user.id_usuario)
        flash("Corte diario generado.", "success")
        return redirect(url_for("sales.cortes"))

    data = CorteDiario.query.order_by(CorteDiario.id_corte.desc()).all()
    q = (request.args.get("q") or "").strip().lower()
    filtro_margen = (request.args.get("margen") or "todos").strip().lower()
    orden = (request.args.get("orden") or "margen_desc").strip().lower()

    contexto = _contexto_costos_utilidad(
        q=q,
        filtro_margen=filtro_margen,
        orden=orden,
    )

    return render_template(
        "sales/costos_utilidad.html",
        cortes=data,
        costos=contexto["costos"],
        resumen=contexto["resumen"],
        filtros=contexto["filtros"],
    )


@sales_bp.get("/costos-utilidad/<int:id_producto>/desglose")
@login_required
@require_permission("Costos y Utilidad", "leer")
def costos_utilidad_desglose(id_producto: int):
    producto = (
        Producto.query.options(
            selectinload(Producto.receta_base)
            .selectinload(Receta.detalles)
            .selectinload(DetalleReceta.materia_prima)
            .selectinload(MateriaPrima.unidad_base)
        )
        .filter(Producto.id_producto == id_producto)
        .first_or_404()
    )
    snapshot = _calcular_snapshot_rf12(producto)
    return jsonify(
        {
            "id_producto": snapshot["id_producto"],
            "codigo": snapshot["codigo"],
            "producto": snapshot["producto"].nombre,
            "receta": snapshot["receta_nombre"],
            "version": snapshot["receta_version"],
            "rendimiento_base": float(snapshot["rendimiento_base"]),
            "unidad_produccion": snapshot["unidad_produccion"],
            "precio_venta": float(snapshot["precio_venta"]),
            "costo_produccion": float(snapshot["costo_produccion"]),
            "costo_unitario": float(snapshot["costo_unitario"]),
            "utilidad_unitaria": float(snapshot["utilidad_unitaria"]),
            "porcentaje_utilidad": float(snapshot["porcentaje_utilidad"]),
            "estado_margen": snapshot["estado_margen"],
            "es_calculable": snapshot["es_calculable"],
            "mensajes": snapshot["mensajes"],
            "ingredientes": [
                {
                    "materia": item["materia"],
                    "unidad": item["unidad"],
                    "cantidad_requerida": float(item["cantidad_requerida"]),
                    "porcentaje_merma": float(item["porcentaje_merma"]),
                    "cantidad_real": float(item["cantidad_real"]),
                    "costo_unitario": float(item["costo_unitario"]),
                    "costo_ingrediente": float(item["costo_ingrediente"]),
                }
                for item in snapshot["ingredientes"]
            ],
        }
    )
