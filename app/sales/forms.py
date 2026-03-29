from flask_wtf import FlaskForm
from wtforms import DecimalField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, NumberRange


class SalidaEfectivoForm(FlaskForm):
    concepto = StringField("Concepto", validators=[DataRequired()])
    monto = DecimalField("Monto", validators=[DataRequired(), NumberRange(min=0.01)])
    tipo = SelectField("Tipo", choices=[("GASTO_OPERATIVO", "Gasto operativo"), ("COMPRA_MATERIA_PRIMA", "Compra materia prima")])
    submit = SubmitField("Registrar salida")
