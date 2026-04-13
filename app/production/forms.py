from decimal import Decimal

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
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange, Optional


class RecetaBaseForm(FlaskForm):
    id_producto = SelectField("Producto", coerce=int, validators=[DataRequired(message="Seleccionar producto.")])
    rendimiento_base = DecimalField("Cantidad base", validators=[InputRequired(message="Cantidad requerida."), NumberRange(min=0.01)], places=2)
    estado = SelectField("Estado", choices=[("ACTIVA", "Activa"), ("INACTIVA", "Inactiva")])
    categoria = StringField("Categoría", validators=[Optional(), Length(max=100)])
    descripcion = StringField("Descripción corta", validators=[Optional(), Length(max=255)])
    unidad_produccion = StringField("Unidad de producción", validators=[Optional(), Length(max=50)])

class SolicitudProduccionForm(FlaskForm):
    id_producto = SelectField("Producto", coerce=int, validators=[DataRequired(message="Este campo es obligatorio.")])
    cantidad = IntegerField("Cantidad", validators=[InputRequired(message="La cantidad es obligatoria."), NumberRange(min=1, message="Debe ser mayor o igual a 1.")])
    submit = SubmitField("Enviar solicitud")


class MateriaPrimaForm(FlaskForm):
    nombre = StringField(
        "Nombre",
        validators=[DataRequired(message="El nombre es obligatorio."), Length(min=2, max=120, message="Entre 2 y 120 caracteres.")],
    )
    id_unidad_base = SelectField(
        "Unidad base",
        coerce=int,
        validators=[DataRequired(message="Este campo es obligatorio.")],
    )
    id_unidad_compra = SelectField(
        "Unidad compra",
        coerce=int,
        validators=[DataRequired(message="Este campo es obligatorio.")],
    )
    factor_conversion = DecimalField(
        "Factor de conversion",
        validators=[InputRequired(message="El factor es obligatorio."), NumberRange(min=0.0001, message="Debe ser mayor a 0.")],
        places=4,
    )
    porcentaje_merma = DecimalField(
        "% Merma",
        validators=[InputRequired(message="El porcentaje es obligatorio."), NumberRange(min=0, message="No puede ser negativo.")],
        places=2,
        default=Decimal("0"),
    )
    stock_minimo = DecimalField(
        "Stock minimo",
        validators=[InputRequired(message="El stock es obligatorio."), NumberRange(min=0, message="No puede ser negativo.")],
        places=4,
        default=Decimal("0"),
    )
    cantidad_inicial = DecimalField(
        "Cantidad inicial",
        validators=[InputRequired(message="La cantidad es obligatoria."), NumberRange(min=0, message="No puede ser negativo.")],
        places=4,
        default=Decimal("0"),
    )
    submit = SubmitField("Guardar materia prima")


# ─────────────────────────── Ajuste de Inventario ────────────────────────────

class AjusteInventarioForm(FlaskForm):
    id_materia = HiddenField("ID Materia")
    tipo = HiddenField("Tipo", default="ENTRADA")
    cantidad = DecimalField(
        "Cantidad",
        validators=[
            InputRequired(message="La cantidad es obligatoria."),
            NumberRange(min=0.0001, message="La cantidad debe ser mayor a cero."),
        ],
        places=4,
    )
    referencia_id = StringField(
        "Referencia / Motivo",
        validators=[
            DataRequired(message="Debes indicar una referencia o motivo del movimiento."),
            Length(max=255, message="Máximo 255 caracteres."),
        ],
    )
    submit = SubmitField("Registrar movimiento")


# ─────────────────────────── Órdenes de Producción ────────────────────────────

class OrdenProduccionForm(FlaskForm):
    id_solicitud = SelectField(
        "Solicitud aprobada (opcional)",
        coerce=int,
        validators=[Optional()],
    )
    id_producto = SelectField(
        "Producto",
        coerce=int,
        validators=[DataRequired(message="Selecciona un producto.")],
    )
    id_receta = SelectField(
        "Receta activa",
        coerce=int,
        validators=[DataRequired(message="Selecciona una receta activa.")],
    )
    cantidad = IntegerField(
        "Cantidad a producir",
        validators=[
            InputRequired(message="La cantidad es obligatoria."),
            NumberRange(min=1, message="Debe ser al menos 1."),
        ],
    )
    observaciones = StringField(
        "Observaciones",
        validators=[Optional(), Length(max=255)],
    )
    submit = SubmitField("Crear orden")


# ─────────────────────────── Resolver Solicitud ────────────────────────────

class ResolverSolicitudForm(FlaskForm):
    id_solicitud = HiddenField("ID Solicitud")
    estado = SelectField(
        "Estado",
        choices=[("APROBADA", "Aprobar"), ("RECHAZADA", "Rechazar")],
        validators=[DataRequired(message="Selecciona un estado.")],
    )
    observaciones_resolucion = StringField(
        "Observaciones de resolución",
        validators=[Optional(), Length(max=255)],
    )
    submit = SubmitField("Guardar resolución")
