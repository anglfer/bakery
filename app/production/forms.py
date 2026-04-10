from decimal import Decimal

from flask_wtf import FlaskForm
from wtforms import (
    DecimalField,
    IntegerField,
    SelectField,
    StringField,
    SubmitField,
)
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange


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
