from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, Index, UniqueConstraint, inspect, text

from app.extensions import bcrypt, db, login_manager

ESTADOS_PAGO = ("PENDIENTE", "PAGADO")
TIPOS_MOVIMIENTO = ("ENTRADA", "SALIDA", "AJUSTE")
ESTADOS_SOLICITUD = ("PENDIENTE", "APROBADA", "RECHAZADA")
ESTADOS_ORDEN = ("PENDIENTE", "EN_PROCESO", "FINALIZADO", "CANCELADO")
ESTADOS_VENTA = ("CONFIRMADO", "EN_PROCESO_PRODUCCION")
ESTADOS_PEDIDO = ("PENDIENTE", "CONFIRMADO", "PAGADO", "ENTREGADO", "CANCELADO")
TIPOS_PAGO = ("EFECTIVO", "TARJETA")
TIPOS_PAGO_PEDIDO = ("EN_LINEA", "CONTRA_ENTREGA")
FK_USUARIO = "usuario.id_usuario"
FK_UNIDAD = "unidad_medida.id_unidad"
FK_MATERIA = "materia_prima.id_materia"
FK_PRODUCTO = "producto.id_producto"
CASCADE_DELETE_ORPHAN = "all, delete-orphan"


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def utc_today() -> date:
    return utc_now().date()


class TimestampMixin:
    fecha_creacion = db.Column(db.DateTime, default=utc_now, nullable=False)


class Rol(TimestampMixin, db.Model):
    __tablename__ = "rol"

    id_rol = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), unique=True, nullable=False)
    descripcion = db.Column(db.String(255), nullable=False)
    activo = db.Column(db.Boolean, default=True, nullable=False)
    es_base = db.Column(db.Boolean, default=False, nullable=False)

    usuarios = db.relationship("Usuario", back_populates="rol", lazy="dynamic")
    permisos = db.relationship("Permiso", back_populates="rol", lazy="dynamic")


class Modulo(db.Model):
    __tablename__ = "modulo"

    id_modulo = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(80), unique=True, nullable=False)
    activo = db.Column(db.Boolean, default=True, nullable=False)


class Permiso(db.Model):
    __tablename__ = "permiso"
    __table_args__ = (
        UniqueConstraint("id_rol", "id_modulo", name="uq_permiso_rol_modulo"),
    )

    id_permiso = db.Column(db.Integer, primary_key=True)
    id_rol = db.Column(db.Integer, db.ForeignKey("rol.id_rol"), nullable=False)
    id_modulo = db.Column(db.Integer, db.ForeignKey("modulo.id_modulo"), nullable=False)
    lectura = db.Column(db.Boolean, default=False, nullable=False)
    escritura = db.Column(db.Boolean, default=False, nullable=False)
    actualizacion = db.Column(db.Boolean, default=False, nullable=False)
    eliminacion = db.Column(db.Boolean, default=False, nullable=False)

    rol = db.relationship("Rol", back_populates="permisos")
    modulo = db.relationship("Modulo")


class Persona(TimestampMixin, db.Model):
    __tablename__ = "persona"

    id_persona = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    apellidos = db.Column(db.String(120), nullable=False)
    telefono = db.Column(db.String(30), nullable=False)
    correo = db.Column(db.String(120), unique=True, nullable=False)
    direccion = db.Column(db.String(255), nullable=False)
    ciudad = db.Column(db.String(120), nullable=False)

    usuario = db.relationship("Usuario", back_populates="persona", uselist=False)


class Usuario(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "usuario"

    id_usuario = db.Column(db.Integer, primary_key=True)
    id_persona = db.Column(
        db.Integer, db.ForeignKey("persona.id_persona"), unique=True, nullable=False
    )
    id_rol = db.Column(db.Integer, db.ForeignKey("rol.id_rol"), nullable=False)
    username = db.Column(db.String(60), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    intentos_fallidos = db.Column(db.Integer, default=0, nullable=False)
    bloqueado_hasta = db.Column(db.DateTime, nullable=True)
    ultimo_acceso = db.Column(db.DateTime, nullable=True)
    token_2fa = db.Column(db.String(10), nullable=True)
    expiracion_2fa = db.Column(db.DateTime, nullable=True)
    activo = db.Column(db.Boolean, default=True, nullable=False)

    rol = db.relationship("Rol", back_populates="usuarios")
    persona = db.relationship("Persona", back_populates="usuario")
    bitacora = db.relationship(
        "BitacoraAcceso", back_populates="usuario", lazy="dynamic"
    )

    def get_id(self) -> str:
        return str(self.id_usuario)

    def set_password(self, password: str) -> None:
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")

    def check_password(self, password: str) -> bool:
        return bcrypt.check_password_hash(self.password_hash, password)

    def is_locked(self) -> bool:
        return bool(self.bloqueado_hasta and self.bloqueado_hasta > utc_now())

    def register_failed_login(self) -> None:
        self.intentos_fallidos += 1
        if self.intentos_fallidos >= 3:
            self.bloqueado_hasta = utc_now() + timedelta(minutes=15)

    def reset_login_attempts(self) -> None:
        self.intentos_fallidos = 0
        self.bloqueado_hasta = None


@login_manager.user_loader
def load_user(user_id: str) -> Usuario | None:
    return Usuario.query.get(int(user_id))


class BitacoraAcceso(db.Model):
    __tablename__ = "bitacora_acceso"

    id_bitacora = db.Column(db.Integer, primary_key=True)
    id_usuario = db.Column(db.Integer, db.ForeignKey(FK_USUARIO), nullable=False)
    fecha = db.Column(db.DateTime, default=utc_now, nullable=False)
    exitoso = db.Column(db.Boolean, nullable=False)
    error_mensaje = db.Column(db.String(255), nullable=True)

    usuario = db.relationship("Usuario", back_populates="bitacora")


class Proveedor(TimestampMixin, db.Model):
    __tablename__ = "proveedor"

    id_proveedor = db.Column(db.Integer, primary_key=True)
    nombre_empresa = db.Column(db.String(120), unique=True, nullable=False)
    nombre_contacto = db.Column(db.String(120), nullable=False)
    telefono = db.Column(db.String(30), nullable=False)
    correo = db.Column(db.String(120), unique=True, nullable=False)
    ciudad = db.Column(db.String(120), nullable=False)
    estado = db.Column(db.String(120), nullable=False)
    direccion = db.Column(db.String(255), nullable=False)
    activo = db.Column(db.Boolean, default=True, nullable=False)
    fecha_alta = db.Column(db.DateTime, default=utc_now, nullable=False)

    @property
    def nombre_proveedor(self) -> str:
        return self.nombre_empresa

    @property
    def estado_proveedor(self) -> str:
        return "activo" if self.activo else "inactivo"

    @property
    def id_proveedor_formateado(self) -> str:
        if not self.id_proveedor:
            return "PROV-000"
        return f"PROV-{self.id_proveedor:03d}"


class UnidadMedida(db.Model):
    __tablename__ = "unidad_medida"
    __table_args__ = (
        CheckConstraint(
            "factor_base > 0", name="ck_unidad_medida_factor_base_positivo"
        ),
    )

    id_unidad = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(80), unique=True, nullable=False)
    abreviatura = db.Column(db.String(20), unique=True, nullable=False)
    dimension = db.Column(db.String(20), default="CONTEO", nullable=False)
    factor_base = db.Column(db.Numeric(12, 4), default=1, nullable=False)


class MateriaPrima(TimestampMixin, db.Model):
    __tablename__ = "materia_prima"
    __table_args__ = (
        CheckConstraint("cantidad_disponible >= 0", name="ck_mp_cantidad_no_negativa"),
        CheckConstraint(
            "factor_conversion > 0", name="ck_mp_factor_conversion_positiva"
        ),
        CheckConstraint(
            "porcentaje_merma >= 0", name="ck_mp_porcentaje_merma_no_negativo"
        ),
        CheckConstraint("stock_minimo >= 0", name="ck_mp_stock_minimo_no_negativo"),
    )

    id_materia = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), unique=True, nullable=False)
    id_unidad_base = db.Column(db.Integer, db.ForeignKey(FK_UNIDAD), nullable=False)
    id_unidad_compra = db.Column(db.Integer, db.ForeignKey(FK_UNIDAD), nullable=False)
    factor_conversion = db.Column(db.Numeric(12, 4), nullable=False)
    porcentaje_merma = db.Column(db.Numeric(5, 2), default=0, nullable=False)
    costo_unitario = db.Column(db.Numeric(12, 4), default=0, nullable=False)
    stock_minimo = db.Column(db.Numeric(12, 4), default=0, nullable=False)
    cantidad_disponible = db.Column(db.Numeric(12, 4), default=0, nullable=False)
    activa = db.Column(db.Boolean, default=True, nullable=False)

    unidad_base = db.relationship("UnidadMedida", foreign_keys=[id_unidad_base])
    unidad_compra = db.relationship("UnidadMedida", foreign_keys=[id_unidad_compra])

    @property
    def esta_bajo_minimo(self) -> bool:
        return Decimal(str(self.cantidad_disponible)) < Decimal(str(self.stock_minimo))

    @property
    def estado_stock(self) -> str:
        if not self.activa:
            return "INACTIVA"
        if self.esta_bajo_minimo:
            return "CRITICO"
        minimo = Decimal(str(self.stock_minimo))
        if minimo > 0 and Decimal(str(self.cantidad_disponible)) < (minimo * Decimal("1.5")):
            return "BAJO"
        return "OK"


class MovimientoInventarioMP(db.Model):
    __tablename__ = "movimiento_inventario_mp"
    __table_args__ = (
        CheckConstraint("cantidad > 0", name="ck_movimiento_mp_cantidad_positiva"),
        Index("ix_movimiento_mp_materia_fecha", "id_materia_prima", "fecha"),
    )

    id_movimiento = db.Column(db.Integer, primary_key=True)
    id_materia_prima = db.Column(db.Integer, db.ForeignKey(FK_MATERIA), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    cantidad = db.Column(db.Numeric(12, 4), nullable=False)
    fecha = db.Column(db.DateTime, default=utc_now, nullable=False)
    id_usuario = db.Column(db.Integer, db.ForeignKey(FK_USUARIO), nullable=False)
    referencia_id = db.Column(db.String(50), nullable=True)

    materia_prima = db.relationship("MateriaPrima")
    usuario = db.relationship("Usuario")


class Compra(TimestampMixin, db.Model):
    __tablename__ = "compra"

    id_compra = db.Column(db.Integer, primary_key=True)
    id_proveedor = db.Column(
        db.Integer, db.ForeignKey("proveedor.id_proveedor"), nullable=False
    )
    id_usuario_comprador = db.Column(
        db.Integer, db.ForeignKey(FK_USUARIO), nullable=False
    )
    fecha = db.Column(db.DateTime, default=utc_now, nullable=False)
    total = db.Column(db.Numeric(12, 2), default=0, nullable=False)
    estado_pago = db.Column(db.String(20), default="PENDIENTE", nullable=False)

    proveedor = db.relationship("Proveedor")
    comprador = db.relationship("Usuario")
    detalles = db.relationship(
        "DetalleCompra", back_populates="compra", cascade=CASCADE_DELETE_ORPHAN
    )

    @property
    def folio_formateado(self) -> str:
        if not self.id_compra:
            return "C-000"
        return f"C-{self.id_compra:03d}"

    @property
    def resumen_materias(self) -> str:
        nombres = [
            detalle.materia_prima.nombre
            for detalle in self.detalles[:3]
            if detalle.materia_prima
        ]
        if not nombres:
            return "Sin detalle"
        if len(self.detalles) > 3:
            return f"{', '.join(nombres)} y {len(self.detalles) - 3} más"
        return ", ".join(nombres)


class DetalleCompra(db.Model):
    __tablename__ = "detalle_compra"
    __table_args__ = (
        CheckConstraint(
            "cantidad_comprada > 0", name="ck_detalle_compra_cantidad_positiva"
        ),
        CheckConstraint(
            "precio_unitario >= 0", name="ck_detalle_compra_precio_no_negativo"
        ),
        CheckConstraint(
            "cantidad_base > 0", name="ck_detalle_compra_cantidad_base_positiva"
        ),
    )

    id_detalle = db.Column(db.Integer, primary_key=True)
    id_compra = db.Column(db.Integer, db.ForeignKey("compra.id_compra"), nullable=False)
    id_materia_prima = db.Column(db.Integer, db.ForeignKey(FK_MATERIA), nullable=False)
    cantidad_comprada = db.Column(db.Numeric(12, 4), nullable=False)
    id_unidad_compra = db.Column(db.Integer, db.ForeignKey(FK_UNIDAD), nullable=False)
    precio_unitario = db.Column(db.Numeric(12, 2), nullable=False)
    subtotal = db.Column(db.Numeric(12, 2), nullable=False)
    cantidad_base = db.Column(db.Numeric(12, 4), nullable=False)

    compra = db.relationship("Compra", back_populates="detalles")
    materia_prima = db.relationship("MateriaPrima")
    unidad_compra = db.relationship("UnidadMedida")


class Producto(TimestampMixin, db.Model):
    __tablename__ = "producto"
    __table_args__ = (
        CheckConstraint(
            "cantidad_disponible >= 0", name="ck_producto_stock_no_negativo"
        ),
        CheckConstraint(
            "cantidad_reservada >= 0", name="ck_producto_reserva_no_negativa"
        ),
        CheckConstraint(
            "cantidad_reservada <= cantidad_disponible",
            name="ck_producto_reserva_no_supera_stock",
        ),
        CheckConstraint(
            "stock_minimo >= 0", name="ck_producto_stock_minimo_no_negativo"
        ),
        CheckConstraint(
            "costo_produccion_actual >= 0",
            name="ck_producto_costo_produccion_no_negativo",
        ),
        CheckConstraint(
            "margen_objetivo_pct > 0 AND margen_objetivo_pct < 100",
            name="ck_producto_margen_objetivo_rango",
        ),
        CheckConstraint(
            "precio_sugerido IS NULL OR precio_sugerido >= 0",
            name="ck_producto_precio_sugerido_no_negativo",
        ),
    )

    id_producto = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), unique=True, nullable=False)
    descripcion = db.Column(db.String(255), nullable=False)
    precio_venta = db.Column(db.Numeric(12, 2), nullable=False)
    unidad_venta = db.Column(db.String(20), default="Pieza", nullable=False)
    cantidad_disponible = db.Column(db.Integer, default=0, nullable=False)
    cantidad_reservada = db.Column(db.Integer, default=0, nullable=False)
    stock_minimo = db.Column(db.Integer, default=0, nullable=False)
    costo_produccion_actual = db.Column(db.Numeric(12, 2), default=0, nullable=False)
    margen_objetivo_pct = db.Column(db.Numeric(5, 2), default=25, nullable=False)
    precio_sugerido = db.Column(db.Numeric(12, 2), nullable=True)
    fecha_costo_actualizado = db.Column(db.DateTime, nullable=True)
    id_receta = db.Column(db.Integer, db.ForeignKey("receta.id_receta"), nullable=True)
    activo = db.Column(db.Boolean, default=True, nullable=False)
    imagen = db.Column(db.String(255), nullable=True)
    fecha_actualizacion = db.Column(
        db.DateTime, default=utc_now, onupdate=utc_now, nullable=True
    )

    receta_base = db.relationship("Receta", foreign_keys=[id_receta])

    @property
    def cantidad_libre(self) -> int:
        return max(
            int(self.cantidad_disponible or 0) - int(self.cantidad_reservada or 0),
            0,
        )


class Receta(TimestampMixin, db.Model):
    __tablename__ = "receta"
    __table_args__ = (
        UniqueConstraint("nombre", "version", name="uq_receta_nombre_version"),
    )

    id_receta = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    descripcion = db.Column(db.String(255), nullable=True)
    unidad_produccion = db.Column(db.String(50), default="pieza", nullable=False)
    categoria = db.Column(db.String(50), nullable=True)
    version = db.Column(db.Integer, nullable=False)
    rendimiento_base = db.Column(db.Numeric(12, 4), nullable=False)
    activa = db.Column(db.Boolean, default=True, nullable=False)
    fecha_creacion = db.Column(db.DateTime, default=utc_now, nullable=False)

    detalles = db.relationship(
        "DetalleReceta", back_populates="receta", cascade=CASCADE_DELETE_ORPHAN
    )


class DetalleReceta(db.Model):
    __tablename__ = "detalle_receta"
    __table_args__ = (
        CheckConstraint(
            "cantidad_base > 0", name="ck_detalle_receta_cantidad_positiva"
        ),
    )

    id_detalle = db.Column(db.Integer, primary_key=True)
    id_receta = db.Column(db.Integer, db.ForeignKey("receta.id_receta"), nullable=False)
    id_materia_prima = db.Column(db.Integer, db.ForeignKey(FK_MATERIA), nullable=False)
    cantidad_base = db.Column(db.Numeric(12, 4), nullable=False)

    receta = db.relationship("Receta", back_populates="detalles")
    materia_prima = db.relationship("MateriaPrima")


class SolicitudProduccion(TimestampMixin, db.Model):
    __tablename__ = "solicitud_produccion"
    __table_args__ = (
        CheckConstraint("cantidad > 0", name="ck_solicitud_cantidad_positiva"),
    )

    id_solicitud = db.Column(db.Integer, primary_key=True)
    id_producto = db.Column(db.Integer, db.ForeignKey(FK_PRODUCTO), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    estado = db.Column(db.String(20), default="PENDIENTE", nullable=False)
    fecha_solicitud = db.Column(db.DateTime, default=utc_now, nullable=False)
    id_usuario_solicita = db.Column(
        db.Integer, db.ForeignKey(FK_USUARIO), nullable=False
    )
    id_usuario_resuelve = db.Column(
        db.Integer, db.ForeignKey(FK_USUARIO), nullable=True
    )
    observaciones = db.Column(db.String(255), nullable=True)

    producto = db.relationship("Producto")


class OrdenProduccion(TimestampMixin, db.Model):
    __tablename__ = "orden_produccion"
    __table_args__ = (
        CheckConstraint("cantidad_producir > 0", name="ck_orden_cantidad_positiva"),
    )

    id_orden = db.Column(db.Integer, primary_key=True)
    id_solicitud = db.Column(
        db.Integer, db.ForeignKey("solicitud_produccion.id_solicitud"), nullable=False
    )
    id_receta = db.Column(db.Integer, db.ForeignKey("receta.id_receta"), nullable=False)
    id_producto = db.Column(db.Integer, db.ForeignKey(FK_PRODUCTO), nullable=False)
    cantidad_producir = db.Column(db.Integer, nullable=False)
    estado = db.Column(db.String(20), default="PENDIENTE", nullable=False)
    fecha_inicio = db.Column(db.DateTime, nullable=True)
    fecha_fin = db.Column(db.DateTime, nullable=True)
    id_usuario_responsable = db.Column(
        db.Integer, db.ForeignKey(FK_USUARIO), nullable=False
    )
    costo_total = db.Column(db.Numeric(12, 2), default=0, nullable=False)

    solicitud = db.relationship("SolicitudProduccion")
    receta = db.relationship("Receta")
    producto = db.relationship("Producto")


class Carrito(TimestampMixin, db.Model):
    __tablename__ = "carrito"

    id_carrito = db.Column(db.Integer, primary_key=True)
    id_usuario_cliente = db.Column(
        db.Integer, db.ForeignKey(FK_USUARIO), nullable=False
    )
    fecha_creacion = db.Column(db.DateTime, default=utc_now, nullable=False)

    usuario_cliente = db.relationship("Usuario")
    detalles = db.relationship(
        "DetalleCarrito", back_populates="carrito", cascade=CASCADE_DELETE_ORPHAN
    )


class DetalleCarrito(db.Model):
    __tablename__ = "detalle_carrito"
    __table_args__ = (
        CheckConstraint(
            "cantidad >= 1 AND cantidad <= 5", name="ck_carrito_cantidad_1_5"
        ),
    )

    id_detalle = db.Column(db.Integer, primary_key=True)
    id_carrito = db.Column(
        db.Integer, db.ForeignKey("carrito.id_carrito"), nullable=False
    )
    id_producto = db.Column(db.Integer, db.ForeignKey(FK_PRODUCTO), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)

    carrito = db.relationship("Carrito", back_populates="detalles")
    producto = db.relationship("Producto")


class Pedido(TimestampMixin, db.Model):
    __tablename__ = "pedido"

    id_pedido = db.Column(db.Integer, primary_key=True)
    id_usuario_cliente = db.Column(
        db.Integer, db.ForeignKey(FK_USUARIO), nullable=False
    )
    fecha_pedido = db.Column(db.DateTime, default=utc_now, nullable=False)
    fecha_entrega = db.Column(db.Date, nullable=False)
    total = db.Column(db.Numeric(12, 2), default=0, nullable=False)
    estado_pedido = db.Column(db.String(20), default="PENDIENTE", nullable=False)
    estado_pago = db.Column(db.String(20), default="PENDIENTE", nullable=False)
    tipo_pago = db.Column(db.String(20), nullable=False)  # EN_LINEA / CONTRA_ENTREGA
    referencia_pago = db.Column(db.String(120), nullable=True)

    usuario_cliente = db.relationship("Usuario")
    detalles = db.relationship(
        "DetallePedido", back_populates="pedido", cascade=CASCADE_DELETE_ORPHAN
    )
    pago = db.relationship(
        "PagoPedido",
        back_populates="pedido",
        uselist=False,
        cascade=CASCADE_DELETE_ORPHAN,
    )


class DetallePedido(db.Model):
    __tablename__ = "detalle_pedido"
    __table_args__ = (
        CheckConstraint(
            "cantidad >= 1 AND cantidad <= 5", name="ck_pedido_cantidad_1_5"
        ),
    )

    id_detalle = db.Column(db.Integer, primary_key=True)
    id_pedido = db.Column(db.Integer, db.ForeignKey("pedido.id_pedido"), nullable=False)
    id_producto = db.Column(db.Integer, db.ForeignKey(FK_PRODUCTO), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    precio_unitario = db.Column(db.Numeric(12, 2), nullable=False)
    subtotal = db.Column(db.Numeric(12, 2), nullable=False)

    pedido = db.relationship("Pedido", back_populates="detalles")
    producto = db.relationship("Producto")


class PagoPedido(db.Model):
    __tablename__ = "pago_pedido"

    id_pago = db.Column(db.Integer, primary_key=True)
    id_pedido = db.Column(
        db.Integer, db.ForeignKey("pedido.id_pedido"), unique=True, nullable=False
    )
    estado_pago = db.Column(db.String(20), default="PENDIENTE", nullable=False)
    tipo_pago = db.Column(db.String(20), nullable=False)  # EFECTIVO / TARJETA
    referencia = db.Column(db.String(120), nullable=True)
    fecha_pago = db.Column(db.DateTime, nullable=True)

    pedido = db.relationship("Pedido", back_populates="pago")


class Venta(TimestampMixin, db.Model):
    __tablename__ = "venta"

    id_venta = db.Column(db.Integer, primary_key=True)
    id_pedido = db.Column(db.Integer, db.ForeignKey("pedido.id_pedido"), nullable=True)
    id_usuario_cliente = db.Column(
        db.Integer, db.ForeignKey(FK_USUARIO), nullable=False
    )
    fecha = db.Column(db.DateTime, default=utc_now, nullable=False)
    total = db.Column(db.Numeric(12, 2), default=0, nullable=False)
    estado = db.Column(db.String(40), default="EN_PROCESO_PRODUCCION", nullable=False)
    tipo_pago = db.Column(db.String(20), default="EFECTIVO", nullable=False)
    requiere_ticket = db.Column(db.Boolean, default=False, nullable=False)

    usuario_cliente = db.relationship("Usuario")
    pedido = db.relationship("Pedido")
    detalles = db.relationship(
        "DetalleVenta", back_populates="venta", cascade=CASCADE_DELETE_ORPHAN
    )


class TicketVenta(db.Model):
    __tablename__ = "ticket_venta"

    id_ticket = db.Column(db.Integer, primary_key=True)
    id_venta = db.Column(
        db.Integer, db.ForeignKey("venta.id_venta"), unique=True, nullable=False
    )
    folio = db.Column(db.String(30), unique=True, nullable=False)
    fecha = db.Column(db.DateTime, default=utc_now, nullable=False)

    venta = db.relationship("Venta")


class DetalleVenta(db.Model):
    __tablename__ = "detalle_venta"

    id_detalle = db.Column(db.Integer, primary_key=True)
    id_venta = db.Column(db.Integer, db.ForeignKey("venta.id_venta"), nullable=False)
    id_producto = db.Column(db.Integer, db.ForeignKey(FK_PRODUCTO), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    precio_unitario = db.Column(db.Numeric(12, 2), nullable=False)
    costo_unitario_produccion = db.Column(db.Numeric(12, 2), nullable=True)
    utilidad_unitaria = db.Column(db.Numeric(12, 2), nullable=True)
    subtotal = db.Column(db.Numeric(12, 2), nullable=False)

    venta = db.relationship("Venta", back_populates="detalles")
    producto = db.relationship("Producto")


class SalidaEfectivo(TimestampMixin, db.Model):
    __tablename__ = "salida_efectivo"

    id_salida = db.Column(db.Integer, primary_key=True)
    concepto = db.Column(db.String(150), nullable=False)
    monto = db.Column(db.Numeric(12, 2), nullable=False)
    tipo = db.Column(db.String(50), nullable=False)
    id_usuario = db.Column(db.Integer, db.ForeignKey(FK_USUARIO), nullable=False)
    referencia = db.Column(db.String(80), nullable=True)


class CorteDiario(TimestampMixin, db.Model):
    __tablename__ = "corte_diario"

    id_corte = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.Date, default=utc_today, nullable=False)
    total_ventas = db.Column(db.Numeric(12, 2), default=0, nullable=False)
    numero_ventas = db.Column(db.Integer, default=0, nullable=False)
    utilidad_diaria = db.Column(db.Numeric(12, 2), default=0, nullable=False)
    salida_efectivo_proveedores = db.Column(
        db.Numeric(12, 2), default=0, nullable=False
    )
    id_usuario = db.Column(db.Integer, db.ForeignKey(FK_USUARIO), nullable=False)


def seed_base_catalog_data() -> None:
    roles_base = (
        ("Administrador", "Acceso total al sistema."),
        ("Ventas", "Gestiona ventas y solicitudes de produccion."),
        ("Produccion", "Gestiona ordenes y recetas."),
        ("Cliente", "Compra y consulta pedidos web."),
    )
    for nombre, descripcion in roles_base:
        role = Rol.query.filter_by(nombre=nombre).first()
        if not role:
            db.session.add(
                Rol(nombre=nombre, descripcion=descripcion, es_base=True, activo=True)
            )

    modulos_base = (
        "Dashboard",
        "Usuarios",
        "Roles",
        "Proveedores",
        "Inventario MP",
        "Compras MP",
        "Recetas",
        "Ordenes",
        "Solicitudes",
        "Producto Terminado",
        "Ventas",
        "Costos y Utilidad",
        "Pedidos Clientes",
        "Catalogo Web",
        "Carrito",
    )
    for nombre in modulos_base:
        exists = Modulo.query.filter_by(nombre=nombre).first()
        if not exists:
            db.session.add(Modulo(nombre=nombre))

    unidades_base = (
        ("Kilogramo", "kg", "MASA", Decimal("1000")),
        ("Gramo", "g", "MASA", Decimal("1")),
        ("Litro", "l", "VOLUMEN", Decimal("1000")),
        ("Mililitro", "ml", "VOLUMEN", Decimal("1")),
        ("Pieza", "pza", "CONTEO", Decimal("1")),
        ("Costal", "cos", "MASA", Decimal("25000")),
    )

    inspector = inspect(db.engine)
    columnas_unidad = {
        column["name"] for column in inspector.get_columns("unidad_medida")
    }
    tiene_dimension_y_factor = {
        "dimension",
        "factor_base",
    }.issubset(columnas_unidad)

    for nombre, abreviatura, dimension, factor_base in unidades_base:
        existente = db.session.execute(
            text(
                """
                SELECT id_unidad
                FROM unidad_medida
                WHERE abreviatura = :abreviatura
                LIMIT 1
                """
            ),
            {"abreviatura": abreviatura},
        ).first()

        if not existente:
            if tiene_dimension_y_factor:
                db.session.execute(
                    text(
                        """
                        INSERT INTO unidad_medida
                            (nombre, abreviatura, dimension, factor_base)
                        VALUES
                            (:nombre, :abreviatura, :dimension, :factor_base)
                        """
                    ),
                    {
                        "nombre": nombre,
                        "abreviatura": abreviatura,
                        "dimension": dimension,
                        "factor_base": factor_base,
                    },
                )
            else:
                db.session.execute(
                    text(
                        """
                        INSERT INTO unidad_medida (nombre, abreviatura)
                        VALUES (:nombre, :abreviatura)
                        """
                    ),
                    {
                        "nombre": nombre,
                        "abreviatura": abreviatura,
                    },
                )
            continue

        if tiene_dimension_y_factor:
            db.session.execute(
                text(
                    """
                    UPDATE unidad_medida
                    SET nombre = :nombre,
                        dimension = :dimension,
                        factor_base = :factor_base
                    WHERE abreviatura = :abreviatura
                    """
                ),
                {
                    "nombre": nombre,
                    "dimension": dimension,
                    "factor_base": factor_base,
                    "abreviatura": abreviatura,
                },
            )
        else:
            db.session.execute(
                text(
                    """
                    UPDATE unidad_medida
                    SET nombre = :nombre
                    WHERE abreviatura = :abreviatura
                    """
                ),
                {
                    "nombre": nombre,
                    "abreviatura": abreviatura,
                },
            )

    productos_base = (
        (
            "Pastel de Chocolate",
            "Humedo y cremoso, elaborado artesanalmente.",
            Decimal("450.00"),
        ),
        ("Pastel Red Velvet", "Terciopelo rojo clasico con betun.", Decimal("480.00")),
        (
            "Pastel de Frutos",
            "Fruta fresca natural sobre bizcocho suave.",
            Decimal("510.00"),
        ),
        (
            "Pastel Tres Leches",
            "Suave y esponjoso, banado en tres leches.",
            Decimal("390.00"),
        ),
        ("Concha de Vainilla", "Artesanal, horneada cada dia.", Decimal("14.00")),
        (
            "Pay de Fresa",
            "Relleno de crema pastelera y fresa fresca.",
            Decimal("220.00"),
        ),
        (
            "Cuernos de Mantequilla",
            "Hojaldrados con mantequilla francesa.",
            Decimal("28.00"),
        ),
        (
            "Muffin de Vainilla",
            "Muffin suave de vainilla con chispas.",
            Decimal("35.00"),
        ),
        (
            "Brownie de Chocolate",
            "Brownie denso de chocolate intenso.",
            Decimal("42.00"),
        ),
        ("Galleta de Nuez", "Galleta suave y crujiente de nuez.", Decimal("18.00")),
    )
    for nombre, descripcion, precio in productos_base:
        exists = Producto.query.filter_by(nombre=nombre).first()
        if not exists:
            db.session.add(
                Producto(
                    nombre=nombre,
                    descripcion=descripcion,
                    precio_venta=precio,
                    cantidad_disponible=10,
                    stock_minimo=5,
                    activo=True,
                )
            )

    db.session.commit()
