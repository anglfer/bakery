from __future__ import annotations

from decimal import Decimal

from app.extensions import db
from app.models import (
    BitacoraAcceso,
    Carrito,
    Compra,
    CorteDiario,
    DetalleCarrito,
    DetalleCompra,
    DetalleReceta,
    DetalleVenta,
    MateriaPrima,
    Modulo,
    MovimientoInventarioMP,
    OrdenProduccion,
    Permiso,
    Persona,
    Producto,
    Proveedor,
    Receta,
    Rol,
    SalidaEfectivo,
    SolicitudProduccion,
    UnidadMedida,
    Usuario,
    Venta,
    utc_now,
    utc_today,
)

ROLE_ADMIN = "Administrador"
ROLE_SALES = "Ventas"
ROLE_PRODUCTION = "Produccion"
ROLE_CLIENT = "Cliente"

MP_HARINA = "Harina de Trigo"
MP_AZUCAR = "Azucar Refinada"
MP_MANTEQUILLA = "Mantequilla s/sal"
MP_HUEVO = "Huevo Fresco"


def seed_full_data() -> None:
    _seed_roles_modules_permissions()
    users = _seed_people_and_users()
    _seed_suppliers()
    _seed_raw_materials()
    _seed_recipes()
    _seed_purchase_and_inventory(users)
    _seed_production_flow(users)
    _seed_customer_flow(users)
    _seed_access_logs(users)
    db.session.commit()


def _seed_roles_modules_permissions() -> None:
    role_permissions = {
        ROLE_ADMIN: {
            "leer": True,
            "crear": True,
            "editar": True,
            "desactivar": True,
        },
        ROLE_SALES: {
            "Dashboard": (True, False, False, False),
            "Ventas": (True, True, True, False),
            "Solicitudes": (True, True, True, False),
            "Pedidos Clientes": (True, True, True, False),
            "Catalogo Web": (True, True, True, False),
            "Carrito": (True, True, True, True),
            "Producto Terminado": (True, False, False, False),
            "Compras MP": (True, True, True, False),
            "Costos y Utilidad": (True, False, False, False),
        },
        ROLE_PRODUCTION: {
            "Dashboard": (True, False, False, False),
            "Recetas": (True, True, True, True),
            "Ordenes": (True, True, True, True),
            "Solicitudes": (True, True, True, False),
            "Inventario MP": (True, True, True, False),
            "Producto Terminado": (True, True, True, False),
        },
        ROLE_CLIENT: {
            "Catalogo Web": (True, True, False, False),
            "Carrito": (True, True, True, True),
            "Pedidos Clientes": (True, True, False, True),
        },
    }

    modules = {module.nombre: module for module in Modulo.query.all()}
    roles = {role.nombre: role for role in Rol.query.all()}
    if not roles or not modules:
        return

    for role_name, permission_data in role_permissions.items():
        role = roles.get(role_name)
        if not role:
            continue

        if role_name == ROLE_ADMIN:
            for module in modules.values():
                _upsert_permission(
                    role.id_rol,
                    module.id_modulo,
                    True,
                    True,
                    True,
                    True,
                )
            continue

        for module_name, flags in permission_data.items():
            module = modules.get(module_name)
            if not module:
                continue
            lectura, escritura, actualizacion, eliminacion = flags
            _upsert_permission(
                role.id_rol,
                module.id_modulo,
                lectura,
                escritura,
                actualizacion,
                eliminacion,
            )

    db.session.flush()


def _upsert_permission(
    id_rol: int,
    id_modulo: int,
    lectura: bool,
    escritura: bool,
    actualizacion: bool,
    eliminacion: bool,
) -> None:
    existing = Permiso.query.filter_by(
        id_rol=id_rol,
        id_modulo=id_modulo,
    ).first()
    if existing:
        existing.lectura = lectura
        existing.escritura = escritura
        existing.actualizacion = actualizacion
        existing.eliminacion = eliminacion
        return

    db.session.add(
        Permiso(
            id_rol=id_rol,
            id_modulo=id_modulo,
            lectura=lectura,
            escritura=escritura,
            actualizacion=actualizacion,
            eliminacion=eliminacion,
        )
    )


def _seed_people_and_users() -> dict[str, Usuario]:
    users_data = [
        {
            "username": "admin",
            "pwd": "Admin@123",
            "rol": ROLE_ADMIN,
            "nombre": "Carlos",
            "apellidos": "Mendoza",
            "telefono": "4771001000",
            "correo": "prime@softbakery.local",
            "direccion": "Av. Principal 100",
            "ciudad": "Leon",
        },
        {
            "username": "ventas",
            "pwd": "Ventas@123",
            "rol": ROLE_SALES,
            "nombre": "Ana",
            "apellidos": "Garcia",
            "telefono": "4771002000",
            "correo": "ventas@softbakery.local",
            "direccion": "Blvd. Norte 55",
            "ciudad": "Leon",
        },
        {
            "username": "produccion",
            "pwd": "Produccion@123",
            "rol": ROLE_PRODUCTION,
            "nombre": "Luis",
            "apellidos": "Ruiz",
            "telefono": "4771003000",
            "correo": "produccion@softbakery.local",
            "direccion": "Col. Centro 12",
            "ciudad": "Leon",
        },
        {
            "username": "cliente",
            "pwd": "Cliente@123",
            "rol": ROLE_CLIENT,
            "nombre": "Laura",
            "apellidos": "Martinez",
            "telefono": "4771004000",
            "correo": "cliente@softbakery.local",
            "direccion": "Jardin 88",
            "ciudad": "Leon",
        },
    ]

    out: dict[str, Usuario] = {}
    roles = {r.nombre: r for r in Rol.query.all()}

    for item in users_data:
        user = Usuario.query.filter_by(username=item["username"]).first()
        if user:
            out[item["rol"]] = user
            continue

        persona = Persona.query.filter_by(correo=item["correo"]).first()
        if not persona:
            persona = Persona(
                nombre=item["nombre"],
                apellidos=item["apellidos"],
                telefono=item["telefono"],
                correo=item["correo"],
                direccion=item["direccion"],
                ciudad=item["ciudad"],
            )
            db.session.add(persona)
            db.session.flush()

        user = Usuario(
            id_persona=persona.id_persona,
            id_rol=roles[item["rol"]].id_rol,
            username=item["username"],
            activo=True,
            ultimo_acceso=utc_now(),
        )
        user.set_password(item["pwd"])
        db.session.add(user)
        db.session.flush()
        out[item["rol"]] = user

    return out


def _seed_suppliers() -> None:
    suppliers = [
        (
            "Harinera del Norte",
            "4772001000",
            "harinera@proveedor.local",
            "Parque Industrial 1",
        ),
        (
            "Lacteos Premium SA",
            "4772002000",
            "lacteos@proveedor.local",
            "Zona Sur 23",
        ),
        (
            "Insumos Reposteria MX",
            "4772003000",
            "insumos@proveedor.local",
            "Av. Comercio 300",
        ),
    ]
    for nombre, telefono, correo, direccion in suppliers:
        exists = Proveedor.query.filter_by(nombre_empresa=nombre).first()
        if exists:
            continue
        db.session.add(
            Proveedor(
                nombre_empresa=nombre,
                telefono=telefono,
                correo=correo,
                direccion=direccion,
                activo=True,
            )
        )
    db.session.flush()


def _seed_raw_materials() -> None:
    units = {u.abreviatura: u for u in UnidadMedida.query.all()}
    materials = [
        # name, unidad_base, unidad_compra, factor_conversion, merma%, stock_minimo, stock_inicial, costo_unitario(MXN por unidad_base)
        (MP_HARINA, "g", "cos", "25000", "2.0", "10000", "2000", "0.03"),
        (MP_AZUCAR, "g", "cos", "25000", "0.5", "5000", "18000", "0.028"),
        (MP_MANTEQUILLA, "g", "kg", "1000", "1.5", "2000", "800", "0.16"),
        ("Cacao en Polvo", "g", "kg", "1000", "1.0", "3000", "3200", "0.22"),
        ("Leche Entera", "ml", "l", "1000", "0.0", "2000", "4500", "0.02"),
        (MP_HUEVO, "pza", "pza", "1", "0.0", "30", "48", "4.00"),
        ("Vainilla Extracto", "ml", "l", "1000", "0.0", "500", "600", "0.35"),
        ("Polvo de Hornear", "g", "kg", "1000", "0.0", "800", "900", "0.05"),
        ("Fresas Frescas", "g", "kg", "1000", "8.0", "1000", "1200", "0.09"),
        ("Nuez Pecana", "g", "kg", "1000", "3.0", "600", "700", "0.25"),
    ]

    for name, base_u, buy_u, factor, merma, minimo, stock, costo in materials:
        exists = MateriaPrima.query.filter_by(nombre=name).first()
        if exists:
            continue
        db.session.add(
            MateriaPrima(
                nombre=name,
                id_unidad_base=units[base_u].id_unidad,
                id_unidad_compra=units[buy_u].id_unidad,
                factor_conversion=Decimal(factor),
                porcentaje_merma=Decimal(merma),
                costo_unitario=Decimal(costo),
                stock_minimo=Decimal(minimo),
                cantidad_disponible=Decimal(stock),
                activa=True,
            )
        )
    db.session.flush()


def _seed_recipes() -> None:
    recipes = {
        "Pastel de Chocolate": [
            (MP_HARINA, "250"),
            ("Cacao en Polvo", "80"),
            (MP_AZUCAR, "200"),
            (MP_HUEVO, "3"),
            (MP_MANTEQUILLA, "120"),
            ("Leche Entera", "180"),
            ("Vainilla Extracto", "5"),
            ("Polvo de Hornear", "8"),
        ],
        "Pay de Fresa": [
            (MP_HARINA, "180"),
            (MP_MANTEQUILLA, "90"),
            (MP_AZUCAR, "120"),
            ("Fresas Frescas", "300"),
            (MP_HUEVO, "2"),
        ],
        "Galleta de Nuez": [
            (MP_HARINA, "300"),
            (MP_AZUCAR, "160"),
            (MP_MANTEQUILLA, "150"),
            ("Nuez Pecana", "100"),
            (MP_HUEVO, "2"),
        ],
    }

    products = {p.nombre: p for p in Producto.query.all()}
    materials = {m.nombre: m for m in MateriaPrima.query.all()}

    for product_name, detail_rows in recipes.items():
        product = products.get(product_name)
        if not product:
            continue

        recipe = Receta.query.filter_by(
            id_producto=product.id_producto,
            version=1,
        ).first()
        if not recipe:
            recipe = Receta(
                id_producto=product.id_producto,
                version=1,
                rendimiento_base=12,
                activa=True,
            )
            db.session.add(recipe)
            db.session.flush()

        for material_name, amount in detail_rows:
            material = materials.get(material_name)
            if not material:
                continue
            exists = DetalleReceta.query.filter_by(
                id_receta=recipe.id_receta,
                id_materia_prima=material.id_materia,
            ).first()
            if exists:
                continue
            db.session.add(
                DetalleReceta(
                    id_receta=recipe.id_receta,
                    id_materia_prima=material.id_materia,
                    cantidad_base=Decimal(amount),
                )
            )

    db.session.flush()


def _seed_purchase_and_inventory(users: dict[str, Usuario]) -> None:
    buyer = users.get(ROLE_ADMIN)
    supplier = Proveedor.query.first()
    if not buyer or not supplier:
        return

    existing = Compra.query.first()
    if existing:
        return

    purchase = Compra(
        id_proveedor=supplier.id_proveedor,
        id_usuario_comprador=buyer.id_usuario,
        estado_pago="PAGADO",
        total=Decimal("3200.00"),
    )
    db.session.add(purchase)
    db.session.flush()

    mp_harina = MateriaPrima.query.filter_by(nombre=MP_HARINA).first()
    mp_azucar = MateriaPrima.query.filter_by(nombre=MP_AZUCAR).first()
    if not mp_harina or not mp_azucar:
        return

    details = [
        (mp_harina, Decimal("2"), Decimal("600")),
        (mp_azucar, Decimal("2"), Decimal("500")),
    ]
    total = Decimal("0")
    for material, quantity, unit_price in details:
        subtotal = quantity * unit_price
        base_qty = quantity * Decimal(str(material.factor_conversion))
        total += subtotal
        db.session.add(
            DetalleCompra(
                id_compra=purchase.id_compra,
                id_materia_prima=material.id_materia,
                cantidad_comprada=quantity,
                id_unidad_compra=material.id_unidad_compra,
                precio_unitario=unit_price,
                subtotal=subtotal,
                cantidad_base=base_qty,
            )
        )
        material.cantidad_disponible = (
            Decimal(str(material.cantidad_disponible)) + base_qty
        )
        db.session.add(
            MovimientoInventarioMP(
                id_materia_prima=material.id_materia,
                tipo="ENTRADA",
                cantidad=base_qty,
                id_usuario=buyer.id_usuario,
                referencia_id=f"COMPRA-{purchase.id_compra}",
            )
        )

    purchase.total = total
    db.session.add(
        SalidaEfectivo(
            concepto=f"Pago compra {purchase.id_compra}",
            monto=total,
            tipo="COMPRA_MATERIA_PRIMA",
            id_usuario=buyer.id_usuario,
            referencia=f"COMPRA-{purchase.id_compra}",
        )
    )
    db.session.flush()


def _seed_production_flow(users: dict[str, Usuario]) -> None:
    sales_user = users.get(ROLE_SALES)
    prod_user = users.get(ROLE_PRODUCTION)
    product = Producto.query.filter_by(nombre="Pastel de Chocolate").first()
    recipe = Receta.query.filter_by(activa=True).first()
    if not sales_user or not prod_user or not product or not recipe:
        return

    if not SolicitudProduccion.query.first():
        request = SolicitudProduccion(
            id_producto=product.id_producto,
            cantidad=24,
            estado="APROBADA",
            id_usuario_solicita=sales_user.id_usuario,
            id_usuario_resuelve=prod_user.id_usuario,
            observaciones="Stock bajo en mostrador.",
        )
        db.session.add(request)
        db.session.flush()

        db.session.add(
            OrdenProduccion(
                id_solicitud=request.id_solicitud,
                id_receta=recipe.id_receta,
                id_producto=product.id_producto,
                cantidad_producir=24,
                estado="FINALIZADO",
                fecha_inicio=utc_now(),
                fecha_fin=utc_now(),
                id_usuario_responsable=prod_user.id_usuario,
                costo_total=Decimal("284.50"),
            )
        )

    db.session.flush()


def _seed_customer_flow(users: dict[str, Usuario]) -> None:
    customer = users.get(ROLE_CLIENT)
    sales_user = users.get(ROLE_SALES)
    if not customer or not sales_user:
        return

    cart = Carrito.query.filter_by(
        id_usuario_cliente=customer.id_usuario,
    ).first()
    if not cart:
        cart = Carrito(id_usuario_cliente=customer.id_usuario)
        db.session.add(cart)
        db.session.flush()

    if not DetalleCarrito.query.filter_by(id_carrito=cart.id_carrito).first():
        products = Producto.query.limit(3).all()
        for product in products:
            db.session.add(
                DetalleCarrito(
                    id_carrito=cart.id_carrito,
                    id_producto=product.id_producto,
                    cantidad=1,
                )
            )

    if not Venta.query.filter_by(
        id_usuario_cliente=customer.id_usuario,
    ).first():
        sale = Venta(
            id_usuario_cliente=customer.id_usuario,
            estado="CONFIRMADO",
            pagado_en_linea=True,
            total=Decimal("810.00"),
        )
        db.session.add(sale)
        db.session.flush()

        products = Producto.query.limit(3).all()
        for index, product in enumerate(products, start=1):
            qty = 1 if index != 2 else 2
            subtotal = Decimal(str(product.precio_venta)) * qty
            db.session.add(
                DetalleVenta(
                    id_venta=sale.id_venta,
                    id_producto=product.id_producto,
                    cantidad=qty,
                    precio_unitario=product.precio_venta,
                    subtotal=subtotal,
                )
            )

        db.session.add(
            CorteDiario(
                fecha=utc_today(),
                total_ventas=sale.total,
                numero_ventas=1,
                utilidad_diaria=Decimal("120.00"),
                salida_efectivo_proveedores=Decimal("85.00"),
                id_usuario=sales_user.id_usuario,
            )
        )


def _seed_access_logs(users: dict[str, Usuario]) -> None:
    if BitacoraAcceso.query.count() > 0:
        return

    for role_name, user in users.items():
        db.session.add(
            BitacoraAcceso(
                id_usuario=user.id_usuario,
                exitoso=True,
                error_mensaje=f"Acceso inicial seed para rol {role_name}",
            )
        )
