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
        "Usuario", validators=[DataRequired(), Length(min=3, max=60)]
    )
    password = PasswordField("Contrasena", validators=[DataRequired()])
    submit = SubmitField("Iniciar sesion")


class Verify2FAForm(FlaskForm):
    code = StringField(
        "Codigo",
        validators=[DataRequired(), Length(min=6, max=6)],
    )
    submit = SubmitField("Verificar codigo")


class RegisterClientForm(FlaskForm):
    nombre = StringField(
        "Nombre",
        validators=[DataRequired(), Length(max=100)],
    )
    apellidos = StringField(
        "Apellido",
        validators=[DataRequired(), Length(max=120)],
    )
    telefono = StringField(
        "Telefono",
        validators=[
            DataRequired(),
            Length(min=7, max=30),
            Regexp(
                r"^[\d\s\-\+\(\)]+$",
                message=(
                    "Telefono invalido. " "Usa solo numeros y separadores (+ - ())."
                ),
            ),
        ],
    )
    username = StringField(
        "Usuario",
        validators=[
            DataRequired(),
            Length(min=4, max=60),
            Regexp(
                r"^[a-zA-Z0-9_.]+$",
                message=(
                    "Usuario invalido. Usa solo letras, numeros, "
                    "guion bajo (_) y punto (.)"
                ),
            ),
        ],
    )
    password = PasswordField(
        "Contrasena",
        validators=[DataRequired(), PASSWORD_RULE],
    )
    confirm_password = PasswordField(
        "Confirmar contrasena",
        validators=[
            DataRequired(),
            EqualTo("password", message="Las contrasenas no coinciden."),
        ],
    )
    submit = SubmitField("Crear cuenta")

    def validate_password(self, field) -> None:
        if is_password_insecure(field.data or ""):
            raise ValidationError(
                ("La contraseña es demasiado común o insegura. " "Elige una diferente.")
            )
