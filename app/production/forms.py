from decimal import Decimal

from flask_wtf import FlaskForm
from wtforms import (
    DecimalField,
    IntegerField,
    SelectField,
    StringField,
    SubmitField,
)
from wtforms.validators import DataRequired, Length, NumberRange


class SolicitudProduccionForm(FlaskForm):
    id_producto = SelectField("Producto", coerce=int, validators=[DataRequired()])
    cantidad = IntegerField("Cantidad", validators=[DataRequired(), NumberRange(min=1)])
    submit = SubmitField("Enviar solicitud")


class MateriaPrimaForm(FlaskForm):
    nombre = StringField(
        "Nombre",
        validators=[DataRequired(), Length(min=2, max=120)],
    )
    id_unidad_base = SelectField(
        "Unidad base",
        coerce=int,
        validators=[DataRequired()],
    )
    id_unidad_compra = SelectField(
        "Unidad compra",
        coerce=int,
        validators=[DataRequired()],
    )
    factor_conversion = DecimalField(
        "Factor de conversion",
        validators=[DataRequired(), NumberRange(min=0.0001)],
        places=4,
    )
    porcentaje_merma = DecimalField(
        "% Merma",
        validators=[DataRequired(), NumberRange(min=0)],
        places=2,
        default=Decimal("0"),
    )
    stock_minimo = DecimalField(
        "Stock minimo",
        validators=[DataRequired(), NumberRange(min=0)],
        places=4,
        default=Decimal("0"),
    )
    cantidad_inicial = DecimalField(
        "Cantidad inicial",
        validators=[DataRequired(), NumberRange(min=0)],
        places=4,
        default=Decimal("0"),
    )
    submit = SubmitField("Guardar materia prima")
