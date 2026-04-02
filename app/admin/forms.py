from flask_wtf import FlaskForm
from wtforms import EmailField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, Length, Regexp


class ProveedorForm(FlaskForm):
    nombre_empresa = StringField(
        "Nombre proveedor",
        validators=[DataRequired(), Length(max=120)],
    )
    nombre_contacto = StringField(
        "Nombre contacto",
        validators=[DataRequired(), Length(max=120)],
    )
    telefono = StringField(
        "Telefono",
        validators=[
            DataRequired(),
            Length(min=10, max=20),
            Regexp(
                r"^(\+52[\s\-]?)?(\d[\s\-]?){10}$",
                message=("Telefono invalido. Usa 10 digitos " "o +52 con 10 digitos."),
            ),
        ],
    )
    correo = EmailField(
        "Correo electronico",
        validators=[DataRequired(), Email(), Length(max=120)],
    )
    ciudad = StringField(
        "Ciudad",
        validators=[DataRequired(), Length(max=120)],
    )
    estado = StringField(
        "Estado",
        validators=[DataRequired(), Length(max=120)],
    )
    direccion = StringField(
        "Direccion",
        validators=[DataRequired(), Length(max=255)],
    )
    submit = SubmitField("Guardar")
