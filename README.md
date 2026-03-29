# SoftBakery 2.0

Sistema modular en Flask para seguridad, proveedores, inventario, produccion, ventas y portal de cliente.

## Ejecutar

1. Crear entorno virtual.
2. Instalar dependencias con `pip install -r requirements.txt`.
3. Ejecutar `python run.py` o `python entrypoint.py`.

## Modulos

- `app/auth`: login, 2FA, registro cliente.
- `app/admin`: dashboard, usuarios, roles, proveedores.
- `app/production`: recetas, ordenes, solicitudes.
- `app/sales`: ventas, costos/utilidad, salidas de efectivo.
- `app/catalog`: landing, catalogo y carrito.
