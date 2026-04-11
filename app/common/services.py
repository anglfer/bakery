from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from flask import current_app

from app.extensions import db
from app.models import (
    Carrito,
    Compra,
    DetalleCarrito,
    DetalleCompra,
    DetalleOrdenProduccion,
    DetallePedido,
    DetalleReceta,
    DetalleVenta,
    MateriaPrima,
    MovimientoInventarioMP,
    MovimientoInventarioProducto,
    OrdenProduccion,
    PagoPedido,
    Pedido,
    PedidoEstadoHistorial,
    Producto,
    Receta,
    SalidaEfectivo,
    SolicitudProduccion,
    TicketVenta,
    UnidadMedida,
    Venta,
    utc_now,
)

TRANSICIONES_PEDIDO = {
    "PENDIENTE": {"CONFIRMADO", "CANCELADO"},
    "CONFIRMADO": {"PAGADO"},
    "PAGADO": set(),
    "ENTREGADO": set(),
    "CANCELADO": set(),
}

TRANSICIONES_ORDEN = {
    "PENDIENTE": {"EN_PROCESO", "CANCELADO"},
    "EN_PROCESO": {"FINALIZADO"},
    "FINALIZADO": set(),
    "CANCELADO": set(),
}

MXN_QUANTIZE = Decimal("0.01")
UNIT_COST_QUANTIZE = Decimal("0.0001")


def _to_mxn(value: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(MXN_QUANTIZE, rounding=ROUND_HALF_UP)


def _to_unit_cost(value: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(UNIT_COST_QUANTIZE, rounding=ROUND_HALF_UP)


def _is_integer_decimal(value: Decimal) -> bool:
    decimal_value = Decimal(str(value))
    return decimal_value == decimal_value.to_integral_value()


def _as_integer_decimal(value: Decimal) -> Decimal:
    return Decimal(str(value)).to_integral_value()


def _nombre_negocio_ticket() -> str:
    nombre = current_app.config.get("BUSINESS_NAME", "SoftBakery")
    return str(nombre).strip() or "SoftBakery"


def _crear_ticket_venta(id_venta: int) -> None:
    folio = f"SB-{id_venta:06d}"
    db.session.add(
        TicketVenta(
            id_venta=id_venta,
            folio=folio,
            nombre_negocio=_nombre_negocio_ticket(),
        )
    )


def _registrar_movimiento_producto(
    *,
    producto: Producto,
    tipo: str,
    cantidad: int,
    stock_anterior: int,
    stock_posterior: int,
    id_usuario: int,
    referencia_id: str | None = None,
) -> None:
    movimiento = MovimientoInventarioProducto()
    movimiento.id_producto = producto.id_producto
    movimiento.tipo = tipo
    movimiento.cantidad = cantidad
    movimiento.stock_anterior = stock_anterior
    movimiento.stock_posterior = stock_posterior
    movimiento.referencia_id = referencia_id
    movimiento.id_usuario = id_usuario
    db.session.add(movimiento)


def _validar_transicion_estado(
    *,
    estado_actual: str,
    estado_destino: str,
    mapa_transiciones: dict[str, set[str]],
    entidad: str,
) -> None:
    estados_permitidos = mapa_transiciones.get(estado_actual, set())
    if estado_destino not in estados_permitidos:
        raise ValueError(
            f"Transicion invalida para {entidad}: {estado_actual} -> {estado_destino}"
        )


def _obtener_producto_bloqueado(id_producto: int) -> Producto | None:
    return Producto.query.filter_by(id_producto=id_producto).with_for_update().first()


def _obtener_materia_bloqueada(id_materia: int) -> MateriaPrima | None:
    return MateriaPrima.query.filter_by(id_materia=id_materia).with_for_update().first()


def _inferir_factor_conversion_por_unidades(
    *, id_unidad_base: int, id_unidad_compra: int
) -> Decimal | None:
    unidad_base = UnidadMedida.query.get(id_unidad_base)
    unidad_compra = UnidadMedida.query.get(id_unidad_compra)
    if not unidad_base or not unidad_compra:
        return None

    dimension_base = (unidad_base.dimension or "CONTEO").upper()
    dimension_compra = (unidad_compra.dimension or "CONTEO").upper()
    if dimension_base != dimension_compra:
        return None

    factor_base = Decimal(str(unidad_base.factor_base or 0))
    factor_compra = Decimal(str(unidad_compra.factor_base or 0))
    if factor_base <= 0 or factor_compra <= 0:
        return None

    return factor_compra / factor_base


def _validar_reglas_conteo_materia(
    *,
    id_unidad_base: int,
    id_unidad_compra: int,
    factor_conversion: Decimal,
    stock_minimo: Decimal | None = None,
    cantidad_inicial: Decimal | None = None,
) -> tuple[UnidadMedida, UnidadMedida]:
    unidad_base = UnidadMedida.query.get(id_unidad_base)
    unidad_compra = UnidadMedida.query.get(id_unidad_compra)
    if not unidad_base or not unidad_compra:
        raise ValueError("Debes seleccionar unidades válidas")

    dimension_base = (unidad_base.dimension or "CONTEO").upper()
    dimension_compra = (unidad_compra.dimension or "CONTEO").upper()
    if dimension_base != dimension_compra:
        raise ValueError(
            "La unidad base y la unidad de compra deben compartir dimensión"
        )

    factor_decimal = Decimal(str(factor_conversion))
    if dimension_base == "CONTEO":
        if not _is_integer_decimal(factor_decimal):
            raise ValueError(
                "Para unidades de conteo (pza), el factor de conversión debe ser entero"
            )
        if stock_minimo is not None and not _is_integer_decimal(stock_minimo):
            raise ValueError(
                "Para unidades de conteo (pza), el stock mínimo debe ser entero"
            )
        if cantidad_inicial is not None and not _is_integer_decimal(cantidad_inicial):
            raise ValueError(
                "Para unidades de conteo (pza), la cantidad inicial debe ser entera"
            )

    return unidad_base, unidad_compra


def _obtener_pedido_bloqueado(id_pedido: int) -> Pedido | None:
    return Pedido.query.filter_by(id_pedido=id_pedido).with_for_update().first()


def _obtener_orden_bloqueada(id_orden: int) -> OrdenProduccion | None:
    return OrdenProduccion.query.filter_by(id_orden=id_orden).with_for_update().first()


def _cantidad_libre_producto(producto: Producto) -> int:
    disponible = int(producto.cantidad_disponible or 0)
    reservada = int(producto.cantidad_reservada or 0)
    return max(disponible - reservada, 0)


def _actualizar_costo_unitario_materia_prima_desde_compra(
    *,
    materia: MateriaPrima,
    cantidad_base_ingreso: Decimal,
    precio_unitario_compra: Decimal,
    factor_conversion: Decimal,
) -> None:
    if factor_conversion <= 0:
        raise ValueError(
            f"La materia prima '{materia.nombre}' tiene un factor de conversion invalido"
        )

    costo_unitario_nuevo = _to_unit_cost(precio_unitario_compra / factor_conversion)
    stock_actual = Decimal(str(materia.cantidad_disponible))
    costo_actual = Decimal(str(materia.costo_unitario))
    stock_resultante = stock_actual + cantidad_base_ingreso

    if stock_resultante <= 0:
        materia.costo_unitario = costo_unitario_nuevo
        return

    costo_ponderado = (
        (stock_actual * costo_actual) + (cantidad_base_ingreso * costo_unitario_nuevo)
    ) / stock_resultante
    materia.costo_unitario = _to_unit_cost(costo_ponderado)


def _validar_disponibilidad_materia_prima_para_orden(
    *, receta: Receta, cantidad_producir: int
) -> None:
    consumos = _obtener_consumo_materias_para_orden(
        receta=receta,
        cantidad_producir=cantidad_producir,
    )
    for consumo in consumos:
        if consumo["stock_previo"] < consumo["cantidad_real"]:
            raise ValueError(f"Stock insuficiente para {consumo['materia'].nombre}")


def _obtener_consumo_materias_para_orden(
    *,
    receta: Receta,
    cantidad_producir: int,
) -> list[dict]:
    if receta.rendimiento_base <= 0:
        raise ValueError("Rendimiento base invalido")

    factor = Decimal(str(cantidad_producir)) / Decimal(str(receta.rendimiento_base))
    consumos: list[dict] = []
    for detalle in DetalleReceta.query.filter_by(id_receta=receta.id_receta).all():
        materia = _obtener_materia_bloqueada(detalle.id_materia_prima)
        if not materia or not materia.activa:
            raise ValueError("Materia prima no disponible")

        cantidad_receta = Decimal(str(detalle.cantidad_base))
        cantidad_necesaria = cantidad_receta * factor
        porcentaje_merma = Decimal(str(materia.porcentaje_merma or 0))
        merma_factor = Decimal("1") + (porcentaje_merma / Decimal("100"))
        cantidad_real = cantidad_necesaria * merma_factor
        dimension_base = (materia.unidad_base.dimension or "CONTEO").upper()
        if dimension_base == "CONTEO":
            cantidad_necesaria = cantidad_necesaria.to_integral_value(
                rounding=ROUND_CEILING
            )
            cantidad_real = cantidad_real.to_integral_value(rounding=ROUND_CEILING)
        stock_previo = Decimal(str(materia.cantidad_disponible or 0))
        consumos.append(
            {
                "materia": materia,
                "cantidad_receta": cantidad_receta,
                "cantidad_necesaria": cantidad_necesaria,
                "porcentaje_merma": porcentaje_merma,
                "cantidad_real": cantidad_real,
                "stock_previo": stock_previo,
            }
        )

    return consumos


def _registrar_consumo_materias_en_orden(
    *,
    orden: OrdenProduccion,
    receta: Receta,
    id_usuario: int,
) -> Decimal:
    costo_total = Decimal("0")
    consumos = _obtener_consumo_materias_para_orden(
        receta=receta,
        cantidad_producir=orden.cantidad_producir,
    )

    for consumo in consumos:
        materia = consumo["materia"]
        cantidad_real = consumo["cantidad_real"]
        stock_previo = Decimal(str(materia.cantidad_disponible or 0))
        if stock_previo < cantidad_real:
            raise ValueError(f"Stock insuficiente para {materia.nombre}")

        stock_posterior = stock_previo - cantidad_real
        materia.cantidad_disponible = stock_posterior
        movimiento = MovimientoInventarioMP()
        movimiento.id_materia_prima = materia.id_materia
        movimiento.tipo = "SALIDA"
        movimiento.cantidad = cantidad_real
        movimiento.id_usuario = id_usuario
        movimiento.referencia_id = f"ORD-{orden.id_orden}"
        db.session.add(movimiento)
        detalle_consumo = DetalleOrdenProduccion()
        detalle_consumo.id_orden = orden.id_orden
        detalle_consumo.id_materia_prima = materia.id_materia
        detalle_consumo.cantidad_receta = consumo["cantidad_receta"]
        detalle_consumo.cantidad_necesaria = consumo["cantidad_necesaria"]
        detalle_consumo.porcentaje_merma = consumo["porcentaje_merma"]
        detalle_consumo.cantidad_real_descontada = cantidad_real
        detalle_consumo.stock_previo = stock_previo
        detalle_consumo.stock_posterior = stock_posterior
        db.session.add(detalle_consumo)
        costo_total += cantidad_real * Decimal(str(materia.costo_unitario or 0))

    return _to_mxn(costo_total)


def _validar_parametros_checkout(
    *,
    tipo_pago_pedido: str,
    tipo_pago: str,
    referencia_pago: str | None,
) -> None:
    if tipo_pago_pedido not in {"EN_LINEA", "CONTRA_ENTREGA"}:
        raise ValueError("Tipo de pago invalido")
    if tipo_pago not in {"EFECTIVO", "TARJETA"}:
        raise ValueError("Tipo de pago invalido")
    if tipo_pago_pedido == "EN_LINEA" and tipo_pago != "TARJETA":
        raise ValueError("Los pedidos en linea solo permiten pago con tarjeta")
    if tipo_pago_pedido == "EN_LINEA" and not referencia_pago:
        raise ValueError("La referencia de pago es obligatoria para pagos en linea")


def _validar_fecha_entrega_programada(fecha_entrega: date) -> None:
    fecha_minima = date.today() + timedelta(days=3)
    if fecha_entrega < fecha_minima:
        raise ValueError("La fecha de entrega debe ser al menos 3 días posterior a hoy")


def _validar_pedido_producido_para_entrega(pedido: Pedido) -> None:
    solicitudes_ligadas = SolicitudProduccion.query.filter_by(
        id_pedido=pedido.id_pedido
    ).all()
    if not solicitudes_ligadas:
        return

    for solicitud in solicitudes_ligadas:
        if solicitud.estado == "PENDIENTE":
            raise ValueError("El pedido aún tiene solicitudes de producción sin cerrar")

        if solicitud.estado == "RECHAZADA":
            raise ValueError(
                "No se puede entregar: existe solicitud de producción rechazada"
            )

        if solicitud.estado != "APROBADA":
            raise ValueError(
                "No se puede entregar: existe solicitud de producción con estado inválido"
            )

        ordenes_vigentes = [
            orden for orden in solicitud.ordenes if orden.estado != "CANCELADO"
        ]
        if not ordenes_vigentes:
            raise ValueError(
                "No se puede entregar: falta la orden de producción vinculada"
            )
        if not any(orden.estado == "FINALIZADO" for orden in ordenes_vigentes):
            raise ValueError("El pedido aún tiene solicitudes de producción sin cerrar")


def _reservar_inventario_para_pedido(
    detalles_ordenados: list[DetalleCarrito],
) -> dict[int, Producto]:
    productos_bloqueados: dict[int, Producto] = {}
    for detalle in detalles_ordenados:
        producto = _obtener_producto_bloqueado(detalle.id_producto)
        if not producto or not producto.activo:
            raise ValueError("Producto no disponible")
        if _cantidad_libre_producto(producto) < detalle.cantidad:
            raise ValueError(f"Stock insuficiente para {producto.nombre}")
        producto.cantidad_reservada = int(producto.cantidad_reservada or 0) + int(
            detalle.cantidad
        )
        productos_bloqueados[producto.id_producto] = producto
    return productos_bloqueados


def _registrar_detalles_pedido(
    *,
    pedido: Pedido,
    detalles_ordenados: list[DetalleCarrito],
    productos_bloqueados: dict[int, Producto],
) -> Decimal:
    total = Decimal("0")
    for detalle in detalles_ordenados:
        producto = productos_bloqueados[detalle.id_producto]
        subtotal = Decimal(str(producto.precio_venta)) * detalle.cantidad
        db.session.add(
            DetallePedido(
                id_pedido=pedido.id_pedido,
                id_producto=producto.id_producto,
                cantidad=detalle.cantidad,
                precio_unitario=producto.precio_venta,
                subtotal=subtotal,
            )
        )
        total += subtotal
    return total


def _confirmar_pedido(pedido: Pedido) -> None:
    if pedido.tipo_pago == "EN_LINEA" and pedido.estado_pago != "PAGADO":
        raise ValueError("Un pedido en linea debe estar pagado para poder confirmarse")
    pedido.estado_pedido = "CONFIRMADO"


def _marcar_pedido_como_pagado(
    *, pedido: Pedido, referencia_pago: str | None = None
) -> None:
    if (
        pedido.tipo_pago == "EN_LINEA"
        and not pedido.referencia_pago
        and referencia_pago
    ):
        pedido.referencia_pago = referencia_pago

    pedido.estado_pago = "PAGADO"
    pedido.estado_pedido = "PAGADO"
    if not pedido.pago:
        return

    pedido.pago.estado_pago = "PAGADO"
    if referencia_pago:
        pedido.pago.referencia = referencia_pago
    pedido.pago.fecha_pago = utc_now()


def _cancelar_pedido_liberando_reserva(pedido: Pedido) -> None:
    detalles_ordenados = sorted(
        pedido.detalles,
        key=lambda detalle_pedido: detalle_pedido.id_producto,
    )
    for detalle in detalles_ordenados:
        producto = _obtener_producto_bloqueado(detalle.id_producto)
        if not producto:
            continue
        reservada = int(producto.cantidad_reservada or 0)
        liberar = min(reservada, int(detalle.cantidad))
        producto.cantidad_reservada = reservada - liberar
    pedido.estado_pedido = "CANCELADO"


def _registrar_historial_estado_pedido(
    *,
    pedido: Pedido,
    estado_anterior: str | None,
    estado_nuevo: str,
    detalle: str | None,
    id_usuario_accion: int | None,
) -> None:
    db.session.add(
        PedidoEstadoHistorial(
            id_pedido=pedido.id_pedido,
            estado_anterior=estado_anterior,
            estado_nuevo=estado_nuevo,
            detalle=(detalle or "").strip() or None,
            id_usuario=id_usuario_accion,
        )
    )


def registrar_compra(compra: Compra, detalles: list[dict]) -> Compra:
    if not detalles:
        raise ValueError("La compra debe incluir al menos un detalle")

    db.session.add(compra)
    db.session.flush()

    total = Decimal("0")
    ids_materia_impactadas: set[int] = set()
    for item in detalles:
        materia = _obtener_materia_bloqueada(item["id_materia_prima"])
        if not materia or not materia.activa:
            raise ValueError("Materia prima no encontrada")

        cantidad_comprada = Decimal(str(item["cantidad_comprada"]))
        precio_unitario = Decimal(str(item["precio_unitario"]))
        if cantidad_comprada <= 0:
            raise ValueError("La cantidad comprada debe ser mayor a cero")
        if precio_unitario < 0:
            raise ValueError("El precio unitario no puede ser negativo")

        dimension_compra = (materia.unidad_compra.dimension or "CONTEO").upper()
        if dimension_compra == "CONTEO" and not _is_integer_decimal(cantidad_comprada):
            raise ValueError(
                f"La materia prima '{materia.nombre}' usa conteo y la cantidad comprada debe ser entera"
            )

        factor_conversion = Decimal(str(materia.factor_conversion))
        if factor_conversion <= 0:
            raise ValueError(
                f"La materia prima '{materia.nombre}' tiene un factor de conversion invalido"
            )

        subtotal = cantidad_comprada * precio_unitario
        cantidad_base = cantidad_comprada * factor_conversion
        if cantidad_base <= 0:
            raise ValueError("La cantidad convertida debe ser mayor a cero")

        dimension_base = (materia.unidad_base.dimension or "CONTEO").upper()
        if dimension_base == "CONTEO":
            if not _is_integer_decimal(cantidad_base):
                raise ValueError(
                    f"La materia prima '{materia.nombre}' requiere conversión entera en unidades de conteo"
                )
            cantidad_base = _as_integer_decimal(cantidad_base)

        _actualizar_costo_unitario_materia_prima_desde_compra(
            materia=materia,
            cantidad_base_ingreso=cantidad_base,
            precio_unitario_compra=precio_unitario,
            factor_conversion=factor_conversion,
        )

        detalle = DetalleCompra(
            id_materia_prima=materia.id_materia,
            cantidad_comprada=cantidad_comprada,
            id_unidad_compra=materia.id_unidad_compra,
            precio_unitario=precio_unitario,
            subtotal=_to_mxn(subtotal),
            cantidad_base=cantidad_base,
        )
        compra.detalles.append(detalle)

        materia.cantidad_disponible = (
            Decimal(str(materia.cantidad_disponible)) + cantidad_base
        )
        if dimension_base == "CONTEO":
            materia.cantidad_disponible = _as_integer_decimal(
                materia.cantidad_disponible
            )

        movimiento = MovimientoInventarioMP(
            id_materia_prima=materia.id_materia,
            tipo="ENTRADA",
            cantidad=cantidad_base,
            id_usuario=compra.id_usuario_comprador,
            referencia_id=f"COMPRA-{compra.id_compra}",
        )
        db.session.add(movimiento)
        total += subtotal
        ids_materia_impactadas.add(materia.id_materia)

    compra.total = _to_mxn(total)
    recalcular_costos_productos_afectados_por_materias(
        ids_materia=list(ids_materia_impactadas)
    )
    db.session.commit()
    return compra


def crear_materia_prima(
    *,
    nombre: str,
    id_unidad_base: int,
    id_unidad_compra: int,
    factor_conversion: Decimal,
    porcentaje_merma: Decimal,
    stock_minimo: Decimal,
    cantidad_inicial: Decimal,
    id_usuario: int,
) -> MateriaPrima:
    nombre_limpio = (nombre or "").strip()
    if not nombre_limpio:
        raise ValueError("El nombre de la materia prima es obligatorio")
    if id_unidad_base <= 0 or id_unidad_compra <= 0:
        raise ValueError("Debes seleccionar unidad base y unidad de compra")
    if factor_conversion <= 0:
        factor_inferido = _inferir_factor_conversion_por_unidades(
            id_unidad_base=id_unidad_base,
            id_unidad_compra=id_unidad_compra,
        )
        if not factor_inferido or factor_inferido <= 0:
            raise ValueError("El factor de conversion debe ser mayor a cero")
        factor_conversion = factor_inferido

    _validar_reglas_conteo_materia(
        id_unidad_base=id_unidad_base,
        id_unidad_compra=id_unidad_compra,
        factor_conversion=factor_conversion,
        stock_minimo=stock_minimo,
        cantidad_inicial=cantidad_inicial,
    )

    if porcentaje_merma < 0:
        raise ValueError("El porcentaje de merma no puede ser negativo")
    if stock_minimo < 0 or cantidad_inicial < 0:
        raise ValueError("Las cantidades de inventario no pueden ser negativas")

    existente = MateriaPrima.query.filter(
        db.func.lower(MateriaPrima.nombre) == nombre_limpio.lower()
    ).first()
    if existente:
        raise ValueError("Ya existe una materia prima con ese nombre")

    materia = MateriaPrima(
        nombre=nombre_limpio,
        id_unidad_base=id_unidad_base,
        id_unidad_compra=id_unidad_compra,
        factor_conversion=(
            _as_integer_decimal(factor_conversion)
            if _is_integer_decimal(factor_conversion)
            else factor_conversion
        ),
        porcentaje_merma=porcentaje_merma,
        stock_minimo=(
            _as_integer_decimal(stock_minimo)
            if _is_integer_decimal(stock_minimo)
            else stock_minimo
        ),
        cantidad_disponible=(
            _as_integer_decimal(cantidad_inicial)
            if _is_integer_decimal(cantidad_inicial)
            else cantidad_inicial
        ),
        activa=True,
    )
    db.session.add(materia)
    db.session.flush()

    if cantidad_inicial > 0:
        db.session.add(
            MovimientoInventarioMP(
                id_materia_prima=materia.id_materia,
                tipo="ENTRADA",
                cantidad=cantidad_inicial,
                id_usuario=id_usuario,
                referencia_id=f"ALTA-MP-{materia.id_materia}",
            )
        )

    db.session.commit()
    return materia


def actualizar_materia_prima(
    *,
    id_materia: int,
    nombre: str,
    id_unidad_base: int,
    id_unidad_compra: int,
    factor_conversion: Decimal,
    porcentaje_merma: Decimal,
    stock_minimo: Decimal,
) -> MateriaPrima:
    materia = _obtener_materia_bloqueada(id_materia)
    if not materia:
        raise ValueError("Materia prima no encontrada")

    nombre_limpio = (nombre or "").strip()
    if not nombre_limpio:
        raise ValueError("El nombre de la materia prima es obligatorio")
    if id_unidad_base <= 0 or id_unidad_compra <= 0:
        raise ValueError("Debes seleccionar unidad base y unidad de compra")
    if factor_conversion <= 0:
        factor_inferido = _inferir_factor_conversion_por_unidades(
            id_unidad_base=id_unidad_base,
            id_unidad_compra=id_unidad_compra,
        )
        if not factor_inferido or factor_inferido <= 0:
            raise ValueError("El factor de conversion debe ser mayor a cero")
        factor_conversion = factor_inferido

    _validar_reglas_conteo_materia(
        id_unidad_base=id_unidad_base,
        id_unidad_compra=id_unidad_compra,
        factor_conversion=factor_conversion,
        stock_minimo=stock_minimo,
    )

    if porcentaje_merma < 0:
        raise ValueError("El porcentaje de merma no puede ser negativo")
    if stock_minimo < 0:
        raise ValueError("El stock minimo no puede ser negativo")

    duplicado = MateriaPrima.query.filter(
        db.func.lower(MateriaPrima.nombre) == nombre_limpio.lower(),
        MateriaPrima.id_materia != materia.id_materia,
    ).first()
    if duplicado:
        raise ValueError("Ya existe una materia prima con ese nombre")

    materia.nombre = nombre_limpio
    materia.id_unidad_base = id_unidad_base
    materia.id_unidad_compra = id_unidad_compra
    materia.factor_conversion = (
        _as_integer_decimal(factor_conversion)
        if _is_integer_decimal(factor_conversion)
        else factor_conversion
    )
    materia.porcentaje_merma = porcentaje_merma
    materia.stock_minimo = (
        _as_integer_decimal(stock_minimo)
        if _is_integer_decimal(stock_minimo)
        else stock_minimo
    )

    recalcular_costos_productos_afectados_por_materias(ids_materia=[materia.id_materia])
    db.session.commit()
    return materia


def desactivar_materia_prima(*, id_materia: int) -> MateriaPrima:
    materia = _obtener_materia_bloqueada(id_materia)
    if not materia:
        raise ValueError("Materia prima no encontrada")
    if not materia.activa:
        return materia

    uso_en_receta_activa = (
        db.session.query(DetalleReceta.id_detalle)
        .join(Receta, Receta.id_receta == DetalleReceta.id_receta)
        .filter(DetalleReceta.id_materia_prima == materia.id_materia)
        .filter(Receta.activa.is_(True))
        .first()
    )
    if uso_en_receta_activa:
        raise ValueError(
            "No se puede desactivar: la materia prima esta en recetas activas"
        )

    materia.activa = False
    db.session.commit()
    return materia


def agregar_producto_a_carrito(
    id_usuario: int, id_producto: int, cantidad: int
) -> None:
    if cantidad < 1 or cantidad > 5:
        raise ValueError("La cantidad por producto en carrito debe estar entre 1 y 5")

    producto = Producto.query.get(id_producto)
    if not producto or not producto.activo:
        raise ValueError("Producto no disponible")

    if _cantidad_libre_producto(producto) <= 0:
        raise ValueError("Producto sin inventario disponible")

    if cantidad > _cantidad_libre_producto(producto):
        raise ValueError("La cantidad solicitada excede el inventario disponible")

    carrito = Carrito.query.filter_by(id_usuario_cliente=id_usuario).first()
    if not carrito:
        carrito = Carrito(id_usuario_cliente=id_usuario)
        db.session.add(carrito)
        db.session.flush()

    detalle = DetalleCarrito.query.filter_by(
        id_carrito=carrito.id_carrito, id_producto=id_producto
    ).first()
    if not detalle:
        detalle = DetalleCarrito(
            id_carrito=carrito.id_carrito, id_producto=id_producto, cantidad=cantidad
        )
        db.session.add(detalle)
    else:
        nueva_cantidad = detalle.cantidad + cantidad
        if nueva_cantidad > 5:
            raise ValueError("No puedes agregar mas de 5 unidades del mismo producto")
        if nueva_cantidad > _cantidad_libre_producto(producto):
            raise ValueError("La cantidad solicitada excede el inventario disponible")
        detalle.cantidad = nueva_cantidad

    db.session.commit()


def crear_venta_desde_carrito(id_usuario: int, pagado_en_linea: bool) -> Venta:
    carrito = Carrito.query.filter_by(id_usuario_cliente=id_usuario).first()
    if not carrito or not carrito.detalles:
        raise ValueError("No hay productos en el carrito")

    venta = Venta(
        id_usuario_cliente=id_usuario,
        estado="EN_PROCESO_PRODUCCION",
        tipo_pago="TARJETA" if pagado_en_linea else "EFECTIVO",
        fecha=utc_now(),
    )
    db.session.add(venta)
    db.session.flush()

    total = Decimal("0")
    for detalle in carrito.detalles:
        producto = Producto.query.get(detalle.id_producto)
        if not producto or not producto.activo:
            raise ValueError("Producto no disponible")
        subtotal = Decimal(str(producto.precio_venta)) * detalle.cantidad
        costo_unitario_produccion = _costo_unitario_produccion_para_venta(
            producto=producto
        )
        utilidad_unitaria = _to_mxn(
            Decimal(str(producto.precio_venta)) - costo_unitario_produccion
        )
        db.session.add(
            DetalleVenta(
                id_venta=venta.id_venta,
                id_producto=producto.id_producto,
                cantidad=detalle.cantidad,
                precio_unitario=producto.precio_venta,
                costo_unitario_produccion=costo_unitario_produccion,
                utilidad_unitaria=utilidad_unitaria,
                subtotal=subtotal,
            )
        )
        total += subtotal

    venta.total = total
    DetalleCarrito.query.filter_by(id_carrito=carrito.id_carrito).delete()
    db.session.commit()
    return venta


def crear_pedido_desde_carrito(
    *,
    id_usuario: int,
    fecha_entrega,
    tipo_pago_pedido: str,
    tipo_pago: str,
    referencia_pago: str | None,
    id_usuario_accion: int | None = None,
) -> Pedido:
    carrito = Carrito.query.filter_by(id_usuario_cliente=id_usuario).first()
    if not carrito or not carrito.detalles:
        raise ValueError("No hay productos en el carrito")

    _validar_parametros_checkout(
        tipo_pago_pedido=tipo_pago_pedido,
        tipo_pago=tipo_pago,
        referencia_pago=referencia_pago,
    )
    _validar_fecha_entrega_programada(fecha_entrega)

    # Validar y reservar inventario al momento de generar pedido.
    detalles_ordenados = sorted(
        carrito.detalles,
        key=lambda detalle_carrito: detalle_carrito.id_producto,
    )
    productos_bloqueados = _reservar_inventario_para_pedido(detalles_ordenados)

    pedido = Pedido(
        id_usuario_cliente=id_usuario,
        fecha_entrega=fecha_entrega,
        tipo_pago=tipo_pago_pedido,
        estado_pedido="PENDIENTE",
        estado_pago="PENDIENTE",
        referencia_pago=referencia_pago,
    )
    db.session.add(pedido)
    db.session.flush()
    _registrar_historial_estado_pedido(
        pedido=pedido,
        estado_anterior=None,
        estado_nuevo="PENDIENTE",
        detalle="Pedido creado desde carrito web.",
        id_usuario_accion=id_usuario_accion or id_usuario,
    )

    total = _registrar_detalles_pedido(
        pedido=pedido,
        detalles_ordenados=detalles_ordenados,
        productos_bloqueados=productos_bloqueados,
    )

    pedido.total = total
    estado_pago_inicial = "PAGADO" if tipo_pago_pedido == "EN_LINEA" else "PENDIENTE"
    fecha_pago_inicial = utc_now() if estado_pago_inicial == "PAGADO" else None
    db.session.add(
        PagoPedido(
            id_pedido=pedido.id_pedido,
            estado_pago=estado_pago_inicial,
            tipo_pago=tipo_pago,
            referencia=referencia_pago,
            fecha_pago=fecha_pago_inicial,
        )
    )

    if tipo_pago_pedido == "EN_LINEA":
        pedido.estado_pago = "PAGADO"
        pedido.estado_pedido = "CONFIRMADO"
        _registrar_historial_estado_pedido(
            pedido=pedido,
            estado_anterior="PENDIENTE",
            estado_nuevo="CONFIRMADO",
            detalle=(
                "Confirmación automática por pago en línea"
                + (f". Ref: {pedido.referencia_pago}" if pedido.referencia_pago else "")
            ),
            id_usuario_accion=id_usuario_accion or id_usuario,
        )

    DetalleCarrito.query.filter_by(id_carrito=carrito.id_carrito).delete()
    db.session.commit()
    return pedido


def generar_venta_desde_pedido(
    *,
    id_pedido: int,
    requiere_ticket: bool,
    id_usuario_accion: int | None = None,
) -> Venta:
    pedido = _obtener_pedido_bloqueado(id_pedido)
    if not pedido:
        raise ValueError("Pedido no encontrado")
    if pedido.estado_pedido == "CANCELADO":
        raise ValueError("No se puede generar venta de un pedido cancelado")
    if pedido.estado_pago != "PAGADO" or pedido.estado_pedido not in {
        "CONFIRMADO",
        "PAGADO",
    }:
        raise ValueError(
            "Solo pedidos confirmados y pagados pueden entregarse y generar venta"
        )
    _validar_pedido_producido_para_entrega(pedido)

    # Validar y descontar inventario al momento de entrega/venta.
    detalles_ordenados = sorted(
        pedido.detalles, key=lambda detalle_pedido: detalle_pedido.id_producto
    )
    productos_bloqueados: dict[int, Producto] = {}
    total = Decimal("0")
    id_usuario_movimiento = id_usuario_accion or pedido.id_usuario_cliente
    for d in detalles_ordenados:
        producto = _obtener_producto_bloqueado(d.id_producto)
        if not producto:
            raise ValueError(
                "No se puede entregar: uno de los productos del pedido ya no existe"
            )

        disponible = int(producto.cantidad_disponible or 0)
        reservada = int(producto.cantidad_reservada or 0)
        libre = max(disponible - reservada, 0)
        faltante_reserva = max(int(d.cantidad) - reservada, 0)
        if libre < faltante_reserva:
            raise ValueError(f"Stock insuficiente para entregar {producto.nombre}")

        productos_bloqueados[producto.id_producto] = producto
        total += Decimal(str(d.subtotal))

    venta = Venta(
        id_pedido=pedido.id_pedido,
        id_usuario_cliente=pedido.id_usuario_cliente,
        fecha=utc_now(),
        total=total,
        estado="CONFIRMADO",
        tipo_pago=(
            "TARJETA"
            if (pedido.pago and pedido.pago.tipo_pago == "TARJETA")
            else "EFECTIVO"
        ),
        requiere_ticket=requiere_ticket,
    )
    db.session.add(venta)
    db.session.flush()

    for d in detalles_ordenados:
        producto = productos_bloqueados[d.id_producto]
        disponible = int(producto.cantidad_disponible or 0)
        reservada = int(producto.cantidad_reservada or 0)
        consumir_reserva = min(reservada, int(d.cantidad))
        costo_unitario_produccion = _costo_unitario_produccion_para_venta(
            producto=producto
        )
        utilidad_unitaria = _to_mxn(
            Decimal(str(d.precio_unitario)) - costo_unitario_produccion
        )

        producto.cantidad_disponible = disponible - int(d.cantidad)
        producto.cantidad_reservada = reservada - consumir_reserva
        db.session.add(
            DetalleVenta(
                id_venta=venta.id_venta,
                id_producto=producto.id_producto,
                cantidad=d.cantidad,
                precio_unitario=d.precio_unitario,
                costo_unitario_produccion=costo_unitario_produccion,
                utilidad_unitaria=utilidad_unitaria,
                subtotal=d.subtotal,
            )
        )

        stock_posterior = disponible - int(d.cantidad)
        producto.cantidad_disponible = stock_posterior
        producto.cantidad_reservada = reservada - consumir_reserva
        _registrar_movimiento_producto(
            producto=producto,
            tipo="SALIDA",
            cantidad=int(d.cantidad),
            stock_anterior=disponible,
            stock_posterior=stock_posterior,
            id_usuario=id_usuario_movimiento,
            referencia_id=f"PEDIDO-{pedido.id_pedido}",
        )

    if requiere_ticket:
        _crear_ticket_venta(venta.id_venta)

    estado_anterior = pedido.estado_pedido
    pedido.estado_pedido = "ENTREGADO"
    _registrar_historial_estado_pedido(
        pedido=pedido,
        estado_anterior=estado_anterior,
        estado_nuevo="ENTREGADO",
        detalle=(
            f"Pedido entregado y venta #{venta.id_venta} generada"
            + (" con ticket." if requiere_ticket else ".")
        ),
        id_usuario_accion=id_usuario_accion,
    )
    db.session.commit()
    return venta


def actualizar_estado_pedido(
    *,
    id_pedido: int,
    nuevo_estado: str,
    referencia_pago: str | None = None,
    id_usuario_accion: int | None = None,
) -> Pedido:
    pedido = _obtener_pedido_bloqueado(id_pedido)
    if not pedido:
        raise ValueError("Pedido no encontrado")

    nuevo_estado = (nuevo_estado or "").strip().upper()
    if nuevo_estado not in {
        "PENDIENTE",
        "CONFIRMADO",
        "PAGADO",
        "ENTREGADO",
        "CANCELADO",
    }:
        raise ValueError("Estado invalido")

    if pedido.estado_pedido == nuevo_estado:
        return pedido

    if nuevo_estado == "ENTREGADO":
        raise ValueError(
            "La entrega genera venta; usa el flujo de entrega para completarla"
        )

    _validar_transicion_estado(
        estado_actual=pedido.estado_pedido,
        estado_destino=nuevo_estado,
        mapa_transiciones=TRANSICIONES_PEDIDO,
        entidad="pedido",
    )

    estado_anterior = pedido.estado_pedido

    if nuevo_estado == "CANCELADO":
        _cancelar_pedido_liberando_reserva(pedido)
        _registrar_historial_estado_pedido(
            pedido=pedido,
            estado_anterior=estado_anterior,
            estado_nuevo="CANCELADO",
            detalle="Pedido cancelado por usuario interno.",
            id_usuario_accion=id_usuario_accion,
        )
        db.session.commit()
        return pedido

    if nuevo_estado == "CONFIRMADO":
        _confirmar_pedido(pedido)
        _registrar_historial_estado_pedido(
            pedido=pedido,
            estado_anterior=estado_anterior,
            estado_nuevo="CONFIRMADO",
            detalle="Pedido confirmado para preparación/entrega.",
            id_usuario_accion=id_usuario_accion,
        )
        db.session.commit()
        return pedido

    if nuevo_estado == "PAGADO":
        _marcar_pedido_como_pagado(
            pedido=pedido,
            referencia_pago=referencia_pago,
        )
        _registrar_historial_estado_pedido(
            pedido=pedido,
            estado_anterior=estado_anterior,
            estado_nuevo="PAGADO",
            detalle=(
                "Pago aplicado en módulo interno"
                + (f". Ref: {referencia_pago}" if referencia_pago else ".")
            ),
            id_usuario_accion=id_usuario_accion,
        )
        db.session.commit()
        return pedido

    pedido.estado_pedido = nuevo_estado
    _registrar_historial_estado_pedido(
        pedido=pedido,
        estado_anterior=estado_anterior,
        estado_nuevo=nuevo_estado,
        detalle="Actualización de estado manual.",
        id_usuario_accion=id_usuario_accion,
    )
    db.session.commit()
    return pedido


def calcular_costo_unitario_producto(*, id_producto: int) -> Decimal:
    producto = Producto.query.get(id_producto)
    if not producto or not producto.activo:
        raise ValueError("Producto no disponible")
    if not producto.id_receta:
        raise ValueError("Producto sin receta activa")

    receta = Receta.query.get(producto.id_receta)
    if not receta or not receta.activa:
        raise ValueError("Producto sin receta activa")
    if receta.rendimiento_base <= 0:
        raise ValueError("Rendimiento base invalido")

    total = Decimal("0")
    for det in DetalleReceta.query.filter_by(id_receta=receta.id_receta).all():
        materia = MateriaPrima.query.get(det.id_materia_prima)
        if not materia or not materia.activa:
            raise ValueError("Materia prima no disponible")
        base = Decimal(str(det.cantidad_base))
        merma_factor = Decimal("1") + (
            Decimal(str(materia.porcentaje_merma)) / Decimal("100")
        )
        real = base * merma_factor
        total += real * Decimal(str(materia.costo_unitario))

    return total / Decimal(str(receta.rendimiento_base))


def calcular_costo_producto(*, id_producto: int, cantidad: int) -> Decimal:
    if cantidad <= 0:
        return Decimal("0")
    return calcular_costo_unitario_producto(id_producto=id_producto) * Decimal(
        str(cantidad)
    )


def recalcular_costo_y_precio_sugerido_producto(*, id_producto: int) -> Producto:
    producto = _obtener_producto_bloqueado(id_producto)
    if not producto:
        raise ValueError("Producto no disponible")

    costo_unitario = _to_mxn(calcular_costo_unitario_producto(id_producto=id_producto))
    margen_objetivo = Decimal(str(producto.margen_objetivo_pct or Decimal("25")))
    if margen_objetivo <= 0 or margen_objetivo >= 100:
        raise ValueError("Margen objetivo invalido para calcular precio sugerido")

    divisor = Decimal("1") - (margen_objetivo / Decimal("100"))
    if divisor <= 0:
        raise ValueError("No es posible calcular precio sugerido con ese margen")

    precio_sugerido = _to_mxn(costo_unitario / divisor)
    producto.costo_produccion_actual = costo_unitario
    producto.precio_sugerido = precio_sugerido
    producto.fecha_costo_actualizado = utc_now()
    return producto


def recalcular_costos_productos_afectados_por_materias(
    *, ids_materia: list[int]
) -> dict:
    ids_materia_validas = sorted({int(m) for m in ids_materia if int(m) > 0})
    if not ids_materia_validas:
        return {"actualizados": 0, "errores": []}

    productos_ids = (
        db.session.query(Producto.id_producto)
        .join(Receta, Receta.id_receta == Producto.id_receta)
        .join(DetalleReceta, DetalleReceta.id_receta == Receta.id_receta)
        .filter(Producto.activo.is_(True))
        .filter(DetalleReceta.id_materia_prima.in_(ids_materia_validas))
        .distinct()
        .all()
    )

    errores: list[dict] = []
    actualizados = 0
    for (id_producto,) in productos_ids:
        try:
            recalcular_costo_y_precio_sugerido_producto(id_producto=id_producto)
            actualizados += 1
        except ValueError as exc:
            errores.append(
                {
                    "id_producto": id_producto,
                    "error": str(exc),
                }
            )

    return {"actualizados": actualizados, "errores": errores}


def _costo_unitario_produccion_para_venta(*, producto: Producto) -> Decimal:
    costo_actual = Decimal(str(producto.costo_produccion_actual or 0))
    if costo_actual > 0:
        return _to_mxn(costo_actual)

    try:
        return _to_mxn(
            calcular_costo_unitario_producto(id_producto=producto.id_producto)
        )
    except ValueError:
        return Decimal("0.00")


def crear_orden_produccion(
    *,
    id_receta: int,
    cantidad: int,
    id_usuario: int,
    id_producto: int | None = None,
    id_solicitud: int | None = None,
    observaciones: str | None = None,
) -> OrdenProduccion:
    if cantidad <= 0:
        raise ValueError("La cantidad a producir debe ser mayor a cero")

    solicitud = None
    if id_solicitud:
        solicitud = SolicitudProduccion.query.get(id_solicitud)
        if not solicitud:
            raise ValueError("Solicitud no encontrada")
        if solicitud.estado != "APROBADA":
            raise ValueError("Solo solicitudes aprobadas pueden generar orden")

        orden_existente = (
            OrdenProduccion.query.filter_by(id_solicitud=id_solicitud)
            .filter(OrdenProduccion.estado != "CANCELADO")
            .first()
        )
        if orden_existente:
            raise ValueError(
                "La solicitud ya tiene una orden de produccion activa o finalizada"
            )

        if id_producto and int(id_producto) != int(solicitud.id_producto):
            raise ValueError("La solicitud no corresponde al producto seleccionado")
        id_producto = solicitud.id_producto

    if not id_producto or int(id_producto) <= 0:
        raise ValueError("Debes seleccionar un producto valido")

    receta = Receta.query.get(id_receta)
    if not receta or not receta.activa:
        raise ValueError("Receta activa no encontrada")
    if int(receta.id_producto) != int(id_producto):
        raise ValueError("La receta seleccionada no corresponde al producto")

    producto = _obtener_producto_bloqueado(int(id_producto))
    if not producto or not producto.activo:
        raise ValueError("Producto no disponible para produccion")
    if not producto.id_receta:
        raise ValueError("El producto no tiene receta asignada")
    if int(producto.id_receta) != int(receta.id_receta):
        raise ValueError("Debes seleccionar la receta activa del producto")

    _validar_disponibilidad_materia_prima_para_orden(
        receta=receta,
        cantidad_producir=cantidad,
    )

    orden = OrdenProduccion(
        id_solicitud=solicitud.id_solicitud if solicitud else None,
        id_receta=id_receta,
        id_producto=producto.id_producto,
        cantidad_producir=cantidad,
        id_usuario_responsable=id_usuario,
        observaciones=(observaciones or "").strip() or None,
    )
    db.session.add(orden)
    db.session.flush()
    db.session.commit()
    return orden


def iniciar_orden_produccion(
    *,
    id_orden: int,
    id_usuario: int | None = None,
) -> OrdenProduccion:
    orden = _obtener_orden_bloqueada(id_orden)
    if not orden:
        raise ValueError("Orden no encontrada")
    _validar_transicion_estado(
        estado_actual=orden.estado,
        estado_destino="EN_PROCESO",
        mapa_transiciones=TRANSICIONES_ORDEN,
        entidad="orden de produccion",
    )

    receta = Receta.query.get(orden.id_receta)
    if not receta or not receta.activa:
        raise ValueError("Receta activa no encontrada")

    producto = _obtener_producto_bloqueado(orden.id_producto)
    if not producto or not producto.activo:
        raise ValueError("No se encontro el producto asociado a la orden")

    if orden.detalles_consumo:
        raise ValueError("La orden ya tiene consumo registrado")

    usuario_movimiento = id_usuario or orden.id_usuario_responsable
    orden.costo_total = _registrar_consumo_materias_en_orden(
        orden=orden,
        receta=receta,
        id_usuario=usuario_movimiento,
    )

    orden.estado = "EN_PROCESO"
    orden.fecha_inicio = utc_now()
    db.session.commit()
    return orden


def finalizar_orden_produccion(
    *, id_orden: int, id_usuario: int | None = None
) -> OrdenProduccion:
    orden = _obtener_orden_bloqueada(id_orden)
    if not orden:
        raise ValueError("Orden no encontrada")

    _validar_transicion_estado(
        estado_actual=orden.estado,
        estado_destino="FINALIZADO",
        mapa_transiciones=TRANSICIONES_ORDEN,
        entidad="orden de produccion",
    )

    producto = _obtener_producto_bloqueado(orden.id_producto)
    if not producto or not producto.activo:
        raise ValueError("No se encontro el producto asociado a la orden")

    receta = Receta.query.get(orden.id_receta)
    if not receta or not receta.activa:
        raise ValueError("Receta activa no encontrada")

    if not orden.detalles_consumo:
        usuario_movimiento = id_usuario or orden.id_usuario_responsable
        orden.costo_total = _registrar_consumo_materias_en_orden(
            orden=orden,
            receta=receta,
            id_usuario=usuario_movimiento,
        )

    stock_anterior = int(producto.cantidad_disponible or 0)
    incremento = int(orden.cantidad_producir)
    producto.cantidad_disponible = stock_anterior + incremento
    _registrar_movimiento_producto(
        producto=producto,
        tipo="ENTRADA",
        cantidad=incremento,
        stock_anterior=stock_anterior,
        stock_posterior=int(producto.cantidad_disponible or 0),
        id_usuario=id_usuario or orden.id_usuario_responsable,
        referencia_id=f"ORD-{orden.id_orden}",
    )
    try:
        recalcular_costo_y_precio_sugerido_producto(id_producto=producto.id_producto)
    except ValueError:
        # Si el producto aún no tiene receta activa o la receta es inválida,
        # mantenemos el flujo de inventario sin bloquear la finalización.
        pass
    orden.estado = "FINALIZADO"
    orden.fecha_fin = utc_now()
    db.session.commit()
    return orden


def cancelar_orden_produccion(*, id_orden: int) -> OrdenProduccion:
    orden = _obtener_orden_bloqueada(id_orden)
    if not orden:
        raise ValueError("Orden no encontrada")

    _validar_transicion_estado(
        estado_actual=orden.estado,
        estado_destino="CANCELADO",
        mapa_transiciones=TRANSICIONES_ORDEN,
        entidad="orden de produccion",
    )

    orden.estado = "CANCELADO"
    db.session.commit()
    return orden


def registrar_venta_mostrador(
    *,
    id_producto: int,
    cantidad: int,
    tipo_pago: str,
    requiere_ticket: bool,
    id_usuario_emite: int,
) -> Venta:
    return registrar_venta_mostrador_detallada(
        items=[{"id_producto": id_producto, "cantidad": cantidad}],
        tipo_pago=tipo_pago,
        requiere_ticket=requiere_ticket,
        id_usuario_emite=id_usuario_emite,
    )


def registrar_venta_mostrador_detallada(
    *,
    items: list[dict],
    tipo_pago: str,
    requiere_ticket: bool,
    id_usuario_emite: int,
) -> Venta:
    if tipo_pago not in {"EFECTIVO", "TARJETA"}:
        raise ValueError("Tipo de pago invalido")
    if not items:
        raise ValueError("La venta debe incluir al menos un producto")

    acumulado: dict[int, int] = {}
    for item in items:
        id_producto = int(item.get("id_producto", 0))
        cantidad = int(item.get("cantidad", 0))
        if id_producto <= 0 or cantidad <= 0:
            raise ValueError("Detalle de venta invalido")
        acumulado[id_producto] = acumulado.get(id_producto, 0) + cantidad

    productos_bloqueados: dict[int, Producto] = {}
    for id_producto, cantidad in acumulado.items():
        producto = _obtener_producto_bloqueado(id_producto)
        if not producto or not producto.activo:
            raise ValueError("Producto no disponible para venta")
        if _cantidad_libre_producto(producto) < cantidad:
            raise ValueError(f"Stock insuficiente para {producto.nombre}")
        productos_bloqueados[id_producto] = producto

    venta = Venta(
        id_usuario_cliente=id_usuario_emite,
        total=Decimal("0"),
        estado="CONFIRMADO",
        tipo_pago=tipo_pago,
        requiere_ticket=requiere_ticket,
    )
    db.session.add(venta)
    db.session.flush()

    total = Decimal("0")
    for id_producto, cantidad in acumulado.items():
        producto = productos_bloqueados[id_producto]
        subtotal = _to_mxn(Decimal(str(producto.precio_venta)) * cantidad)
        costo_unitario = _costo_unitario_produccion_para_venta(producto=producto)
        utilidad_unitaria = _to_mxn(
            Decimal(str(producto.precio_venta)) - costo_unitario
        )

        db.session.add(
            DetalleVenta(
                id_venta=venta.id_venta,
                id_producto=producto.id_producto,
                cantidad=cantidad,
                precio_unitario=producto.precio_venta,
                costo_unitario_produccion=costo_unitario,
                utilidad_unitaria=utilidad_unitaria,
                subtotal=subtotal,
            )
        )

        stock_anterior = int(producto.cantidad_disponible or 0)
        stock_posterior = stock_anterior - cantidad
        producto.cantidad_disponible = stock_posterior
        _registrar_movimiento_producto(
            producto=producto,
            tipo="SALIDA",
            cantidad=cantidad,
            stock_anterior=stock_anterior,
            stock_posterior=stock_posterior,
            id_usuario=id_usuario_emite,
            referencia_id=f"VENTA-{venta.id_venta}",
        )
        total += subtotal

    venta.total = _to_mxn(total)
    if requiere_ticket:
        _crear_ticket_venta(venta.id_venta)
    db.session.commit()
    return venta


def cancelar_venta_mostrador(*, id_venta: int, id_usuario: int) -> Venta:
    venta = Venta.query.filter_by(id_venta=id_venta).with_for_update().first()
    if not venta:
        raise ValueError("Venta no encontrada")
    if venta.estado == "CANCELADO":
        return venta
    if venta.estado != "CONFIRMADO":
        raise ValueError("Solo se pueden cancelar ventas confirmadas")

    for detalle in venta.detalles:
        producto = _obtener_producto_bloqueado(detalle.id_producto)
        if not producto:
            continue
        stock_anterior = int(producto.cantidad_disponible or 0)
        stock_posterior = stock_anterior + int(detalle.cantidad or 0)
        producto.cantidad_disponible = stock_posterior
        _registrar_movimiento_producto(
            producto=producto,
            tipo="ENTRADA",
            cantidad=int(detalle.cantidad or 0),
            stock_anterior=stock_anterior,
            stock_posterior=stock_posterior,
            id_usuario=id_usuario,
            referencia_id=f"CANCEL-VENTA-{venta.id_venta}",
        )

    venta.estado = "CANCELADO"
    db.session.commit()
    return venta


def pagar_compra(id_compra: int, id_usuario: int) -> None:
    compra = Compra.query.get(id_compra)
    if not compra:
        raise ValueError("Compra no encontrada")
    if compra.estado_pago == "PAGADO":
        raise ValueError("La compra ya fue marcada como pagada")
    compra.estado_pago = "PAGADO"

    salida = SalidaEfectivo(
        concepto=f"Pago compra {compra.id_compra}",
        monto=_to_mxn(Decimal(str(compra.total or 0))),
        tipo="COMPRA_MATERIA_PRIMA",
        id_usuario=id_usuario,
        referencia=f"COMPRA-{compra.id_compra}",
        referencia_tipo="COMPRA",
        referencia_id=compra.id_compra,
    )
    db.session.add(salida)
    recalcular_costos_productos_afectados_por_materias(
        ids_materia=[d.id_materia_prima for d in compra.detalles]
    )
    db.session.commit()
