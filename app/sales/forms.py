from flask_wtf import FlaskForm
from wtforms import (
    DecimalField,
    HiddenField,
    IntegerField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    InputRequired,
    Length,
    NumberRange,
    Optional,
)


class SalidaEfectivoForm(FlaskForm):
    concepto = StringField(
        "Concepto", validators=[DataRequired(message="Este campo es obligatorio.")]
    )
    monto = DecimalField(
        "Monto",
        validators=[
            InputRequired(message="El monto es obligatorio."),
            NumberRange(min=0.01, message="El monto debe ser de al menos 0.01."),
        ],
    )
    tipo = SelectField(
        "Tipo",
        choices=[
            ("GASTO_OPERATIVO", "Gasto operativo"),
            ("COMPRA_MATERIA_PRIMA", "Compra materia prima"),
        ],
    )
    submit = SubmitField("Registrar salida")


# ─────────────────────────── Producto Terminado ────────────────────────────


class ProductoTerminadoForm(FlaskForm):
    id_producto = HiddenField("ID Producto")
    action = HiddenField("Action")
    nombre = StringField(
        "Nombre",
        validators=[
            DataRequired(message="El nombre del producto es obligatorio."),
            Length(max=120, message="Máximo 120 caracteres."),
        ],
    )
    precio_venta = DecimalField(
        "Precio de venta",
        validators=[
            InputRequired(message="El precio de venta es obligatorio."),
            NumberRange(min=0.01, message="El precio debe ser mayor a cero."),
        ],
        places=2,
    )
    unidad_venta = SelectField(
        "Unidad de venta",
        choices=[
            ("Pieza", "Pieza"),
            ("Kg", "Kg"),
            ("Paquete", "Paquete"),
            ("Caja", "Caja"),
            ("Litro", "Litro"),
        ],
        validators=[DataRequired(message="Selecciona una unidad de venta.")],
    )
    stock_minimo = IntegerField(
        "Stock mínimo",
        validators=[
            InputRequired(message="El stock mínimo es obligatorio."),
            NumberRange(min=0, message="No puede ser negativo."),
        ],
    )
    stock_inicial = IntegerField(
        "Stock inicial / actual",
        validators=[
            Optional(),
            NumberRange(min=0, message="No puede ser negativo."),
        ],
    )
    margen_objetivo_pct = DecimalField(
        "Margen objetivo %",
        validators=[
            Optional(),
            NumberRange(min=0.01, max=99.99, message="Debe estar entre 0.01 y 99.99."),
        ],
        places=2,
    )
    id_receta = SelectField(
        "Receta activa",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    descripcion = TextAreaField(
        "Descripción",
        validators=[Optional(), Length(max=500)],
    )
    imagen = StringField(
        "Emoji o URL imagen",
        validators=[Optional(), Length(max=255)],
    )
    activo = SelectField(
        "Estado",
        choices=[("on", "Activo"), ("off", "Inactivo")],
    )
    submit = SubmitField("Guardar producto")


# ─────────────────────────── Solicitudes de Ventas ────────────────────────────


class SolicitudVentasCrearForm(FlaskForm):
    id_producto = SelectField(
        "Producto",
        coerce=int,
        validators=[DataRequired(message="Selecciona un producto.")],
    )
    cantidad = IntegerField(
        "Cantidad solicitada",
        validators=[
            InputRequired(message="La cantidad es obligatoria."),
            NumberRange(min=1, message="Debe ser al menos 1."),
        ],
    )
    observaciones = StringField(
        "Observaciones",
        validators=[Optional(), Length(max=255)],
    )
    submit = SubmitField("Enviar solicitud")


class SolicitudVentasEditarForm(FlaskForm):
    id_solicitud = HiddenField("ID Solicitud")
    cantidad = IntegerField(
        "Cantidad",
        validators=[
            InputRequired(message="La cantidad es obligatoria."),
            NumberRange(min=1, message="Debe ser al menos 1."),
        ],
    )
    observaciones = StringField(
        "Observaciones",
        validators=[Optional(), Length(max=255)],
    )
    submit = SubmitField("Guardar cambios")
