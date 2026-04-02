from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.common.security import log_audit_event, require_permission
from app.common.services import (
    cancelar_orden_produccion,
    crear_orden_produccion,
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
        if action == "ajuste":
            id_materia = _int(request.form.get("id_materia", "0"))
            cantidad = _decimal(request.form.get("cantidad", "0"))
            tipo = request.form.get("tipo", "AJUSTE").strip().upper()
            if tipo not in {"ENTRADA", "SALIDA", "AJUSTE"}:
                flash("Tipo de movimiento invalido.", "danger")
                return redirect(url_for("production.inventario_mp"))

            if tipo == "ENTRADA":
                flash(
                    "Las entradas de materia prima solo pueden registrarse desde compras.",
                    "danger",
                )
                return redirect(url_for("production.inventario_mp"))

            if cantidad <= 0:
                flash("La cantidad del movimiento debe ser mayor a cero.", "danger")
                return redirect(url_for("production.inventario_mp"))

            materia = MateriaPrima.query.get(id_materia)
            if not materia:
                flash("Materia prima no encontrada.", "danger")
                return redirect(url_for("production.inventario_mp"))

            disponible = Decimal(str(materia.cantidad_disponible))
            nueva_cantidad = disponible
            if tipo in {"SALIDA", "AJUSTE"}:
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
                    referencia_id="AJUSTE-MANUAL",
                )
            )
            db.session.commit()
            log_audit_event(
                "INVENTARIO_MP_AJUSTE",
                f"id_materia={materia.id_materia}; tipo={tipo}; cantidad={cantidad}",
            )
            flash("Movimiento registrado.", "success")
            return redirect(url_for("production.inventario_mp"))

    materias = MateriaPrima.query.order_by(MateriaPrima.nombre.asc()).all()
    movimientos = (
        MovimientoInventarioMP.query.order_by(
            MovimientoInventarioMP.id_movimiento.desc()
        )
        .limit(15)
        .all()
    )
    return render_template(
        "production/inventario_mp.html",
        materias=materias,
        movimientos=movimientos,
    )


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
                    id_usuario=current_user.id_usuario,
                )
                log_audit_event(
                    "ORDEN_PRODUCCION_INICIADA",
                    f"id_orden={orden.id_orden}",
                )
                flash(
                    "Orden iniciada. Insumos descontados y costo calculado.", "success"
                )
            except ValueError as exc:
                flash(str(exc), "danger")
            return redirect(url_for("production.ordenes"))

        if action == "finalizar":
            try:
                orden_actualizada = finalizar_orden_produccion(id_orden=orden.id_orden)
                log_audit_event(
                    "ORDEN_PRODUCCION_FINALIZADA",
                    f"id_orden={orden_actualizada.id_orden}; id_producto={orden_actualizada.id_producto}; cantidad={orden_actualizada.cantidad_producir}",
                )
                flash("Orden finalizada y stock actualizado.", "success")
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
