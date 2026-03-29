from flask_wtf import FlaskForm
from wtforms import IntegerField, SelectField, SubmitField
from wtforms.validators import DataRequired, NumberRange


class SolicitudProduccionForm(FlaskForm):
    id_producto = SelectField("Producto", coerce=int, validators=[DataRequired()])
    cantidad = IntegerField("Cantidad", validators=[DataRequired(), NumberRange(min=1)])
    submit = SubmitField("Enviar solicitud")
