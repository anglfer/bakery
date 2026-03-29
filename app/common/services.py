from __future__ import annotations

from decimal import Decimal

from app.extensions import db
from app.models import (
    Carrito,
    Compra,
    DetalleCarrito,
    DetalleCompra,
    DetallePedido,
    DetalleReceta,
    DetalleVenta,
    MateriaPrima,
    MovimientoInventarioMP,
    OrdenProduccion,
    PagoPedido,
    Pedido,
    Producto,
    Receta,
    SalidaEfectivo,
    SolicitudProduccion,
    TicketVenta,
    Venta,
    utc_now,
)


def registrar_compra(compra: Compra, detalles: list[dict]) -> Compra:
    total = Decimal("0")
    for item in detalles:
        materia = MateriaPrima.query.get(item["id_materia_prima"])
        if not materia:
            raise ValueError("Materia prima no encontrada")

        cantidad_comprada = Decimal(str(item["cantidad_comprada"]))
        precio_unitario = Decimal(str(item["precio_unitario"]))
        subtotal = cantidad_comprada * precio_unitario
        cantidad_base = cantidad_comprada * Decimal(str(materia.factor_conversion))

        detalle = DetalleCompra(
            id_materia_prima=materia.id_materia,
            cantidad_comprada=cantidad_comprada,
            id_unidad_compra=materia.id_unidad_compra,
            precio_unitario=precio_unitario,
            subtotal=subtotal,
            cantidad_base=cantidad_base,
        )
        compra.detalles.append(detalle)

        materia.cantidad_disponible = (
            Decimal(str(materia.cantidad_disponible)) + cantidad_base
        )

        movimiento = MovimientoInventarioMP(
            id_materia_prima=materia.id_materia,
            tipo="ENTRADA",
            cantidad=cantidad_base,
            id_usuario=compra.id_usuario_comprador,
            referencia_id=f"COMPRA-{compra.id_compra or 'NEW'}",
        )
        db.session.add(movimiento)
        total += subtotal

    compra.total = total
    db.session.add(compra)
    db.session.commit()
    return compra


def agregar_producto_a_carrito(
    id_usuario: int, id_producto: int, cantidad: int
) -> None:
    if cantidad < 1 or cantidad > 5:
        raise ValueError("La cantidad por producto en carrito debe estar entre 1 y 5")

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
        detalle.cantidad = nueva_cantidad

    db.session.commit()


def crear_venta_desde_carrito(id_usuario: int, pagado_en_linea: bool) -> Venta:
    carrito = Carrito.query.filter_by(id_usuario_cliente=id_usuario).first()
    if not carrito or not carrito.detalles:
        raise ValueError("No hay productos en el carrito")

    venta = Venta(
        id_usuario_cliente=id_usuario,
        estado="EN_PROCESO_PRODUCCION",
        pagado_en_linea=pagado_en_linea,
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
        db.session.add(
            DetalleVenta(
                id_venta=venta.id_venta,
                id_producto=producto.id_producto,
                cantidad=detalle.cantidad,
                precio_unitario=producto.precio_venta,
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
) -> Pedido:
    carrito = Carrito.query.filter_by(id_usuario_cliente=id_usuario).first()
    if not carrito or not carrito.detalles:
        raise ValueError("No hay productos en el carrito")

    if tipo_pago_pedido not in {"EN_LINEA", "CONTRA_ENTREGA"}:
        raise ValueError("Tipo de pago invalido")

    if tipo_pago not in {"EFECTIVO", "TARJETA"}:
        raise ValueError("Tipo de pago invalido")

    if tipo_pago_pedido == "EN_LINEA" and not referencia_pago:
        raise ValueError("La referencia de pago es obligatoria para pagos en linea")

    # Validar stock al momento de generar pedido (sin descontar inventario aun)
    for detalle in carrito.detalles:
        producto = Producto.query.get(detalle.id_producto)
        if not producto or not producto.activo:
            raise ValueError("Producto no disponible")
        if producto.cantidad_disponible < detalle.cantidad:
            raise ValueError(f"Stock insuficiente para {producto.nombre}")

    pedido = Pedido(
        id_usuario_cliente=id_usuario,
        fecha_entrega=fecha_entrega,
        tipo_pago=tipo_pago_pedido,
        estado_pedido="CONFIRMADO" if tipo_pago_pedido == "EN_LINEA" else "PENDIENTE",
        estado_pago="PAGADO" if tipo_pago_pedido == "EN_LINEA" else "PENDIENTE",
        referencia_pago=referencia_pago,
    )
    db.session.add(pedido)
    db.session.flush()

    total = Decimal("0")
    for detalle in carrito.detalles:
        producto = Producto.query.get(detalle.id_producto)
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

    pedido.total = total
    db.session.add(
        PagoPedido(
            id_pedido=pedido.id_pedido,
            estado_pago=pedido.estado_pago,
            tipo_pago=tipo_pago,
            referencia=referencia_pago,
            fecha_pago=utc_now() if pedido.estado_pago == "PAGADO" else None,
        )
    )

    DetalleCarrito.query.filter_by(id_carrito=carrito.id_carrito).delete()
    db.session.commit()
    return pedido


def generar_venta_desde_pedido(
    *,
    id_pedido: int,
    id_usuario_emite: int,
    requiere_ticket: bool,
) -> Venta:
    pedido = Pedido.query.get(id_pedido)
    if not pedido:
        raise ValueError("Pedido no encontrado")
    if pedido.estado_pedido == "CANCELADO":
        raise ValueError("No se puede generar venta de un pedido cancelado")
    if pedido.estado_pedido != "PAGADO":
        raise ValueError("Solo pedidos PAGADOS pueden entregarse y generar venta")

    # Validar y descontar inventario al momento de entrega/venta
    total = Decimal("0")
    for d in pedido.detalles:
        producto = Producto.query.get(d.id_producto)
        if not producto or not producto.activo:
            raise ValueError("Producto no disponible para entregar")
        if producto.cantidad_disponible < d.cantidad:
            raise ValueError(f"Stock insuficiente para entregar {producto.nombre}")
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

    for d in pedido.detalles:
        producto = Producto.query.get(d.id_producto)
        producto.cantidad_disponible -= d.cantidad
        db.session.add(
            DetalleVenta(
                id_venta=venta.id_venta,
                id_producto=producto.id_producto,
                cantidad=d.cantidad,
                precio_unitario=d.precio_unitario,
                subtotal=d.subtotal,
            )
        )

    # Ticket: siempre se almacena, aunque "requiere_ticket" controle impresion/visualizacion
    folio = f"SB-{venta.id_venta:06d}"
    db.session.add(TicketVenta(id_venta=venta.id_venta, folio=folio))

    pedido.estado_pedido = "ENTREGADO"
    db.session.commit()
    return venta


def actualizar_estado_pedido(
    *,
    id_pedido: int,
    nuevo_estado: str,
    referencia_pago: str | None = None,
) -> Pedido:
    pedido = Pedido.query.get(id_pedido)
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

    # Reglas de transicion basadas en el PDF
    if nuevo_estado == "CANCELADO":
        if pedido.estado_pedido != "PENDIENTE":
            raise ValueError("Solo pedidos PENDIENTE pueden cancelarse")
        pedido.estado_pedido = "CANCELADO"
        db.session.commit()
        return pedido

    if nuevo_estado == "CONFIRMADO":
        if pedido.estado_pedido not in {"PENDIENTE"}:
            raise ValueError("Solo pedidos PENDIENTE pueden confirmarse")
        # Para pedidos en linea, ya vienen confirmados al pagarse
        pedido.estado_pedido = "CONFIRMADO"
        db.session.commit()
        return pedido

    if nuevo_estado == "PAGADO":
        if pedido.estado_pedido not in {"PENDIENTE", "CONFIRMADO"}:
            raise ValueError(
                "Solo pedidos PENDIENTE/CONFIRMADO pueden marcarse como pagados"
            )
        if (
            pedido.tipo_pago == "EN_LINEA"
            and not pedido.referencia_pago
            and referencia_pago
        ):
            pedido.referencia_pago = referencia_pago
        pedido.estado_pago = "PAGADO"
        pedido.estado_pedido = "PAGADO"
        if pedido.pago:
            pedido.pago.estado_pago = "PAGADO"
            if referencia_pago:
                pedido.pago.referencia = referencia_pago
            pedido.pago.fecha_pago = utc_now()
        db.session.commit()
        return pedido

    if nuevo_estado == "ENTREGADO":
        raise ValueError(
            "La entrega genera venta; usa el flujo de entrega para completarla"
        )

    pedido.estado_pedido = nuevo_estado
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


def crear_orden_produccion(
    id_solicitud: int, id_receta: int, cantidad: int, id_usuario: int
) -> OrdenProduccion:
    solicitud = SolicitudProduccion.query.get(id_solicitud)
    if not solicitud:
        raise ValueError("Solicitud no encontrada")
    if solicitud.estado != "APROBADA":
        raise ValueError("Solo solicitudes aprobadas pueden generar orden")

    receta = Receta.query.get(id_receta)
    if not receta or not receta.activa:
        raise ValueError("Receta activa no encontrada")

    producto = Producto.query.get(solicitud.id_producto)
    if not producto or not producto.activo:
        raise ValueError("Producto no disponible para produccion")
    if not producto.id_receta:
        raise ValueError("El producto no tiene receta asignada")
    if int(producto.id_receta) != int(receta.id_receta):
        raise ValueError("La receta seleccionada no corresponde al producto solicitado")

    orden = OrdenProduccion(
        id_solicitud=id_solicitud,
        id_receta=id_receta,
        id_producto=producto.id_producto,
        cantidad_producir=cantidad,
        id_usuario_responsable=id_usuario,
    )
    db.session.add(orden)
    db.session.flush()
    db.session.commit()
    return orden


def iniciar_orden_produccion(*, id_orden: int, id_usuario: int) -> OrdenProduccion:
    orden = OrdenProduccion.query.get(id_orden)
    if not orden:
        raise ValueError("Orden no encontrada")
    if orden.estado != "PENDIENTE":
        raise ValueError("Solo se pueden iniciar ordenes pendientes")

    receta = Receta.query.get(orden.id_receta)
    if not receta or not receta.activa:
        raise ValueError("Receta activa no encontrada")
    if receta.rendimiento_base <= 0:
        raise ValueError("Rendimiento base invalido")

    # Cantidad_real = cantidad_receta * factor * (1 + merma/100)
    # factor = cantidad_a_producir / rendimiento_base
    factor = Decimal(str(orden.cantidad_producir)) / Decimal(
        str(receta.rendimiento_base)
    )
    costo_total_mxn = Decimal("0")

    for detalle in DetalleReceta.query.filter_by(id_receta=receta.id_receta).all():
        materia = MateriaPrima.query.get(detalle.id_materia_prima)
        if not materia or not materia.activa:
            raise ValueError("Materia prima no disponible")

        base = Decimal(str(detalle.cantidad_base)) * factor
        merma_factor = Decimal("1") + (
            Decimal(str(materia.porcentaje_merma)) / Decimal("100")
        )
        real = base * merma_factor

        disponible = Decimal(str(materia.cantidad_disponible))
        if disponible < real:
            raise ValueError(f"Stock insuficiente para {materia.nombre}")

        materia.cantidad_disponible = disponible - real
        db.session.add(
            MovimientoInventarioMP(
                id_materia_prima=materia.id_materia,
                tipo="SALIDA",
                cantidad=real,
                id_usuario=id_usuario,
                referencia_id=f"ORD-{orden.id_orden}",
            )
        )

        costo_unit = Decimal(str(materia.costo_unitario))
        costo_total_mxn += real * costo_unit

    orden.estado = "EN_PROCESO"
    orden.fecha_inicio = utc_now()
    orden.costo_total = costo_total_mxn
    db.session.commit()
    return orden


def pagar_compra(id_compra: int, id_usuario: int) -> None:
    compra = Compra.query.get(id_compra)
    if not compra:
        raise ValueError("Compra no encontrada")
    compra.estado_pago = "PAGADO"

    salida = SalidaEfectivo(
        concepto=f"Pago compra {compra.id_compra}",
        monto=compra.total,
        tipo="COMPRA_MATERIA_PRIMA",
        id_usuario=id_usuario,
        referencia=f"COMPRA-{compra.id_compra}",
    )
    db.session.add(salida)
    db.session.commit()
