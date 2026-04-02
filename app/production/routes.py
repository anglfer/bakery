from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.common.security import log_audit_event, require_permission
from app.common.services import (
    actualizar_materia_prima,
    cancelar_orden_produccion,
    crear_materia_prima,
    crear_orden_produccion,
    desactivar_materia_prima,
    finalizar_orden_produccion,
    iniciar_orden_produccion,
)
from app.extensions import db
from app.models import (
    DetalleReceta,
    MateriaPrima,
    MovimientoInventarioMP,
    OrdenProduccion,
    Producto,
    Receta,
    SolicitudProduccion,
    UnidadMedida,
)
from app.production import production_bp


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


@production_bp.route("/inventario-mp", methods=["GET", "POST"])
@login_required
@require_permission("Inventario MP", "leer")
def inventario_mp():
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "crear":
            try:
                materia = crear_materia_prima(
                    nombre=request.form.get("nombre", ""),
                    id_unidad_base=_int(request.form.get("id_unidad_base", "0")),
                    id_unidad_compra=_int(request.form.get("id_unidad_compra", "0")),
                    factor_conversion=_decimal(
                        request.form.get("factor_conversion", "0")
                    ),
                    porcentaje_merma=_decimal(
                        request.form.get("porcentaje_merma", "0")
                    ),
                    stock_minimo=_decimal(request.form.get("stock_minimo", "0")),
                    cantidad_inicial=_decimal(
                        request.form.get("cantidad_inicial", "0")
                    ),
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

        if action == "editar":
            id_materia = _int(request.form.get("id_materia", "0"))
            try:
                materia = actualizar_materia_prima(
                    id_materia=id_materia,
                    nombre=request.form.get("nombre", ""),
                    id_unidad_base=_int(request.form.get("id_unidad_base", "0")),
                    id_unidad_compra=_int(request.form.get("id_unidad_compra", "0")),
                    factor_conversion=_decimal(
                        request.form.get("factor_conversion", "0")
                    ),
                    porcentaje_merma=_decimal(
                        request.form.get("porcentaje_merma", "0")
                    ),
                    stock_minimo=_decimal(request.form.get("stock_minimo", "0")),
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
            id_materia = _int(request.form.get("id_materia", "0"))
            cantidad = _decimal(request.form.get("cantidad", "0"))
            tipo = request.form.get("tipo", "AJUSTE").strip().upper()
            referencia_id = (request.form.get("referencia_id") or "").strip()
            if tipo not in {"ENTRADA", "SALIDA", "AJUSTE"}:
                flash("Tipo de movimiento invalido.", "danger")
                return redirect(url_for("production.inventario_mp"))

            if cantidad <= 0:
                flash("La cantidad del movimiento debe ser mayor a cero.", "danger")
                return redirect(url_for("production.inventario_mp"))

            if not referencia_id:
                flash("Debes indicar una referencia o motivo del movimiento.", "danger")
                return redirect(url_for("production.inventario_mp"))

            materia = MateriaPrima.query.get(id_materia)
            if not materia:
                flash("Materia prima no encontrada.", "danger")
                return redirect(url_for("production.inventario_mp"))

            disponible = Decimal(str(materia.cantidad_disponible))
            nueva_cantidad = disponible
            if tipo == "ENTRADA":
                nueva_cantidad = disponible + cantidad
            elif tipo in {"SALIDA", "AJUSTE"}:
                nueva_cantidad = disponible - cantidad

            if nueva_cantidad < 0:
                flash("La cantidad no puede ser negativa.", "danger")
                return redirect(url_for("production.inventario_mp"))

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
    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        id_producto = _int(request.form.get("id_producto", "0"))
        rendimiento = _decimal(request.form.get("rendimiento", "0"))
        unidad_produccion = (request.form.get("unidad_produccion") or "pieza").strip()
        categoria = (request.form.get("categoria") or "").strip() or None
        descripcion = (request.form.get("descripcion") or "").strip() or None

        ids_materia = request.form.getlist("id_materia_prima[]")
        cantidades = request.form.getlist("cantidad_receta[]")

        if not nombre or rendimiento <= 0:
            flash("Nombre y rendimiento base son obligatorios.", "warning")
            return redirect(url_for("production.recetas"))

        producto = Producto.query.get(id_producto)
        if not producto or not producto.activo:
            flash("Debes seleccionar un producto activo para la receta.", "warning")
            return redirect(url_for("production.recetas"))

        if nombre.strip().lower() != producto.nombre.strip().lower():
            flash(
                "El nombre de la receta debe coincidir con el producto seleccionado.",
                "warning",
            )
            return redirect(url_for("production.recetas"))

        if not ids_materia or not cantidades or len(ids_materia) != len(cantidades):
            flash("Debes registrar al menos un ingrediente valido.", "warning")
            return redirect(url_for("production.recetas"))

        version_actual = (
            Receta.query.filter(db.func.lower(Receta.nombre) == nombre.lower())
            .order_by(Receta.version.desc())
            .first()
        )
        next_version = 1 if not version_actual else version_actual.version + 1

        receta = Receta(
            nombre=nombre,
            descripcion=descripcion,
            unidad_produccion=unidad_produccion or "pieza",
            categoria=categoria,
            version=next_version,
            rendimiento_base=rendimiento,
            activa=True,
        )
        db.session.add(receta)
        db.session.flush()

        detalles_validos = 0
        for id_materia_raw, cantidad_raw in zip(ids_materia, cantidades):
            id_materia = _int(id_materia_raw, 0)
            cantidad = _decimal(cantidad_raw, "0")
            if id_materia <= 0 or cantidad <= 0:
                continue

            materia = MateriaPrima.query.get(id_materia)
            if not materia or not materia.activa:
                db.session.rollback()
                flash(
                    "No se puede crear la receta con materias primas inexistentes o inactivas.",
                    "warning",
                )
                return redirect(url_for("production.recetas"))

            db.session.add(
                DetalleReceta(
                    id_receta=receta.id_receta,
                    id_materia_prima=id_materia,
                    cantidad_base=cantidad,
                )
            )
            detalles_validos += 1

        receta_ids_previas = [producto.id_receta] if producto.id_receta else []
        if receta_ids_previas:
            Receta.query.filter(Receta.id_receta.in_(receta_ids_previas)).update(
                {"activa": False},
                synchronize_session=False,
            )

        if detalles_validos == 0:
            db.session.rollback()
            flash(
                "Debes registrar al menos un ingrediente " "con cantidad mayor a cero.",
                "warning",
            )
            return redirect(url_for("production.recetas"))

        producto.id_receta = receta.id_receta
        db.session.commit()
        log_audit_event(
            "RECETA_CREADA",
            f"id_receta={receta.id_receta}; id_producto={producto.id_producto}; nombre={receta.nombre}; version={receta.version}",
        )
        flash("Receta creada con ingredientes y nueva version.", "success")
        return redirect(url_for("production.recetas"))

    data = Receta.query.order_by(Receta.id_receta.desc()).all()
    materias = (
        MateriaPrima.query.filter_by(activa=True)
        .order_by(MateriaPrima.nombre.asc())
        .all()
    )
    return render_template(
        "production/recetas.html",
        recetas=data,
        materias=materias,
        productos=Producto.query.filter_by(activo=True)
        .order_by(Producto.nombre.asc())
        .all(),
        unidades=UnidadMedida.query.order_by(UnidadMedida.nombre.asc()).all(),
    )


@production_bp.post("/recetas/<int:id_receta>/toggle")
@login_required
@require_permission("Recetas", "editar")
def toggle_receta(id_receta: int):
    receta = Receta.query.get_or_404(id_receta)
    if not receta.activa:
        Receta.query.filter(
            db.func.lower(Receta.nombre) == receta.nombre.lower(),
            Receta.activa.is_(True),
        ).update({"activa": False}, synchronize_session=False)
    receta.activa = not receta.activa
    db.session.commit()
    log_audit_event(
        "RECETA_TOGGLE",
        f"id_receta={receta.id_receta}; nombre={receta.nombre}; activa={receta.activa}",
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

    if request.method == "POST":
        if role_name == "Ventas":
            flash("El area de ventas solo puede consultar ordenes.", "warning")
            return redirect(url_for("production.ordenes"))

        action = request.form.get("action", "")
        if action == "crear":
            id_solicitud = _int(request.form.get("id_solicitud", "0"))
            id_receta = _int(request.form.get("id_receta", "0"))
            cantidad = _int(request.form.get("cantidad", "0"))
            if id_solicitud <= 0 or id_receta <= 0 or cantidad <= 0:
                flash("Datos invalidos para crear la orden.", "warning")
                return redirect(url_for("production.ordenes"))

            try:
                orden = crear_orden_produccion(
                    id_solicitud=id_solicitud,
                    id_receta=id_receta,
                    cantidad=cantidad,
                    id_usuario=current_user.id_usuario,
                )
                log_audit_event(
                    "ORDEN_PRODUCCION_CREADA",
                    f"id_orden={orden.id_orden}; id_solicitud={id_solicitud}; id_receta={id_receta}; cantidad={cantidad}",
                )
                flash("Orden de produccion creada.", "success")
            except ValueError as exc:
                flash(str(exc), "danger")
            return redirect(url_for("production.ordenes"))

        id_orden = _int(request.form.get("id_orden", "0"))
        orden = OrdenProduccion.query.get_or_404(id_orden)

        if action == "iniciar":
            if orden.estado != "PENDIENTE":
                flash("Solo se pueden iniciar ordenes pendientes.", "warning")
                return redirect(url_for("production.ordenes"))
            try:
                iniciar_orden_produccion(
                    id_orden=orden.id_orden,
                )
                log_audit_event(
                    "ORDEN_PRODUCCION_INICIADA",
                    f"id_orden={orden.id_orden}",
                )
                flash("Orden iniciada correctamente.", "success")
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
                    "Orden finalizada. Se descontaron insumos y se actualizo el stock de producto.",
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
    recetas_activas = (
        Receta.query.filter_by(activa=True).order_by(Receta.id_receta.desc()).all()
    )
    solicitudes_aprobadas = (
        SolicitudProduccion.query.filter_by(estado="APROBADA")
        .order_by(SolicitudProduccion.id_solicitud.desc())
        .all()
    )
    return render_template(
        "production/ordenes.html",
        ordenes=data,
        recetas=recetas_activas,
        solicitudes=solicitudes_aprobadas,
    )


@production_bp.route("/solicitudes", methods=["GET", "POST"])
@login_required
@require_permission("Solicitudes", "leer")
def solicitudes():
    role_name = current_user.rol.nombre if current_user.rol else ""
    if role_name not in {"Administrador", "Produccion"}:
        flash("Solo el area de produccion puede gestionar solicitudes.", "danger")
        return redirect(url_for("catalog.home"))

    if request.method == "POST":
        id_solicitud = _int(request.form.get("id_solicitud", "0"))
        estado = request.form.get("estado", "PENDIENTE").strip().upper()
        solicitud = SolicitudProduccion.query.get_or_404(id_solicitud)
        if solicitud.estado != "PENDIENTE":
            flash("Solo se pueden resolver solicitudes pendientes.", "warning")
            return redirect(url_for("production.solicitudes"))

        if estado not in {"APROBADA", "RECHAZADA"}:
            flash("Estado de resolucion invalido.", "danger")
            return redirect(url_for("production.solicitudes"))

        solicitud.estado = estado
        solicitud.id_usuario_resuelve = current_user.id_usuario
        solicitud.observaciones = request.form.get("observaciones", "").strip()
        db.session.commit()
        log_audit_event(
            "SOLICITUD_PRODUCCION_RESUELTA",
            f"id_solicitud={solicitud.id_solicitud}; estado={solicitud.estado}; id_usuario_resuelve={current_user.id_usuario}",
        )
        flash("Solicitud actualizada.", "success")
        return redirect(url_for("production.solicitudes"))

    data = SolicitudProduccion.query.order_by(
        SolicitudProduccion.id_solicitud.desc()
    ).all()
    return render_template("production/solicitudes.html", solicitudes=data)
