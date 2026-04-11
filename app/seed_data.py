from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.common.services import (
    calcular_costo_unitario_producto,
    recalcular_costo_y_precio_sugerido_producto,
    recalcular_costos_productos_afectados_por_materias,
)
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
PRODUCTO_PASTEL_CHOCOLATE = "Pastel de Chocolate"
MXN_QUANTIZE = Decimal("0.01")

LEGACY_PRODUCT_NAMES: dict[str, str] = {
    "Pastel Red Velvet": "Pastel de Red Velvet",
    "Pastel de Frutos": "Pastel de Frutos Secos",
    "Pastel Tres Leches": "Pastel de 3 Leches de Durazno",
}

PRODUCT_CATALOG: tuple[tuple[str, str, Decimal, str, Decimal], ...] = (
    (
        "Pastel de Chocolate",
        "Bizcocho de cacao con betun de chocolate semiamargo.",
        Decimal("480.00"),
        "img/productos/Pastel_de_chocolate.jpg",
        Decimal("30.00"),
    ),
    (
        "Pastel de Red Velvet",
        "Terciopelo rojo con queso crema batido.",
        Decimal("520.00"),
        "img/productos/Pastel_de_red_velvet.jpg",
        Decimal("30.00"),
    ),
    (
        "Pastel Helado de Oreo",
        "Pastel frio con galleta Oreo molida y crema.",
        Decimal("560.00"),
        "img/productos/Pastel_helado_de_Oreo.jpg",
        Decimal("32.00"),
    ),
    (
        "Pastel de Zanahoria",
        "Pan especiado de zanahoria con nuez y canela.",
        Decimal("510.00"),
        "img/productos/Pastel_de_zanahoria.jpg",
        Decimal("30.00"),
    ),
    (
        "Pastel de Vainilla",
        "Bizcocho de vainilla clasico con crema batida.",
        Decimal("450.00"),
        "img/productos/Pastel_de_vainilla.jpg",
        Decimal("28.00"),
    ),
    (
        "Pastel de Moka",
        "Pastel de cafe y chocolate estilo moka.",
        Decimal("540.00"),
        "img/productos/Pastel_de_moka.jpg",
        Decimal("31.00"),
    ),
    (
        "Pastel de Frutos Secos",
        "Pastel con mezcla de nueces y frutos secos.",
        Decimal("590.00"),
        "img/productos/Pastel_de_frutos_secos.jpg",
        Decimal("32.00"),
    ),
    (
        "Pastel de 3 Leches de Durazno",
        "Pastel tres leches con durazno en almibar.",
        Decimal("530.00"),
        "img/productos/Pastel_de_3_leches_de_durazno.jpg",
        Decimal("30.00"),
    ),
    (
        "Chocoflan con Cajeta",
        "Flan napolitano con pan de chocolate y cajeta.",
        Decimal("500.00"),
        "img/productos/Chocoflan_con_cajeta.jpg",
        Decimal("30.00"),
    ),
    (
        "Cheesecake de Mora Azul",
        "Cheesecake cremoso con cobertura de mora azul.",
        Decimal("620.00"),
        "img/productos/cheesecake_de_mora_azul.jpg",
        Decimal("33.00"),
    ),
)

DEFAULT_MARGIN_BY_PRODUCT: dict[str, Decimal] = {
    nombre: margen for nombre, _, _, _, margen in PRODUCT_CATALOG
}


def _to_mxn(value: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(MXN_QUANTIZE, rounding=ROUND_HALF_UP)


def _resolver_costo_unitario_para_snapshot(producto: Producto) -> Decimal:
    costo_actual = Decimal(str(producto.costo_produccion_actual or 0))
    if costo_actual > 0:
        return _to_mxn(costo_actual)

    try:
        return _to_mxn(
            calcular_costo_unitario_producto(
                id_producto=producto.id_producto,
            )
        )
    except ValueError:
        return Decimal("0.00")


def _asegurar_snapshots_historicos_venta(venta: Venta) -> Decimal:
    utilidad_total = Decimal("0")
    detalles_venta = DetalleVenta.query.filter_by(
        id_venta=venta.id_venta,
    ).all()
    for detalle in detalles_venta:
        producto = Producto.query.get(detalle.id_producto)
        if not producto:
            continue

        costo_unitario = _resolver_costo_unitario_para_snapshot(producto)
        utilidad_unitaria = _to_mxn(
            Decimal(str(detalle.precio_unitario)) - costo_unitario
        )

        if detalle.costo_unitario_produccion is None:
            detalle.costo_unitario_produccion = costo_unitario
        if detalle.utilidad_unitaria is None:
            detalle.utilidad_unitaria = utilidad_unitaria

        utilidad_total += Decimal(str(detalle.utilidad_unitaria)) * Decimal(
            str(detalle.cantidad)
        )

    return _to_mxn(utilidad_total)


def seed_full_data() -> None:
    _seed_roles_modules_permissions()
    users = _seed_people_and_users()
    _seed_measurement_units_metadata()
    _seed_products_catalog_metadata()
    _seed_suppliers()
    _seed_raw_materials()
    _seed_recipes()
    _seed_purchase_and_inventory(users)
    _seed_product_costing()
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
            "Producto Terminado": (True, True, True, False),
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
            "Roberto Sanchez",
            "4772001000",
            "harinera@proveedor.local",
            "Leon",
            "Guanajuato",
            "Parque Industrial 1",
        ),
        (
            "Lacteos Premium SA",
            "Claudia Rios",
            "4772002000",
            "lacteos@proveedor.local",
            "Silao",
            "Guanajuato",
            "Zona Sur 23",
        ),
        (
            "Insumos Reposteria MX",
            "Miguel Herrera",
            "4772003000",
            "insumos@proveedor.local",
            "Irapuato",
            "Guanajuato",
            "Av. Comercio 300",
        ),
    ]
    for nombre, contacto, telefono, correo, ciudad, estado, direccion in suppliers:
        exists = Proveedor.query.filter_by(nombre_empresa=nombre).first()
        if exists:
            continue
        db.session.add(
            Proveedor(
                nombre_empresa=nombre,
                nombre_contacto=contacto,
                telefono=telefono,
                correo=correo,
                ciudad=ciudad,
                estado=estado,
                direccion=direccion,
                activo=True,
            )
        )
    db.session.flush()


def _seed_measurement_units_metadata() -> None:
    unit_specs = {
        "kg": ("Kilogramo", "MASA", Decimal("1000")),
        "g": ("Gramo", "MASA", Decimal("1")),
        "l": ("Litro", "VOLUMEN", Decimal("1000")),
        "ml": ("Mililitro", "VOLUMEN", Decimal("1")),
        "pza": ("Pieza", "CONTEO", Decimal("1")),
        "cos": ("Costal", "MASA", Decimal("20000")),
    }

    for unit in UnidadMedida.query.all():
        spec = unit_specs.get(unit.abreviatura)
        if not spec:
            continue
        nombre, dimension, factor_base = spec
        unit.nombre = nombre
        unit.dimension = dimension
        unit.factor_base = factor_base

    db.session.flush()


def _seed_products_catalog_metadata() -> None:
    for legacy_name, target_name in LEGACY_PRODUCT_NAMES.items():
        legacy_product = Producto.query.filter_by(nombre=legacy_name).first()
        target_product = Producto.query.filter_by(nombre=target_name).first()
        if legacy_product and not target_product:
            legacy_product.nombre = target_name

    for nombre, descripcion, precio, imagen, _ in PRODUCT_CATALOG:
        producto = Producto.query.filter_by(nombre=nombre).first()
        if not producto:
            db.session.add(
                Producto(
                    nombre=nombre,
                    descripcion=descripcion,
                    precio_venta=precio,
                    cantidad_disponible=10,
                    stock_minimo=5,
                    activo=True,
                    imagen=imagen,
                )
            )
            continue

        producto.descripcion = descripcion
        producto.precio_venta = precio
        producto.imagen = imagen
        producto.activo = True

    db.session.flush()


def _seed_raw_materials() -> None:
    units = {u.abreviatura: u for u in UnidadMedida.query.all()}
    materials = [
        # name, unidad_base, unidad_compra, factor_conversion, merma%,
        # stock_minimo, stock_inicial, costo_unitario (MXN por unidad_base)
        (MP_HARINA, "g", "cos", "20000", "2.0", "8000", "24000", "0.030"),
        (MP_AZUCAR, "g", "cos", "20000", "0.5", "5000", "16000", "0.028"),
        (MP_MANTEQUILLA, "g", "kg", "1000", "1.5", "2000", "800", "0.16"),
        ("Cacao en Polvo", "g", "kg", "1000", "1.0", "3000", "3200", "0.22"),
        ("Leche Entera", "ml", "l", "1000", "0.0", "2000", "4500", "0.02"),
        (MP_HUEVO, "pza", "pza", "1", "0.0", "30", "48", "4.00"),
        ("Vainilla Extracto", "ml", "l", "1000", "0.0", "500", "600", "0.35"),
        ("Polvo de Hornear", "g", "kg", "1000", "0.0", "800", "900", "0.05"),
        ("Fresas Frescas", "g", "kg", "1000", "8.0", "1000", "1200", "0.09"),
        ("Nuez Pecana", "g", "kg", "1000", "3.0", "600", "700", "0.25"),
        ("Queso Crema", "g", "kg", "1000", "1.0", "1800", "2600", "0.21"),
        (
            "Durazno en Almibar",
            "g",
            "kg",
            "1000",
            "0.5",
            "1500",
            "2500",
            "0.10",
        ),
        ("Cafe Espresso", "g", "kg", "1000", "0.0", "400", "650", "0.42"),
        ("Oreo Molida", "g", "kg", "1000", "0.0", "1200", "1800", "0.12"),
        (
            "Zanahoria Rallada",
            "g",
            "kg",
            "1000",
            "2.0",
            "2000",
            "2600",
            "0.04",
        ),
        (
            "Nuez de Castilla",
            "g",
            "kg",
            "1000",
            "2.0",
            "800",
            "900",
            "0.29",
        ),
        ("Canela Molida", "g", "kg", "1000", "0.0", "200", "300", "0.16"),
        (
            "Leche Condensada",
            "ml",
            "l",
            "1000",
            "0.0",
            "1200",
            "2000",
            "0.08",
        ),
        (
            "Leche Evaporada",
            "ml",
            "l",
            "1000",
            "0.0",
            "1200",
            "2000",
            "0.07",
        ),
        ("Cajeta", "g", "kg", "1000", "0.0", "900", "1400", "0.13"),
        ("Mora Azul", "g", "kg", "1000", "4.0", "900", "1200", "0.20"),
        (
            "Crema para Batir",
            "ml",
            "l",
            "1000",
            "0.0",
            "1400",
            "2000",
            "0.09",
        ),
        (
            "Gelatina sin Sabor",
            "g",
            "kg",
            "1000",
            "0.0",
            "100",
            "180",
            "0.35",
        ),
    ]

    for name, base_u, buy_u, factor, merma, minimo, stock, costo in materials:
        unidad_base = units[base_u]
        unidad_compra = units[buy_u]
        es_conteo = (unidad_base.dimension or "CONTEO").upper() == "CONTEO"
        factor_decimal = Decimal(factor)
        minimo_decimal = Decimal(minimo)
        stock_decimal = Decimal(stock)

        if es_conteo:
            factor_decimal = factor_decimal.to_integral_value(rounding=ROUND_HALF_UP)
            minimo_decimal = minimo_decimal.to_integral_value(rounding=ROUND_HALF_UP)
            stock_decimal = stock_decimal.to_integral_value(rounding=ROUND_HALF_UP)

        exists = MateriaPrima.query.filter_by(nombre=name).first()
        if not exists:
            db.session.add(
                MateriaPrima(
                    nombre=name,
                    id_unidad_base=unidad_base.id_unidad,
                    id_unidad_compra=unidad_compra.id_unidad,
                    factor_conversion=factor_decimal,
                    porcentaje_merma=Decimal(merma),
                    costo_unitario=Decimal(costo),
                    stock_minimo=minimo_decimal,
                    cantidad_disponible=stock_decimal,
                    activa=True,
                )
            )
            continue

        exists.id_unidad_base = unidad_base.id_unidad
        exists.id_unidad_compra = unidad_compra.id_unidad
        exists.factor_conversion = factor_decimal
        exists.porcentaje_merma = Decimal(merma)
        exists.costo_unitario = Decimal(costo)
        exists.stock_minimo = minimo_decimal
        exists.cantidad_disponible = max(
            Decimal(str(exists.cantidad_disponible)),
            stock_decimal,
        )
        if es_conteo:
            exists.cantidad_disponible = exists.cantidad_disponible.to_integral_value(
                rounding=ROUND_HALF_UP
            )
        exists.activa = True
    db.session.flush()


def _seed_recipes() -> None:
    recipes: dict[str, dict] = {
        "Pastel de Chocolate": {
            "rendimiento_base": 1,
            "ingredientes": [
                (MP_HARINA, "250"),
                ("Cacao en Polvo", "80"),
                (MP_AZUCAR, "200"),
                (MP_HUEVO, "3"),
                (MP_MANTEQUILLA, "120"),
                ("Leche Entera", "180"),
                ("Vainilla Extracto", "5"),
                ("Polvo de Hornear", "8"),
            ],
        },
        "Pastel de Red Velvet": {
            "rendimiento_base": 1,
            "ingredientes": [
                (MP_HARINA, "250"),
                (MP_AZUCAR, "220"),
                (MP_HUEVO, "3"),
                (MP_MANTEQUILLA, "130"),
                ("Leche Entera", "220"),
                ("Vainilla Extracto", "5"),
                ("Cacao en Polvo", "20"),
                ("Polvo de Hornear", "8"),
                ("Queso Crema", "180"),
            ],
        },
        "Pastel Helado de Oreo": {
            "rendimiento_base": 1,
            "ingredientes": [
                ("Oreo Molida", "300"),
                ("Queso Crema", "250"),
                ("Crema para Batir", "300"),
                (MP_MANTEQUILLA, "90"),
                ("Leche Condensada", "180"),
                ("Gelatina sin Sabor", "12"),
            ],
        },
        "Pastel de Zanahoria": {
            "rendimiento_base": 1,
            "ingredientes": [
                (MP_HARINA, "260"),
                (MP_AZUCAR, "210"),
                (MP_HUEVO, "3"),
                ("Zanahoria Rallada", "300"),
                ("Nuez de Castilla", "120"),
                ("Canela Molida", "5"),
                ("Polvo de Hornear", "10"),
                (MP_MANTEQUILLA, "110"),
            ],
        },
        "Pastel de Vainilla": {
            "rendimiento_base": 1,
            "ingredientes": [
                (MP_HARINA, "260"),
                (MP_AZUCAR, "220"),
                (MP_HUEVO, "3"),
                (MP_MANTEQUILLA, "120"),
                ("Leche Entera", "200"),
                ("Vainilla Extracto", "8"),
                ("Polvo de Hornear", "8"),
            ],
        },
        "Pastel de Moka": {
            "rendimiento_base": 1,
            "ingredientes": [
                (MP_HARINA, "240"),
                (MP_AZUCAR, "220"),
                (MP_HUEVO, "3"),
                (MP_MANTEQUILLA, "120"),
                ("Leche Entera", "180"),
                ("Cafe Espresso", "20"),
                ("Cacao en Polvo", "40"),
                ("Vainilla Extracto", "4"),
                ("Polvo de Hornear", "8"),
            ],
        },
        "Pastel de Frutos Secos": {
            "rendimiento_base": 1,
            "ingredientes": [
                (MP_HARINA, "250"),
                (MP_AZUCAR, "200"),
                (MP_HUEVO, "3"),
                (MP_MANTEQUILLA, "130"),
                ("Nuez Pecana", "100"),
                ("Nuez de Castilla", "80"),
                ("Vainilla Extracto", "6"),
                ("Canela Molida", "4"),
                ("Polvo de Hornear", "8"),
            ],
        },
        "Pastel de 3 Leches de Durazno": {
            "rendimiento_base": 1,
            "ingredientes": [
                (MP_HARINA, "230"),
                (MP_AZUCAR, "180"),
                (MP_HUEVO, "3"),
                (MP_MANTEQUILLA, "100"),
                ("Leche Entera", "200"),
                ("Leche Condensada", "220"),
                ("Leche Evaporada", "220"),
                ("Durazno en Almibar", "250"),
                ("Polvo de Hornear", "8"),
                ("Vainilla Extracto", "6"),
            ],
        },
        "Chocoflan con Cajeta": {
            "rendimiento_base": 1,
            "ingredientes": [
                (MP_HARINA, "220"),
                (MP_AZUCAR, "180"),
                (MP_HUEVO, "4"),
                (MP_MANTEQUILLA, "100"),
                ("Leche Entera", "180"),
                ("Cacao en Polvo", "60"),
                ("Leche Condensada", "180"),
                ("Leche Evaporada", "160"),
                ("Queso Crema", "150"),
                ("Cajeta", "200"),
                ("Vainilla Extracto", "6"),
                ("Polvo de Hornear", "8"),
            ],
        },
        "Cheesecake de Mora Azul": {
            "rendimiento_base": 1,
            "ingredientes": [
                ("Oreo Molida", "220"),
                (MP_MANTEQUILLA, "100"),
                ("Queso Crema", "500"),
                ("Crema para Batir", "220"),
                (MP_AZUCAR, "180"),
                (MP_HUEVO, "3"),
                ("Mora Azul", "250"),
                ("Gelatina sin Sabor", "10"),
            ],
        },
    }

    products = {p.nombre: p for p in Producto.query.all()}
    materials = {m.nombre: m for m in MateriaPrima.query.all()}

    for product_name, recipe_data in recipes.items():
        product = products.get(product_name)
        if not product:
            continue

        detail_rows = recipe_data["ingredientes"]
        rendimiento_base = Decimal(str(recipe_data["rendimiento_base"]))

        recipe = Receta.query.filter_by(
            id_producto=product.id_producto,
            version=1,
        ).first()
        if not recipe:
            recipe = Receta(
                id_producto=product.id_producto,
                nombre=product.nombre,
                version=1,
                rendimiento_base=rendimiento_base,
                activa=True,
            )
            db.session.add(recipe)
            db.session.flush()
        else:
            recipe.id_producto = product.id_producto
            recipe.nombre = product.nombre
            recipe.rendimiento_base = rendimiento_base
            recipe.activa = True

        Receta.query.filter(
            Receta.id_producto == product.id_producto,
            Receta.id_receta != recipe.id_receta,
        ).update({"activa": False}, synchronize_session=False)

        # asociar la receta creada al producto
        product.id_receta = recipe.id_receta
        db.session.add(product)
        db.session.flush()

        ids_materia_receta: set[int] = set()
        for material_name, amount in detail_rows:
            material = materials.get(material_name)
            if not material:
                continue
            ids_materia_receta.add(material.id_materia)
            exists = DetalleReceta.query.filter_by(
                id_receta=recipe.id_receta,
                id_materia_prima=material.id_materia,
            ).first()
            if exists:
                exists.cantidad_base = Decimal(amount)
                continue
            db.session.add(
                DetalleReceta(
                    id_receta=recipe.id_receta,
                    id_materia_prima=material.id_materia,
                    cantidad_base=Decimal(amount),
                )
            )

        detalles_existentes = DetalleReceta.query.filter_by(
            id_receta=recipe.id_receta
        ).all()
        for detalle in detalles_existentes:
            if detalle.id_materia_prima not in ids_materia_receta:
                db.session.delete(detalle)

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
    ids_materia_impactadas: set[int] = set()
    for material, quantity, unit_price in details:
        subtotal = quantity * unit_price
        base_qty = quantity * Decimal(str(material.factor_conversion))
        total += subtotal
        ids_materia_impactadas.add(material.id_materia)
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

    recalcular_costos_productos_afectados_por_materias(
        ids_materia=sorted(ids_materia_impactadas)
    )
    db.session.flush()


def _seed_product_costing() -> None:
    for product_name, margen in DEFAULT_MARGIN_BY_PRODUCT.items():
        producto = Producto.query.filter_by(nombre=product_name).first()
        if not producto:
            continue
        producto.margen_objetivo_pct = margen

    db.session.flush()

    productos_con_receta = (
        Producto.query.filter(Producto.activo.is_(True))
        .filter(Producto.id_receta.isnot(None))
        .all()
    )
    for producto in productos_con_receta:
        try:
            recalcular_costo_y_precio_sugerido_producto(
                id_producto=producto.id_producto,
            )
        except ValueError:
            continue

    db.session.flush()


def _seed_production_flow(users: dict[str, Usuario]) -> None:
    sales_user = users.get(ROLE_SALES)
    prod_user = users.get(ROLE_PRODUCTION)
    product = Producto.query.filter_by(
        nombre=PRODUCTO_PASTEL_CHOCOLATE,
    ).first()
    recipe = Receta.query.filter_by(activa=True).first()
    if not sales_user or not prod_user or not product or not recipe:
        return

    if not SolicitudProduccion.query.first():
        approved_request = SolicitudProduccion(
            id_producto=product.id_producto,
            cantidad=24,
            estado="APROBADA",
            id_usuario_solicita=sales_user.id_usuario,
            id_usuario_resuelve=prod_user.id_usuario,
            observaciones="Stock bajo en mostrador.",
            observaciones_resolucion="Se aprueba para cubrir demanda del día.",
            fecha_resolucion=utc_now(),
        )
        db.session.add(approved_request)
        db.session.flush()

        db.session.add(
            OrdenProduccion(
                id_solicitud=approved_request.id_solicitud,
                id_receta=recipe.id_receta,
                id_producto=product.id_producto,
                cantidad_producir=24,
                estado="FINALIZADO",
                fecha_inicio=utc_now(),
                fecha_fin=utc_now(),
                id_usuario_responsable=prod_user.id_usuario,
                costo_total=_to_mxn(
                    _resolver_costo_unitario_para_snapshot(product) * Decimal("24")
                ),
            )
        )

        db.session.add(
            SolicitudProduccion(
                id_producto=product.id_producto,
                cantidad=16,
                estado="PENDIENTE",
                id_usuario_solicita=sales_user.id_usuario,
                observaciones=("Pedido para evento corporativo del fin de semana."),
            )
        )
        db.session.add(
            SolicitudProduccion(
                id_producto=product.id_producto,
                cantidad=12,
                estado="RECHAZADA",
                id_usuario_solicita=sales_user.id_usuario,
                id_usuario_resuelve=prod_user.id_usuario,
                observaciones=("Se agotó temporalmente la cobertura de insumos."),
                observaciones_resolucion=(
                    "Se programa reapertura cuando ingrese materia prima."
                ),
                fecha_resolucion=utc_now(),
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

    sale = Venta.query.filter_by(
        id_usuario_cliente=customer.id_usuario,
    ).first()
    if not sale:
        sale = Venta(
            id_usuario_cliente=customer.id_usuario,
            estado="CONFIRMADO",
            tipo_pago="TARJETA",
            total=Decimal("0.00"),
        )
        db.session.add(sale)
        db.session.flush()

        products = Producto.query.limit(3).all()
        total_venta = Decimal("0")
        for index, product in enumerate(products, start=1):
            qty = 1 if index != 2 else 2
            precio_unitario = Decimal(str(product.precio_venta))
            costo_unitario = _resolver_costo_unitario_para_snapshot(product)
            utilidad_unitaria = _to_mxn(precio_unitario - costo_unitario)
            subtotal = _to_mxn(precio_unitario * Decimal(str(qty)))
            total_venta += subtotal
            db.session.add(
                DetalleVenta(
                    id_venta=sale.id_venta,
                    id_producto=product.id_producto,
                    cantidad=qty,
                    precio_unitario=product.precio_venta,
                    costo_unitario_produccion=costo_unitario,
                    utilidad_unitaria=utilidad_unitaria,
                    subtotal=subtotal,
                )
            )

        sale.total = _to_mxn(total_venta)
    else:
        _asegurar_snapshots_historicos_venta(sale)

    if not CorteDiario.query.filter_by(fecha=utc_today()).first():
        utilidad_diaria = _asegurar_snapshots_historicos_venta(sale)

        db.session.add(
            CorteDiario(
                fecha=utc_today(),
                total_ventas=sale.total,
                numero_ventas=1,
                utilidad_diaria=utilidad_diaria,
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


if __name__ == "__main__":
    from app import create_app

    app = create_app()
    with app.app_context():
        seed_full_data()
        db.session.commit()
        print("Seed completa ejecutada.")
