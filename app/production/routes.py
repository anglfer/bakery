from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.common.security import require_permission
from app.common.services import (
    crear_orden_produccion,
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
    utc_now,
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
            materia = MateriaPrima.query.get(id_materia)
            if not materia:
                flash("Materia prima no encontrada.", "danger")
                return redirect(url_for("production.inventario_mp"))

            disponible = Decimal(str(materia.cantidad_disponible))
            nueva_cantidad = disponible + cantidad
            if tipo == "SALIDA":
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
        rendimiento = _decimal(request.form.get("rendimiento", "0"))
        unidad_produccion = (request.form.get("unidad_produccion") or "pieza").strip()
        categoria = (request.form.get("categoria") or "").strip() or None
        descripcion = (request.form.get("descripcion") or "").strip() or None

        ids_materia = request.form.getlist("id_materia_prima[]")
        cantidades = request.form.getlist("cantidad_receta[]")

        if not nombre or rendimiento <= 0:
            flash("Nombre y rendimiento base son obligatorios.", "warning")
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

        Receta.query.filter(
            db.func.lower(Receta.nombre) == nombre.lower(),
            Receta.activa.is_(True),
        ).update({"activa": False}, synchronize_session=False)

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

            db.session.add(
                DetalleReceta(
                    id_receta=receta.id_receta,
                    id_materia_prima=id_materia,
                    cantidad_base=cantidad,
                )
            )
            detalles_validos += 1

        if detalles_validos == 0:
            db.session.rollback()
            flash(
                "Debes registrar al menos un ingrediente " "con cantidad mayor a cero.",
                "warning",
            )
            return redirect(url_for("production.recetas"))

        db.session.commit()
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
    flash("Estado de receta actualizado.", "success")
    return redirect(url_for("production.recetas"))


@production_bp.route("/ordenes", methods=["GET", "POST"])
@login_required
@require_permission("Ordenes", "leer")
def ordenes():
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "crear":
            id_solicitud = _int(request.form.get("id_solicitud", "0"))
            id_receta = _int(request.form.get("id_receta", "0"))
            cantidad = _int(request.form.get("cantidad", "0"))
            if id_solicitud <= 0 or id_receta <= 0 or cantidad <= 0:
                flash("Datos invalidos para crear la orden.", "warning")
                return redirect(url_for("production.ordenes"))

            try:
                crear_orden_produccion(
                    id_solicitud=id_solicitud,
                    id_receta=id_receta,
                    cantidad=cantidad,
                    id_usuario=current_user.id_usuario,
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
                flash(
                    "Orden iniciada. Insumos descontados y costo calculado.", "success"
                )
            except ValueError as exc:
                flash(str(exc), "danger")
            return redirect(url_for("production.ordenes"))

        if action == "finalizar":
            if orden.estado != "EN_PROCESO":
                flash("Solo se pueden finalizar ordenes en proceso.", "warning")
                return redirect(url_for("production.ordenes"))
            orden.estado = "FINALIZADO"
            orden.fecha_fin = utc_now()
            producto = Producto.query.get(orden.id_producto)
            producto.cantidad_disponible += orden.cantidad_producir
            db.session.commit()
            flash("Orden finalizada y stock actualizado.", "success")
            return redirect(url_for("production.ordenes"))

        if action == "cancelar":
            if orden.estado != "PENDIENTE":
                flash("Solo se pueden cancelar ordenes pendientes.", "warning")
                return redirect(url_for("production.ordenes"))
            orden.estado = "CANCELADO"
            db.session.commit()
            flash("Orden cancelada.", "success")
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
        flash("Solicitud actualizada.", "success")
        return redirect(url_for("production.solicitudes"))

    data = SolicitudProduccion.query.order_by(
        SolicitudProduccion.id_solicitud.desc()
    ).all()
    return render_template("production/solicitudes.html", solicitudes=data)
