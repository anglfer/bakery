from flask_wtf import FlaskForm
from wtforms import DecimalField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, InputRequired, NumberRange


class SalidaEfectivoForm(FlaskForm):
    concepto = StringField("Concepto", validators=[DataRequired(message="Este campo es obligatorio.")])
    monto = DecimalField("Monto", validators=[InputRequired(message="El monto es obligatorio."), NumberRange(min=0.01, message="El monto debe ser de al menos 0.01.")])
    tipo = SelectField("Tipo", choices=[("GASTO_OPERATIVO", "Gasto operativo"), ("COMPRA_MATERIA_PRIMA", "Compra materia prima")])
    submit = SubmitField("Registrar salida")
