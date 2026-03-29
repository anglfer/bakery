from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Regexp, ValidationError

from app.common.passwords import is_password_insecure

PASSWORD_RULE = Regexp(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z\d]).{8,}$",
    message="Minimo 8 caracteres con mayuscula, minuscula, numero y caracter especial.",
)


class LoginForm(FlaskForm):
    username = StringField(
        "Usuario", validators=[DataRequired(), Length(min=3, max=60)]
    )
    password = PasswordField("Contrasena", validators=[DataRequired()])
    submit = SubmitField("Iniciar sesion")


class Verify2FAForm(FlaskForm):
    code = StringField("Codigo", validators=[DataRequired(), Length(min=6, max=6)])
    submit = SubmitField("Verificar codigo")


class RegisterClientForm(FlaskForm):
    nombre = StringField("Nombre", validators=[DataRequired(), Length(max=100)])
    apellidos = StringField("Apellidos", validators=[DataRequired(), Length(max=120)])
    telefono = StringField("Telefono", validators=[DataRequired(), Length(max=30)])
    correo = StringField("Correo", validators=[DataRequired(), Length(max=120)])
    direccion = StringField("Direccion", validators=[DataRequired(), Length(max=255)])
    ciudad = StringField("Ciudad", validators=[DataRequired(), Length(max=120)])
    username = StringField(
        "Usuario", validators=[DataRequired(), Length(min=3, max=60)]
    )
    password = PasswordField("Contrasena", validators=[DataRequired(), PASSWORD_RULE])
    submit = SubmitField("Crear cuenta")

    def validate_password(self, field) -> None:
        if is_password_insecure(field.data or ""):
            raise ValidationError(
                "La contraseña es demasiado común o insegura. Elige una diferente."
            )
