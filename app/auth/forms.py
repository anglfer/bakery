from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import (
    DataRequired,
    EqualTo,
    Length,
    Regexp,
    ValidationError,
)

from app.common.passwords import is_password_insecure

PASSWORD_RULE = Regexp(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z\d]).{8,}$",
    message=(
        "Minimo 8 caracteres con mayuscula, minuscula, " "numero y caracter especial."
    ),
)


class LoginForm(FlaskForm):
    username = StringField(
        "Usuario", validators=[DataRequired(message="El usuario es obligatorio"), Length(min=3, max=60, message="Entre 3 y 60 caracteres.")]
    )
    password = PasswordField("Contrasena", validators=[DataRequired(message="La contraseña es obligatoria")])
    submit = SubmitField("Iniciar sesion")


class Verify2FAForm(FlaskForm):
    code = StringField(
        "Codigo",
        validators=[DataRequired(message="El codigo es obligatorio"), Length(min=6, max=6, message="Debe ser de 6 caracteres.")],
    )
    submit = SubmitField("Verificar codigo")


class RegisterClientForm(FlaskForm):
    nombre = StringField(
        "Nombre",
        validators=[DataRequired(message="El nombre es obligatorio."), Length(max=100, message="Máximo 100 caracteres.")],
    )
    apellidos = StringField(
        "Apellido",
        validators=[DataRequired(message="El apellido es obligatorio."), Length(max=120, message="Máximo 120 caracteres.")],
    )
    telefono = StringField(
        "Telefono",
        validators=[
            DataRequired(message="El teléfono es obligatorio."),
            Length(min=7, max=30, message="Entre 7 y 30 caracteres."),
            Regexp(
                r"^[\d\s\-\+\(\)]+$",
                message=(
                    "Teléfono inválido. Usa solo números y separadores (+ - ())."
                ),
            ),
        ],
    )
    username = StringField(
        "Usuario",
        validators=[
            DataRequired(message="El usuario es obligatorio."),
            Length(min=4, max=60, message="Entre 4 y 60 caracteres."),
            Regexp(
                r"^[a-zA-Z0-9_.]+$",
                message=(
                    "Usuario inválido. Usa solo letras, números, "
                    "guion bajo (_) y punto (.)"
                ),
            ),
        ],
    )
    password = PasswordField(
        "Contrasena",
        validators=[DataRequired(message="La contraseña es obligatoria."), PASSWORD_RULE],
    )
    confirm_password = PasswordField(
        "Confirmar contrasena",
        validators=[
            DataRequired(message="La confirmación es obligatoria."),
            EqualTo("password", message="Las contraseñas no coinciden."),
        ],
    )
    submit = SubmitField("Crear cuenta")

    def validate_password(self, field) -> None:
        if is_password_insecure(field.data or ""):
            raise ValidationError(
                ("La contraseña es demasiado común o insegura. " "Elige una diferente.")
            )
