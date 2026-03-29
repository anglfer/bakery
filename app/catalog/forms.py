from flask_wtf import FlaskForm
from wtforms import IntegerField, SubmitField
from wtforms.validators import DataRequired, NumberRange


class CarritoForm(FlaskForm):
    cantidad = IntegerField(
        "Cantidad", validators=[DataRequired(), NumberRange(min=1, max=5)]
    )
    submit = SubmitField("Agregar")
