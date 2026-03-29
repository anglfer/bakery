from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Length


class ProveedorForm(FlaskForm):
    nombre_empresa = StringField("Nombre empresa", validators=[DataRequired(), Length(max=120)])
    submit = SubmitField("Guardar")
