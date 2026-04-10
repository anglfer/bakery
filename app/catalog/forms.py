from flask_wtf import FlaskForm
from wtforms import IntegerField, SubmitField
from wtforms.validators import InputRequired, NumberRange


class CarritoForm(FlaskForm):
    cantidad = IntegerField(
        "Cantidad", validators=[InputRequired(message="La cantidad es obligatoria."), NumberRange(min=1, max=5, message="La cantidad debe ser entre 1 y 5.")]
    )
    submit = SubmitField("Agregar")
