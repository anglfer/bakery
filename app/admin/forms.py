from flask_wtf import FlaskForm
from wtforms import EmailField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, Length, Regexp


class ProveedorForm(FlaskForm):
    nombre_empresa = StringField(
        "Nombre proveedor",
        validators=[DataRequired(message="El nombre del proveedor es obligatorio."), Length(max=120, message="Máximo 120 caracteres.")],
    )
    nombre_contacto = StringField(
        "Nombre contacto",
        validators=[DataRequired(message="El nombre del contacto es obligatorio."), Length(max=120, message="Máximo 120 caracteres.")],
    )
    telefono = StringField(
        "Telefono",
        validators=[
            DataRequired(message="El teléfono es obligatorio."),
            Length(min=10, max=20, message="Debe tener entre 10 y 20 caracteres."),
            Regexp(
                r"^(\+52[\s\-]?)?(\d[\s\-]?){10}$",
                message=("Teléfono inválido. Usa 10 dígitos o +52 con 10 dígitos."),
            ),
        ],
    )
    correo = EmailField(
        "Correo electronico",
        validators=[DataRequired(message="El correo es obligatorio."), Email(message="Correo electrónico inválido."), Length(max=120, message="Máximo 120 caracteres.")],
    )
    ciudad = StringField(
        "Ciudad",
        validators=[DataRequired(message="La ciudad es obligatoria."), Length(max=120, message="Máximo 120 caracteres.")],
    )
    estado = StringField(
        "Estado",
        validators=[DataRequired(message="El estado es obligatorio."), Length(max=120, message="Máximo 120 caracteres.")],
    )
    direccion = StringField(
        "Direccion",
        validators=[DataRequired(message="La dirección es obligatoria."), Length(max=255, message="Máximo 255 caracteres.")],
    )
    submit = SubmitField("Guardar")
