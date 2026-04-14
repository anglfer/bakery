from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    EmailField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    Optional,
    Regexp,
)


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


# Usuarios 

class UsuarioCrearForm(FlaskForm):
    nombre = StringField(
        "Nombre(s)",
        validators=[DataRequired(message="El nombre es obligatorio."), Length(max=80, message="Máximo 80 caracteres.")],
    )
    apellidos = StringField(
        "Apellidos",
        validators=[DataRequired(message="Los apellidos son obligatorios."), Length(max=80, message="Máximo 80 caracteres.")],
    )
    telefono = StringField(
        "Teléfono",
        validators=[
            DataRequired(message="El teléfono es obligatorio."),
            Length(min=10, max=20, message="Debe tener entre 10 y 20 caracteres."),
            Regexp(r"^\+?[\d\s\-]{10,20}$", message="Teléfono inválido."),
        ],
    )
    correo = EmailField(
        "Correo electrónico",
        validators=[DataRequired(message="El correo es obligatorio."), Email(message="Correo inválido."), Length(max=120)],
    )
    ciudad = StringField(
        "Ciudad",
        validators=[Optional(), Length(max=80)],
    )
    direccion = StringField(
        "Dirección",
        validators=[Optional(), Length(max=255)],
    )
    username = StringField(
        "Nombre de usuario",
        validators=[
            DataRequired(message="El nombre de usuario es obligatorio."),
            Length(min=3, max=50, message="Entre 3 y 50 caracteres."),
            Regexp(r"^[\w.\-]+$", message="Solo letras, números, puntos y guiones."),
        ],
    )
    id_rol = SelectField(
        "Rol",
        coerce=int,
        validators=[DataRequired(message="Selecciona un rol.")],
    )
    password = PasswordField(
        "Contraseña",
        validators=[
            DataRequired(message="La contraseña es obligatoria."),
            Length(min=6, message="Mínimo 6 caracteres."),
        ],
    )
    password_confirm = PasswordField(
        "Confirmar contraseña",
        validators=[
            DataRequired(message="Confirma la contraseña."),
            EqualTo("password", message="Las contraseñas no coinciden."),
        ],
    )
    submit = SubmitField("Crear usuario")


class UsuarioEditarForm(FlaskForm):
    nombre = StringField(
        "Nombre(s)",
        validators=[DataRequired(message="El nombre es obligatorio."), Length(max=80)],
    )
    apellidos = StringField(
        "Apellidos",
        validators=[DataRequired(message="Los apellidos son obligatorios."), Length(max=80)],
    )
    telefono = StringField(
        "Teléfono",
        validators=[
            DataRequired(message="El teléfono es obligatorio."),
            Length(min=10, max=20, message="Debe tener entre 10 y 20 caracteres."),
            Regexp(r"^\+?[\d\s\-]{10,20}$", message="Teléfono inválido."),
        ],
    )
    ciudad = StringField(
        "Ciudad",
        validators=[Optional(), Length(max=80)],
    )
    direccion = StringField(
        "Dirección",
        validators=[Optional(), Length(max=255)],
    )
    id_rol = SelectField(
        "Rol",
        coerce=int,
        validators=[DataRequired(message="Selecciona un rol.")],
    )
    submit = SubmitField("Guardar cambios")


#  Roles

class RolCrearForm(FlaskForm):
    nombre = StringField(
        "Nombre del rol",
        validators=[
            DataRequired(message="El nombre es obligatorio."),
            Length(min=2, max=50, message="Entre 2 y 50 caracteres."),
            Regexp(r"^[\w\s\-áéíóúÁÉÍÓÚñÑüÜ]+$", message="Solo letras, números, espacios y guiones."),
        ],
    )
    descripcion = TextAreaField(
        "Descripción",
        validators=[DataRequired(message="La descripción es obligatoria."), Length(max=255, message="Máximo 255 caracteres.")],
    )
    submit = SubmitField("Crear rol")


class RolEditarForm(FlaskForm):
    descripcion = TextAreaField(
        "Descripción",
        validators=[DataRequired(message="La descripción es obligatoria."), Length(max=255, message="Máximo 255 caracteres.")],
    )
    submit = SubmitField("Guardar cambios")
