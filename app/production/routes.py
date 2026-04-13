from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.common.security import log_audit_event, require_permission
from app.common.services import (
    actualizar_materia_prima,
    cancelar_orden_produccion,
    crear_materia_prima,
    crear_orden_produccion,
    desactivar_materia_prima,
    finalizar_orden_produccion,
    iniciar_orden_produccion,
    recalcular_costo_y_precio_sugerido_producto,
)
from app.extensions import db
from app.models import (
    DetalleReceta,
    MateriaPrima,
    Modulo,
    MovimientoInventarioMP,
    OrdenProduccion,
    Permiso,
    Producto,
    Receta,
    SolicitudProduccion,
    UnidadMedida,
    utc_now,
)
from app.production import production_bp
from app.production.forms import MateriaPrimaForm, AjusteInventarioForm, RecetaBaseForm, OrdenProduccionForm, ResolverSolicitudForm


def _decimal(value: str, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _decimal_text(value: Decimal | int | float | None, precision: int = 4) -> str:
    decimal_value = Decimal(str(value or 0))
    formatted = f"{decimal_value:.{precision}f}"
    return formatted.rstrip("0").rstrip(".") or "0"


def _fecha_texto(value) -> str:
    if not value:
        return ""
    return value.strftime("%d/%m/%Y")


def _normalizar_detalles_receta(
    ids_materia: list[str],
    cantidades: list[str],
) -> tuple[list[tuple[int, Decimal]], str | None]:
    if not ids_materia or not cantidades or len(ids_materia) != len(cantidades):
        return [], "Debes registrar al menos un ingrediente valido."

    detalles: list[tuple[int, Decimal]] = []
    materias_vistas: set[int] = set()
    for id_materia_raw, cantidad_raw in zip(ids_materia, cantidades):
        id_materia = _int(id_materia_raw, 0)
        cantidad = _decimal(cantidad_raw, "0")
        if id_materia <= 0 or cantidad <= 0:
            continue

        if id_materia in materias_vistas:
            return [], "No puedes repetir la misma materia prima en una receta."

        materias_vistas.add(id_materia)
        detalles.append((id_materia, cantidad))

    if not detalles:
        return [], "Debes registrar al menos un ingrediente con cantidad mayor a cero."

    return detalles, None


def _firma_detalles_receta(receta: Receta) -> list[tuple[int, str]]:
    return sorted(
        (
            int(detalle.id_materia_prima),
            _decimal_text(detalle.cantidad_base),
        )
        for detalle in receta.detalles
    )


def _calcular_explosion_receta(
    receta: Receta, cantidad_producir: Decimal | int
) -> tuple[list[dict], bool]:
    cantidad = Decimal(str(cantidad_producir or 0))
    rendimiento = Decimal(str(receta.rendimiento_base or 0))
    if cantidad <= 0 or rendimiento <= 0:
        return [], False

    factor = cantidad / rendimiento
    filas: list[dict] = []
    puede_producir = True
    for detalle in sorted(receta.detalles, key=lambda item: item.id_detalle):
        materia = detalle.materia_prima
        cantidad_requerida = Decimal(str(detalle.cantidad_base)) * factor
        cantidad_disponible = Decimal(str(materia.cantidad_disponible or 0))
        suficiente = cantidad_disponible >= cantidad_requerida
        puede_producir = puede_producir and suficiente
        filas.append(
            {
                "id_detalle": detalle.id_detalle,
                "id_materia_prima": detalle.id_materia_prima,
                "materia_nombre": materia.nombre,
                "unidad_base": materia.unidad_base.abreviatura,
                "cantidad_base": _decimal_text(detalle.cantidad_base),
                "cantidad_requerida": _decimal_text(cantidad_requerida),
                "cantidad_disponible": _decimal_text(cantidad_disponible),
                "suficiente": suficiente,
            }
        )

    return filas, puede_producir


def _serializar_receta(receta: Receta) -> dict:
    detalles, puede_producir_base = _calcular_explosion_receta(
        receta, receta.rendimiento_base
    )
    return {
        "id_receta": receta.id_receta,
        "id_producto": receta.id_producto,
        "producto_nombre": receta.producto.nombre if receta.producto else receta.nombre,
        "nombre": receta.nombre,
        "descripcion": receta.descripcion or "",
        "categoria": receta.categoria or "",
        "unidad_produccion": receta.unidad_produccion or "pieza",
        "version": receta.version,
        "rendimiento_base": _decimal_text(receta.rendimiento_base),
        "activa": bool(receta.activa),
        "fecha_creacion": _fecha_texto(receta.fecha_creacion),
        "detalles": detalles,
        "detalles_count": len(detalles),
        "puede_producir_base": puede_producir_base,
    }


def _receta_form_payload(receta: Receta) -> dict:
    return {
        "id_receta": receta.id_receta,
        "id_producto": receta.id_producto,
        "version": receta.version,
        "nombre": receta.nombre,
        "descripcion": receta.descripcion or "",
        "categoria": receta.categoria or "",
        "unidad_produccion": receta.unidad_produccion or "pieza",
        "rendimiento_base": _decimal_text(receta.rendimiento_base),
        "activa": bool(receta.activa),
        "fecha_creacion": _fecha_texto(receta.fecha_creacion),
        "detalles": [
            {
                "id_materia_prima": detalle.id_materia_prima,
                "cantidad_base": _decimal_text(detalle.cantidad_base),
            }
            for detalle in sorted(receta.detalles, key=lambda item: item.id_detalle)
        ],
    }


def _receta_historial_payload(recetas: list[Receta]) -> list[dict]:
    historial: list[dict] = []
    for receta in sorted(recetas, key=lambda item: item.version, reverse=True):
        historial.append(
            {
                "id_receta": receta.id_receta,
                "version": receta.version,
                "activa": bool(receta.activa),
                "rendimiento_base": _decimal_text(receta.rendimiento_base),
                "fecha_creacion": _fecha_texto(receta.fecha_creacion),
                "detalles_count": len(receta.detalles),
            }
        )
    return historial


def _decimal_value(value: Decimal | int | float | None) -> Decimal:
    return Decimal(str(value or 0))


def _serializar_explosion_orden(
    *,
    receta: Receta,
    cantidad_producir: int,
) -> tuple[list[dict], bool, Decimal]:
    cantidad = _decimal_value(cantidad_producir)
    rendimiento = _decimal_value(receta.rendimiento_base)
    if cantidad <= 0 or rendimiento <= 0:
        return [], False, Decimal("0")

    factor = cantidad / rendimiento
    filas: list[dict] = []
    puede_producir = True
    costo_total = Decimal("0")
    for detalle in sorted(receta.detalles, key=lambda item: item.id_detalle):
        materia = detalle.materia_prima
        if not materia:
            continue
        unidad = materia.unidad_base.abreviatura if materia.unidad_base else "u"
        cantidad_receta = _decimal_value(detalle.cantidad_base)
        cantidad_necesaria = cantidad_receta * factor
        merma_pct = _decimal_value(materia.porcentaje_merma)
        cantidad_real = cantidad_necesaria * (
            Decimal("1") + (merma_pct / Decimal("100"))
        )
        stock_actual = _decimal_value(materia.cantidad_disponible)
        costo_unitario = _decimal_value(materia.costo_unitario)
        suficiente = stock_actual >= cantidad_real
        puede_producir = puede_producir and suficiente
        costo_total += cantidad_real * costo_unitario
        filas.append(
            {
                "id_materia_prima": materia.id_materia,
                "materia_nombre": materia.nombre,
                "unidad": unidad,
                "cantidad_receta": float(cantidad_receta),
                "cantidad_necesaria": float(cantidad_necesaria),
                "merma_pct": float(merma_pct),
                "cantidad_real": float(cantidad_real),
                "stock_actual": float(stock_actual),
                "suficiente": suficiente,
            }
        )

    return filas, puede_producir, costo_total


def _serializar_orden_produccion(orden: OrdenProduccion) -> dict:
    detalles: list[dict] = []
    costo_estimado = Decimal(str(orden.costo_total or 0))
    tipo_detalle = "CONSUMIDO"
    puede_producir = True

    if orden.detalles_consumo:
        for detalle in sorted(orden.detalles_consumo, key=lambda item: item.id_detalle):
            materia = detalle.materia_prima
            unidad = "u"
            stock_actual = Decimal("0")
            materia_nombre = "Materia prima"
            if materia:
                unidad = materia.unidad_base.abreviatura if materia.unidad_base else "u"
                stock_actual = _decimal_value(materia.cantidad_disponible)
                materia_nombre = materia.nombre

            cantidad_real = _decimal_value(detalle.cantidad_real_descontada)
            suficiente = stock_actual >= cantidad_real
            puede_producir = puede_producir and suficiente
            detalles.append(
                {
                    "id_materia_prima": detalle.id_materia_prima,
                    "materia_nombre": materia_nombre,
                    "unidad": unidad,
                    "cantidad_receta": float(_decimal_value(detalle.cantidad_receta)),
                    "cantidad_necesaria": float(
                        _decimal_value(detalle.cantidad_necesaria)
                    ),
                    "merma_pct": float(_decimal_value(detalle.porcentaje_merma)),
                    "cantidad_real": float(cantidad_real),
                    "stock_actual": float(stock_actual),
                    "suficiente": suficiente,
                }
            )
    else:
        tipo_detalle = "ESTIMADO"
        if orden.receta:
            detalles, puede_producir, costo_estimado = _serializar_explosion_orden(
                receta=orden.receta,
                cantidad_producir=int(orden.cantidad_producir or 0),
            )
        else:
            puede_producir = False

    return {
        "id_orden": orden.id_orden,
        "folio": f"ORD-{orden.id_orden:03d}",
        "id_producto": orden.id_producto,
        "producto_nombre": orden.producto.nombre if orden.producto else "Producto",
        "id_receta": orden.id_receta,
        "receta_version": orden.receta.version if orden.receta else 0,
        "cantidad_producir": int(orden.cantidad_producir or 0),
        "estado": orden.estado,
        "costo_total": float(_decimal_value(orden.costo_total)),
        "costo_estimado": float(costo_estimado),
        "fecha_inicio": orden.fecha_inicio.isoformat() if orden.fecha_inicio else "",
        "fecha_fin": orden.fecha_fin.isoformat() if orden.fecha_fin else "",
        "fecha_inicio_texto": _fecha_texto(orden.fecha_inicio),
        "fecha_fin_texto": _fecha_texto(orden.fecha_fin),
        "observaciones": orden.observaciones or "",
        "puede_producir": puede_producir,
        "tipo_detalle": tipo_detalle,
        "detalles": detalles,
    }


def _serializar_receta_activa_para_orden(receta: Receta) -> dict:
    detalles: list[dict] = []
    for detalle in sorted(receta.detalles, key=lambda item: item.id_detalle):
        materia = detalle.materia_prima
        if not materia:
            continue
        unidad = materia.unidad_base.abreviatura if materia.unidad_base else "u"
        detalles.append(
            {
                "id_materia_prima": materia.id_materia,
                "materia_nombre": materia.nombre,
                "unidad": unidad,
                "cantidad_receta": float(_decimal_value(detalle.cantidad_base)),
                "merma_pct": float(_decimal_value(materia.porcentaje_merma)),
                "stock_actual": float(_decimal_value(materia.cantidad_disponible)),
                "costo_unitario": float(_decimal_value(materia.costo_unitario)),
            }
        )

    return {
        "id_receta": receta.id_receta,
        "id_producto": receta.id_producto,
        "nombre": receta.nombre,
        "version": receta.version,
        "rendimiento_base": float(_decimal_value(receta.rendimiento_base)),
        "unidad_produccion": receta.unidad_produccion or "pieza",
        "detalles": detalles,
    }


@production_bp.route("/inventario-mp", methods=["GET", "POST"])
@login_required
@require_permission("Inventario MP", "leer")
def inventario_mp():
    form_mp = MateriaPrimaForm()
    form_ajuste = AjusteInventarioForm(prefix="ajuste")
    unidades = UnidadMedida.query.order_by(UnidadMedida.nombre.asc()).all()
    form_mp.id_unidad_base.choices = [(u.id_unidad, f"{u.nombre} ({u.abreviatura})") for u in unidades]
    form_mp.id_unidad_compra.choices = [(u.id_unidad, f"{u.nombre} ({u.abreviatura})") for u in unidades]

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "crear" and form_mp.validate_on_submit():
            try:
                materia = crear_materia_prima(
                    nombre=form_mp.nombre.data.strip(),
                    id_unidad_base=form_mp.id_unidad_base.data,
                    id_unidad_compra=form_mp.id_unidad_compra.data,
                    factor_conversion=form_mp.factor_conversion.data,
                    porcentaje_merma=form_mp.porcentaje_merma.data,
                    stock_minimo=form_mp.stock_minimo.data,
                    cantidad_inicial=form_mp.cantidad_inicial.data,
                    id_usuario=current_user.id_usuario,
                )
                log_audit_event(
                    "INVENTARIO_MP_CREAR",
                    f"id_materia={materia.id_materia}; nombre={materia.nombre}",
                )
                flash("Materia prima registrada correctamente.", "success")
            except ValueError as exc:
                flash(str(exc), "danger")
            return redirect(url_for("production.inventario_mp"))

        if action == "editar" and form_mp.validate_on_submit():
            id_materia = _int(request.form.get("id_materia", "0"))
            try:
                materia = actualizar_materia_prima(
                    id_materia=id_materia,
                    nombre=form_mp.nombre.data.strip(),
                    id_unidad_base=form_mp.id_unidad_base.data,
                    id_unidad_compra=form_mp.id_unidad_compra.data,
                    factor_conversion=form_mp.factor_conversion.data,
                    porcentaje_merma=form_mp.porcentaje_merma.data,
                    stock_minimo=form_mp.stock_minimo.data,
                )
                log_audit_event(
                    "INVENTARIO_MP_EDITAR",
                    f"id_materia={materia.id_materia}; nombre={materia.nombre}",
                )
                flash("Materia prima actualizada.", "success")
            except ValueError as exc:
                flash(str(exc), "danger")
            return redirect(url_for("production.inventario_mp"))

        if action == "desactivar":
            id_materia = _int(request.form.get("id_materia", "0"))
            try:
                materia = desactivar_materia_prima(id_materia=id_materia)
                log_audit_event(
                    "INVENTARIO_MP_DESACTIVAR",
                    f"id_materia={materia.id_materia}; nombre={materia.nombre}",
                )
                flash("Materia prima desactivada.", "success")
            except ValueError as exc:
                flash(str(exc), "danger")
            return redirect(url_for("production.inventario_mp"))

        if action == "ajuste":
            if form_ajuste.validate_on_submit():
                id_materia = form_ajuste.id_materia.data
                cantidad = Decimal(str(form_ajuste.cantidad.data))
                tipo = form_ajuste.tipo.data.upper()
                referencia_id = form_ajuste.referencia_id.data

                materia = MateriaPrima.query.get(id_materia)
                if not materia:
                    flash("Materia prima no encontrada.", "danger")
                    return redirect(url_for("production.inventario_mp"))

                dimension_base = (
                    materia.unidad_base.dimension if materia.unidad_base else "CONTEO"
                ).upper()
                
                if dimension_base == "CONTEO" and cantidad != cantidad.to_integral_value():
                    form_ajuste.cantidad.errors.append("Para materias primas por pieza, la cantidad debe ser entera.")
                else:
                    disponible = Decimal(str(materia.cantidad_disponible))
                    if dimension_base == "CONTEO":
                        disponible = disponible.to_integral_value(rounding=ROUND_HALF_UP)
                    
                    nueva_cantidad = disponible
                    if tipo == "ENTRADA":
                        nueva_cantidad = disponible + cantidad
                    elif tipo in {"SALIDA", "AJUSTE"}:
                        nueva_cantidad = disponible - cantidad

                    if dimension_base == "CONTEO":
                        nueva_cantidad = nueva_cantidad.to_integral_value(rounding=ROUND_HALF_UP)
                        cantidad = cantidad.to_integral_value(rounding=ROUND_HALF_UP)

                    if nueva_cantidad < 0:
                        form_ajuste.cantidad.errors.append("La cantidad descontada supera la existencia.")
                    else:
                        materia.cantidad_disponible = nueva_cantidad
                        db.session.add(
                            MovimientoInventarioMP(
                                id_materia_prima=materia.id_materia,
                                tipo=tipo,
                                cantidad=cantidad,
                                id_usuario=current_user.id_usuario,
                                referencia_id=referencia_id,
                            )
                        )
                        db.session.commit()
                        log_audit_event(
                            "INVENTARIO_MP_AJUSTE",
                            f"id_materia={materia.id_materia}; tipo={tipo}; cantidad={cantidad}; referencia={referencia_id}",
                        )
                        flash("Movimiento registrado.", "success")
                        return redirect(url_for("production.inventario_mp"))

            # Validacion o lógica de la acción falló, volver a renderizar y abrir el modal
            materias = MateriaPrima.query.order_by(MateriaPrima.nombre.asc()).all()
            unidades_meta = [{"id_unidad": u.id_unidad, "abreviatura": u.abreviatura, "dimension": (u.dimension or "CONTEO").upper(), "factor_base": float(u.factor_base)} for u in unidades]
            movimientos = MovimientoInventarioMP.query.order_by(MovimientoInventarioMP.id_movimiento.desc()).limit(15).all()
            resumen_stock = {"critico": 0, "bajo": 0, "ok": 0}
            alertas_stock = []
            for m in materias:
                if not m.activa: continue
                est = (m.estado_stock or "OK").upper()
                if est == "CRITICO":
                    resumen_stock["critico"] += 1
                    alertas_stock.append({"nombre": m.nombre, "cantidad": Decimal(str(m.cantidad_disponible)), "stock_minimo": Decimal(str(m.stock_minimo)), "unidad": m.unidad_base.abreviatura})
                elif est == "BAJO":
                    resumen_stock["bajo"] += 1
                else: resumen_stock["ok"] += 1

            return render_template(
                "production/inventario_mp.html",
                materias=materias,
                unidades=unidades,
                unidades_meta=unidades_meta,
                movimientos=movimientos,
                resumen_stock=resumen_stock,
                alertas_stock=alertas_stock,
                form_mp=form_mp,
                form_ajuste=form_ajuste,
                open_modal="modalInventarioAjuste" if action == "ajuste" else "modalMateriaPrima",
                edit_materia_id=request.form.get("id_materia"),
                ajuste_materia_id=request.form.get(f"ajuste-id_materia"),
            )

    materias = MateriaPrima.query.order_by(MateriaPrima.nombre.asc()).all()
    unidades = UnidadMedida.query.order_by(UnidadMedida.nombre.asc()).all()
    movimientos = (
        MovimientoInventarioMP.query.order_by(
            MovimientoInventarioMP.id_movimiento.desc()
        )
        .limit(15)
        .all()
    )

    resumen_stock = {"critico": 0, "bajo": 0, "ok": 0}
    alertas_stock = []
    for materia in materias:
        if not materia.activa:
            continue

        estado = (materia.estado_stock or "OK").upper()
        if estado == "CRITICO":
            resumen_stock["critico"] += 1
            alertas_stock.append(
                {
                    "nombre": materia.nombre,
                    "cantidad": Decimal(str(materia.cantidad_disponible)),
                    "stock_minimo": Decimal(str(materia.stock_minimo)),
                    "unidad": materia.unidad_base.abreviatura,
                }
            )
            continue

        if estado == "BAJO":
            resumen_stock["bajo"] += 1
            continue

        resumen_stock["ok"] += 1

    return render_template(
        "production/inventario_mp.html",
        materias=materias,
        unidades=unidades,
        unidades_meta=[
            {
                "id_unidad": unidad.id_unidad,
                "abreviatura": unidad.abreviatura,
                "dimension": (unidad.dimension or "CONTEO").upper(),
                "factor_base": float(unidad.factor_base),
            }
            for unidad in unidades
        ],
        movimientos=movimientos,
        resumen_stock=resumen_stock,
        alertas_stock=alertas_stock,
        form_mp=form_mp,
        form_ajuste=form_ajuste,
    )


@production_bp.get("/api/materia-prima/<int:id_materia>")
@login_required
@require_permission("Inventario MP", "leer")
def api_materia_prima(id_materia: int):
    materia = MateriaPrima.query.get_or_404(id_materia)
    return jsonify(
        {
            "id_materia": materia.id_materia,
            "nombre": materia.nombre,
            "id_unidad_base": materia.id_unidad_base,
            "id_unidad_compra": materia.id_unidad_compra,
            "unidad_base": {
                "id_unidad": materia.unidad_base.id_unidad,
                "abreviatura": materia.unidad_base.abreviatura,
            },
            "unidad_compra": {
                "id_unidad": materia.unidad_compra.id_unidad,
                "abreviatura": materia.unidad_compra.abreviatura,
            },
            "factor_conversion": float(materia.factor_conversion),
            "porcentaje_merma": float(materia.porcentaje_merma),
            "stock_minimo": float(materia.stock_minimo),
            "cantidad_disponible": float(materia.cantidad_disponible),
            "activa": bool(materia.activa),
            "estado_stock": materia.estado_stock,
        }
    )


@production_bp.get("/api/materia-prima/<int:id_materia>/movimientos")
@login_required
@require_permission("Inventario MP", "leer")
def api_movimientos_materia_prima(id_materia: int):
    materia = MateriaPrima.query.get_or_404(id_materia)
    movimientos = (
        MovimientoInventarioMP.query.filter_by(id_materia_prima=id_materia)
        .order_by(MovimientoInventarioMP.id_movimiento.desc())
        .limit(100)
        .all()
    )
    data = [
        {
            "id_movimiento": mv.id_movimiento,
            "tipo": mv.tipo,
            "cantidad": float(mv.cantidad),
            "fecha_movimiento": mv.fecha.isoformat(),
            "referencia_id": mv.referencia_id or "-",
            "nombre": materia.nombre,
            "unidad": materia.unidad_base.abreviatura,
        }
        for mv in movimientos
    ]
    return jsonify({"data": data})


@production_bp.get("/api/movimientos-inventario")
@login_required
@require_permission("Inventario MP", "leer")
def api_movimientos_inventario():
    movimientos = (
        MovimientoInventarioMP.query.order_by(
            MovimientoInventarioMP.id_movimiento.desc()
        )
        .limit(150)
        .all()
    )
    data = [
        {
            "id_movimiento": mv.id_movimiento,
            "tipo": mv.tipo,
            "cantidad": float(mv.cantidad),
            "fecha_movimiento": mv.fecha.isoformat(),
            "referencia_id": mv.referencia_id or "-",
            "nombre": mv.materia_prima.nombre,
            "unidad": mv.materia_prima.unidad_base.abreviatura,
        }
        for mv in movimientos
    ]
    return jsonify({"data": data})


@production_bp.route("/recetas", methods=["GET", "POST"])
@login_required
@require_permission("Recetas", "leer")
def recetas():
    form_receta = RecetaBaseForm(prefix="receta")

    productos = Producto.query.order_by(Producto.nombre.asc()).all()
    form_receta.id_producto.choices = [(p.id_producto, p.nombre) for p in productos if p.activo]

    if request.method == "POST":
        action = (request.form.get("action") or "crear").strip().lower()

        modulo = Modulo.query.filter_by(nombre="Recetas").first()
        permiso = (
            Permiso.query.filter_by(
                id_rol=current_user.id_rol,
                id_modulo=modulo.id_modulo if modulo else None,
            ).first()
            if modulo
            else None
        )
        if action == "crear" and (not permiso or not permiso.escritura):
            abort(403)
        if action == "editar" and (not permiso or not permiso.actualizacion):
            abort(403)

        if form_receta.validate_on_submit():
            id_producto = form_receta.id_producto.data
            rendimiento = Decimal(str(form_receta.rendimiento_base.data))
            estado_receta = form_receta.estado.data
            activa = estado_receta != "INACTIVA"
            categoria = (form_receta.categoria.data or "").strip() or None
            descripcion = (form_receta.descripcion.data or "").strip() or None
            unidad_produccion = (form_receta.unidad_produccion.data or "").strip() or None
            
            id_receta_base = _int(request.form.get("id_receta_base", "0"))

            ids_materia = request.form.getlist("id_materia_prima[]")
            cantidades = request.form.getlist("cantidad_receta[]")
            detalles_normalizados, error_detalles = _normalizar_detalles_receta(ids_materia, cantidades)
            if error_detalles:
                flash(error_detalles, "warning")
            else:
                producto = Producto.query.get(id_producto)
                if not producto or not producto.activo:
                    flash("Debes seleccionar un producto activo para la receta.", "warning")
                elif action not in {"crear", "editar"}:
                    flash("Accion de receta no valida.", "warning")
                elif action == "editar" and id_receta_base <= 0:
                    flash("Debes seleccionar una receta base para editar.", "warning")
                else:
                    version_actual = Receta.query.filter_by(id_producto=producto.id_producto).order_by(Receta.version.desc()).first()
                    next_version = 1 if not version_actual else version_actual.version + 1

                    receta_base = None
                    if id_receta_base > 0:
                        receta_base = Receta.query.get(id_receta_base)
                        if not receta_base or receta_base.id_producto != producto.id_producto:
                            flash("La receta base seleccionada no corresponde al producto.", "warning")
                            return redirect(url_for("production.recetas"))

                    skip = False
                    if action == "editar" and receta_base:
                        firma_nueva = sorted((id_mat, _decimal_text(cant)) for id_mat, cant in detalles_normalizados)
                        firma_actual = _firma_detalles_receta(receta_base)
                        rendimiento_actual = Decimal(str(receta_base.rendimiento_base or 0))
                        if firma_nueva == firma_actual and rendimiento == rendimiento_actual:
                            flash("No se detectaron cambios significativos en ingredientes o rendimiento para crear una nueva version.", "warning")
                            skip = True
                    
                    if not skip:
                        receta = Receta()
                        receta.id_producto = producto.id_producto
                        receta.nombre = producto.nombre.strip()
                        receta.descripcion = descripcion
                        receta.unidad_produccion = unidad_produccion or producto.unidad_venta or "pieza"
                        receta.categoria = categoria
                        receta.version = next_version
                        receta.rendimiento_base = rendimiento
                        receta.activa = activa
                        db.session.add(receta)
                        db.session.flush()

                        materias_validas = True
                        for id_materia, cantidad in detalles_normalizados:
                            materia = MateriaPrima.query.get(id_materia)
                            if not materia or not materia.activa:
                                db.session.rollback()
                                flash("No se puede guardar la receta con materias primas inexistentes o inactivas.", "warning")
                                materias_validas = False
                                break

                            detalle = DetalleReceta()
                            detalle.id_receta = receta.id_receta
                            detalle.id_materia_prima = id_materia
                            detalle.cantidad_base = cantidad
                            db.session.add(detalle)

                        if materias_validas:
                            if action == "editar" and receta_base:
                                receta_base.activa = False

                            if activa:
                                producto.receta_activa_id = receta.id_receta

                            db.session.commit()
                            log_audit_event("RECETA_NUEVA_VERSION" if action == "editar" else "RECETA_CREAR", f"id_receta={receta.id_receta}; producto={producto.id_producto}; version={receta.version}")
                            flash(f"Receta version {receta.version} registrada correctamente para el producto {producto.nombre}.", "success")
                            return redirect(url_for("production.recetas"))

        # Si hay errores o la validación falló, llegar aquí para re-renderizar con el modal abierto
        materias_all = MateriaPrima.query.order_by(MateriaPrima.nombre.asc()).all()
        recetas_all = Receta.query.options(selectinload(Receta.detalles).selectinload(DetalleReceta.materia_prima)).order_by(Receta.nombre.asc(), Receta.version.desc()).all()
        return render_template(
            "production/recetas.html",
            recetas=_agrupar_recetas_por_producto(recetas_all),
            materias=materias_all,
            productos=productos,
            form_receta=form_receta,
            open_modal="modalReceta",
        )

    recetas_query = Receta.query.order_by(Receta.id_receta.desc()).all()
    recetas_por_producto: dict[int, list[Receta]] = {}
    recetas_data: list[dict] = []
    for receta in recetas_query:
        recetas_por_producto.setdefault(receta.id_producto, []).append(receta)
        recetas_data.append(_serializar_receta(receta))

    recetas_por_producto_payload = {
        id_producto: _receta_historial_payload(recetas_producto)
        for id_producto, recetas_producto in recetas_por_producto.items()
    }

    materias = (
        MateriaPrima.query.filter_by(activa=True)
        .order_by(MateriaPrima.nombre.asc())
        .all()
    )
    productos = (
        Producto.query.filter_by(activo=True).order_by(Producto.nombre.asc()).all()
    )
    productos_payload = []
    for producto in productos:
        historial = recetas_por_producto.get(producto.id_producto, [])
        receta_activa = next((item for item in historial if item.activa), None)
        siguiente_version = (
            1 if not historial else max(r.version for r in historial) + 1
        )
        productos_payload.append(
            {
                "id_producto": producto.id_producto,
                "nombre": producto.nombre,
                "precio_venta": _decimal_text(producto.precio_venta, precision=2),
                "unidad_venta": producto.unidad_venta,
                "cantidad_disponible": int(producto.cantidad_disponible or 0),
                "recetas_count": len(historial),
                "receta_activa_id": receta_activa.id_receta if receta_activa else None,
                "receta_activa_version": (
                    receta_activa.version if receta_activa else None
                ),
                "siguiente_version": siguiente_version,
                "tiene_receta": bool(historial),
            }
        )

    return render_template(
        "production/recetas.html",
        recetas=recetas_data,
        recetas_por_producto=recetas_por_producto_payload,
        materias=materias,
        productos=productos_payload,
        unidades=UnidadMedida.query.order_by(UnidadMedida.nombre.asc()).all(),
        form_receta=form_receta,
    )


@production_bp.post("/recetas/<int:id_receta>/toggle")
@login_required
@require_permission("Recetas", "editar")
def toggle_receta(id_receta: int):
    receta = Receta.query.get_or_404(id_receta)
    producto = Producto.query.get(receta.id_producto)
    if not producto:
        flash("No se encontro el producto asociado a la receta.", "warning")
        return redirect(url_for("production.recetas"))

    if receta.activa:
        receta.activa = False
        producto.id_receta = (
            Receta.query.filter(
                Receta.id_producto == producto.id_producto,
                Receta.id_receta != receta.id_receta,
                Receta.activa.is_(True),
            )
            .order_by(Receta.version.desc())
            .with_entities(Receta.id_receta)
            .scalar()
        )
    else:
        Receta.query.filter(
            Receta.id_producto == producto.id_producto,
            Receta.activa.is_(True),
            Receta.id_receta != receta.id_receta,
        ).update({"activa": False}, synchronize_session=False)
        receta.activa = True
        producto.id_receta = receta.id_receta

    if producto.id_receta:
        try:
            recalcular_costo_y_precio_sugerido_producto(
                id_producto=producto.id_producto
            )
        except ValueError:
            producto.costo_produccion_actual = Decimal("0")
            producto.precio_sugerido = None
            producto.fecha_costo_actualizado = None
    else:
        producto.costo_produccion_actual = Decimal("0")
        producto.precio_sugerido = None
        producto.fecha_costo_actualizado = None

    db.session.commit()
    log_audit_event(
        "RECETA_TOGGLE",
        (
            f"id_receta={receta.id_receta}; id_producto={producto.id_producto}; "
            f"nombre={receta.nombre}; activa={receta.activa}"
        ),
    )
    flash("Estado de receta actualizado.", "success")
    return redirect(url_for("production.recetas"))


@production_bp.route("/ordenes", methods=["GET", "POST"])
@login_required
def ordenes():
    role_name = current_user.rol.nombre if current_user.rol else ""
    if role_name not in {"Administrador", "Produccion", "Ventas"}:
        flash("No tienes acceso a este modulo.", "danger")
        return redirect(url_for("catalog.home"))

    form_orden = OrdenProduccionForm(prefix="orden")
    
    # Pre-populate choices for the form (could have done this on GET/POST generically)
    recetas_activas = Receta.query.filter_by(activa=True).order_by(Receta.id_producto.asc(), Receta.version.desc()).all()
    form_orden.id_producto.choices = [(r.producto.id_producto, r.producto.nombre) for r in recetas_activas if r.producto and r.producto.activo]
    form_orden.id_receta.choices = [(r.id_receta, f"{r.nombre} (v{r.version})") for r in recetas_activas]
    solicitudes_aprobadas = SolicitudProduccion.query.filter_by(estado="APROBADA").order_by(SolicitudProduccion.fecha_solicitud.desc()).all()
    form_orden.id_solicitud.choices = [(0, "Ninguna (Orden directa)")] + [(s.id_solicitud, f"SOL-{s.id_solicitud:03d} (x{s.cantidad})") for s in solicitudes_aprobadas]

    if request.method == "POST":
        if role_name == "Ventas":
            flash("El area de ventas solo puede consultar ordenes.", "warning")
            return redirect(url_for("production.ordenes"))

        action = request.form.get("action", "")
        if action == "crear":
            if form_orden.validate_on_submit():
                id_producto = form_orden.id_producto.data
                id_receta = form_orden.id_receta.data
                cantidad = form_orden.cantidad.data
                id_solicitud = form_orden.id_solicitud.data
                observaciones = form_orden.observaciones.data or ""
                
                if id_producto <= 0 or id_receta <= 0 or cantidad <= 0:
                    flash("Datos invalidos para crear la orden.", "warning")
                    return redirect(url_for("production.ordenes"))

                try:
                    orden = crear_orden_produccion(
                        id_receta=id_receta,
                        cantidad=cantidad,
                        id_usuario=current_user.id_usuario,
                        id_producto=id_producto,
                        id_solicitud=id_solicitud if id_solicitud > 0 else None,
                        observaciones=observaciones,
                    )
                    log_audit_event(
                        "ORDEN_PRODUCCION_CREADA",
                        (
                            f"id_orden={orden.id_orden}; id_producto={form_orden.id_producto.data}; "
                            f"id_receta={form_orden.id_receta.data}; cantidad={form_orden.cantidad.data}; "
                            f"id_solicitud={form_orden.id_solicitud.data if form_orden.id_solicitud.data > 0 else 'N/A'}"
                        ),
                    )
                    flash("Orden de produccion creada.", "success")
                    return redirect(url_for("production.ordenes"))
                except ValueError as exc:
                    flash(str(exc), "danger")
            
            # Formulario invalido, continua abajo para renderizar
            action = "crear_fallido"

        if action != "crear_fallido":
            id_orden = _int(request.form.get("id_orden", "0"))
            orden = OrdenProduccion.query.get(id_orden) if id_orden > 0 else None
            
            if orden:
                if action == "iniciar":
                    if orden.estado != "PENDIENTE":
                        flash("Solo se pueden iniciar ordenes pendientes.", "warning")
                        return redirect(url_for("production.ordenes"))
                    try:
                        iniciar_orden_produccion(
                            id_orden=orden.id_orden,
                            id_usuario=current_user.id_usuario,
                        )
                        log_audit_event(
                            "ORDEN_PRODUCCION_INICIADA",
                            f"id_orden={orden.id_orden}",
                        )
                        flash(
                            "Orden iniciada correctamente. Se descontaron insumos con merma.",
                            "success",
                        )
                    except ValueError as exc:
                        flash(str(exc), "danger")
                    return redirect(url_for("production.ordenes"))

                if action == "finalizar":
                    try:
                        orden_actualizada = finalizar_orden_produccion(
                            id_orden=orden.id_orden,
                            id_usuario=current_user.id_usuario,
                        )
                        log_audit_event(
                            "ORDEN_PRODUCCION_FINALIZADA",
                            f"id_orden={orden_actualizada.id_orden}; id_producto={orden_actualizada.id_producto}; cantidad={orden_actualizada.cantidad_producir}",
                        )
                        flash(
                            "Orden finalizada. Se ingreso producto terminado a inventario.",
                            "success",
                        )
                    except ValueError as exc:
                        flash(str(exc), "warning")
                    return redirect(url_for("production.ordenes"))

                if action == "cancelar":
                    try:
                        orden_cancelada = cancelar_orden_produccion(id_orden=orden.id_orden)
                        log_audit_event(
                            "ORDEN_PRODUCCION_CANCELADA",
                            f"id_orden={orden_cancelada.id_orden}",
                        )
                        flash("Orden cancelada.", "success")
                    except ValueError as exc:
                        flash(str(exc), "warning")
                    return redirect(url_for("production.ordenes"))

    data = OrdenProduccion.query.order_by(OrdenProduccion.id_orden.desc()).all()
    ordenes_payload = [_serializar_orden_produccion(orden) for orden in data]

    recetas_activas = (
        Receta.query.filter_by(activa=True)
        .order_by(Receta.id_producto.asc(), Receta.version.desc())
        .all()
    )
    recetas_payload = [
        _serializar_receta_activa_para_orden(receta) for receta in recetas_activas
    ]

    productos_con_receta_activa = []
    for receta in recetas_activas:
        if not receta.producto or not receta.producto.activo:
            continue
        productos_con_receta_activa.append(
            {
                "id_producto": receta.producto.id_producto,
                "nombre": receta.producto.nombre,
                "receta_activa_id": receta.id_receta,
                "receta_version": receta.version,
                "rendimiento_base": float(_decimal_value(receta.rendimiento_base)),
                "unidad_produccion": receta.unidad_produccion or "pieza",
            }
        )

    productos_unicos: dict[int, dict] = {
        producto["id_producto"]: producto for producto in productos_con_receta_activa
    }

    solicitudes_aprobadas = (
        SolicitudProduccion.query.filter_by(estado="APROBADA")
        .order_by(SolicitudProduccion.id_solicitud.desc())
        .all()
    )
    id_solicitud_preseleccionada = _int(request.args.get("id_solicitud", "0"), 0)

    can_manage = role_name in {"Administrador", "Produccion"}
    return render_template(
        "production/ordenes.html",
        ordenes=data,
        ordenes_payload=ordenes_payload,
        recetas=recetas_activas,
        recetas_payload=recetas_payload,
        productos_con_receta=list(productos_unicos.values()),
        solicitudes=solicitudes_aprobadas,
        id_solicitud_preseleccionada=id_solicitud_preseleccionada,
        can_manage=can_manage,
        is_readonly=role_name == "Ventas",
        form_orden=form_orden,
        open_modal="modalNuevaOrden" if request.method == "POST" and request.form.get("action") == "crear" and not form_orden.validate() else None,
    )


@production_bp.route("/solicitudes", methods=["GET", "POST"])
@login_required
@require_permission("Solicitudes", "leer")
def solicitudes():
    role_name = current_user.rol.nombre if current_user.rol else ""
    if role_name not in {"Administrador", "Produccion"}:
        flash("Solo el area de produccion puede gestionar solicitudes.", "danger")
        return redirect(url_for("catalog.home"))

    form_resolver = ResolverSolicitudForm(prefix="resolver")

    if request.method == "POST":
        if form_resolver.validate_on_submit():
            id_solicitud = form_resolver.id_solicitud.data
            estado = form_resolver.estado.data
            observaciones_resolucion = form_resolver.observaciones_resolucion.data or None
            
            solicitud = SolicitudProduccion.query.get_or_404(id_solicitud)
            if solicitud.estado != "PENDIENTE":
                flash("Solo se pueden resolver solicitudes pendientes.", "warning")
                return redirect(url_for("production.solicitudes"))

            solicitud.estado = estado
            solicitud.id_usuario_resuelve = current_user.id_usuario
            solicitud.fecha_resolucion = utc_now()
            solicitud.observaciones_resolucion = observaciones_resolucion
            
            db.session.commit()
            log_audit_event(
                "SOLICITUD_PRODUCCION_RESUELTA",
                f"id_solicitud={solicitud.id_solicitud}; estado={solicitud.estado}; id_usuario_resuelve={current_user.id_usuario}",
            )
            flash("Solicitud actualizada.", "success")
            return redirect(url_for("production.solicitudes"))

    estado_filtro = (request.args.get("estado") or "TODOS").strip().upper()
    busqueda = (request.args.get("q") or "").strip()

    solicitudes_query = SolicitudProduccion.query.options(
        selectinload(SolicitudProduccion.producto),
        selectinload(SolicitudProduccion.pedido),
        selectinload(SolicitudProduccion.usuario_solicita),
        selectinload(SolicitudProduccion.usuario_resuelve),
        selectinload(SolicitudProduccion.ordenes),
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

    data = solicitudes_query.order_by(SolicitudProduccion.id_solicitud.desc()).all()
    recetas_activas = (
        Receta.query.filter_by(activa=True)
        .order_by(Receta.id_producto.asc(), Receta.version.desc())
        .all()
    )
    recetas_payload = [
        _serializar_receta_activa_para_orden(receta) for receta in recetas_activas
    ]
    return render_template(
        "production/solicitudes.html",
        solicitudes=data,
        estado_filtro=estado_filtro,
        busqueda=busqueda,
        recetas_payload=recetas_payload,
        role_name=role_name,
        form_resolver=form_resolver,
        open_modal="modalResolver" if request.method == "POST" and not form_resolver.validate() else None,
    )
