import random
import smtplib
from datetime import timedelta
from email.message import EmailMessage

from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from app.auth import auth_bp
from app.auth.forms import LoginForm, RegisterClientForm, Verify2FAForm
from app.extensions import db
from app.models import BitacoraAcceso, Persona, Rol, Usuario, utc_now

LOGIN_TEMPLATE = "auth/login.html"
REGISTER_TEMPLATE = "auth/registro_cliente.html"
LOGIN_ENDPOINT = "auth.login"


def _resolve_home_by_role(usuario: Usuario) -> str:
    role_name = usuario.rol.nombre if usuario.rol else ""
    if role_name == "Administrador":
        return "admin.dashboard"
    if role_name == "Ventas":
        return "sales.ventas"
    if role_name == "Produccion":
        return "production.ordenes"
    return "catalog.home"


def _registrar_bitacora(
    usuario: Usuario, exitoso: bool, mensaje: str | None = None
) -> None:
    db.session.add(
        BitacoraAcceso(
            id_usuario=usuario.id_usuario,
            exitoso=exitoso,
            error_mensaje=mensaje,
        )
    )


def _send_2fa_code_email(usuario: Usuario) -> bool:
    username = current_app.config.get("MAIL_USERNAME")
    password = current_app.config.get("MAIL_PASSWORD")
    sender = current_app.config.get("MAIL_DEFAULT_SENDER") or username
    server = current_app.config.get("MAIL_SERVER", "smtp.gmail.com")
    port = int(current_app.config.get("MAIL_PORT", 587))
    use_tls = bool(current_app.config.get("MAIL_USE_TLS", True))

    if not username or not password or not sender:
        return False

    if not usuario.persona or not usuario.persona.correo or not usuario.token_2fa:
        return False

    message = EmailMessage()
    message["Subject"] = "Codigo de verificacion SoftBakery"
    message["From"] = sender
    message["To"] = usuario.persona.correo
    message.set_content(
        "Tu codigo de verificacion es: "
        f"{usuario.token_2fa}. "
        "Este codigo expira en 5 minutos."
    )

    try:
        with smtplib.SMTP(server, port, timeout=15) as smtp:
            if use_tls:
                smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(message)
        return True
    except (smtplib.SMTPException, OSError):
        return False


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("catalog.home"))

    form = LoginForm()
    if form.validate_on_submit():
        usuario = Usuario.query.filter_by(
            username=form.username.data.strip(), activo=True
        ).first()
        if not usuario:
            flash("Credenciales invalidas", "danger")
            return render_template(LOGIN_TEMPLATE, form=form, show_captcha=False)

        needs_captcha = usuario.intentos_fallidos >= 3
        # For this version, ReCaptcha is disabled due to missing configuration
        # but we preserve the condition if it needs to be implemented.

        if usuario.is_locked():
            flash(
                "Usuario temporalmente bloqueado por intentos fallidos",
                "warning",
            )
            _registrar_bitacora(usuario, False, "Usuario bloqueado")
            db.session.commit()
            return render_template(
                LOGIN_TEMPLATE, form=form, show_captcha=needs_captcha
            )

        if not usuario.check_password(form.password.data):
            usuario.register_failed_login()
            _registrar_bitacora(usuario, False, "Contrasena incorrecta")
            db.session.commit()
            flash("Credenciales invalidas", "danger")
            return render_template(
                LOGIN_TEMPLATE,
                form=form,
                show_captcha=usuario.intentos_fallidos >= 3,
            )

        usuario.reset_login_attempts()
        usuario.token_2fa = f"{random.randint(0, 999999):06d}"
        usuario.expiracion_2fa = utc_now() + timedelta(minutes=5)
        db.session.commit()

        session["pending_2fa_user"] = usuario.id_usuario
        sent = _send_2fa_code_email(usuario)
        if sent:
            flash("Te enviamos un codigo de verificacion a tu correo.", "info")
        else:
            flash(f"Codigo 2FA temporal: {usuario.token_2fa}", "info")
        return redirect(url_for("auth.verify_2fa"))

    return render_template(LOGIN_TEMPLATE, form=form, show_captcha=False)


@auth_bp.route("/verify-2fa", methods=["GET", "POST"])
def verify_2fa():
    user_id = session.get("pending_2fa_user")
    if not user_id:
        return redirect(url_for(LOGIN_ENDPOINT))

    usuario = Usuario.query.get(user_id)
    if not usuario:
        session.pop("pending_2fa_user", None)
        return redirect(url_for(LOGIN_ENDPOINT))

    form = Verify2FAForm()
    if form.validate_on_submit():
        now = utc_now()
        if not usuario.expiracion_2fa or now > usuario.expiracion_2fa:
            flash("El codigo 2FA expiro", "danger")
            return redirect(url_for(LOGIN_ENDPOINT))

        if form.code.data != usuario.token_2fa:
            flash("Codigo 2FA incorrecto", "danger")
            return render_template("auth/verificacion_2fa.html", form=form)

        usuario.ultimo_acceso = now
        usuario.token_2fa = None
        usuario.expiracion_2fa = None
        _registrar_bitacora(usuario, True)
        db.session.commit()

        login_user(usuario)
        session.permanent = True
        session.pop("pending_2fa_user", None)
        flash("Bienvenido a SoftBakery", "success")
        return redirect(url_for(_resolve_home_by_role(usuario)))

    return render_template("auth/verificacion_2fa.html", form=form)


@auth_bp.route("/registro-cliente", methods=["GET", "POST"])
def registro_cliente():
    form = RegisterClientForm()
    if form.validate_on_submit():
        existe_user = Usuario.query.filter_by(
            username=form.username.data.strip()
        ).first()
        if existe_user:
            flash("El nombre de usuario ya existe", "danger")
            return render_template(REGISTER_TEMPLATE, form=form)

        existe_mail = Persona.query.filter_by(
            correo=form.correo.data.strip().lower()
        ).first()
        if existe_mail:
            flash("El correo ya esta registrado", "danger")
            return render_template(REGISTER_TEMPLATE, form=form)

        rol_cliente = Rol.query.filter_by(nombre="Cliente").first()
        persona = Persona(
            nombre=form.nombre.data.strip(),
            apellidos=form.apellidos.data.strip(),
            telefono=form.telefono.data.strip(),
            correo=form.correo.data.strip().lower(),
            direccion=form.direccion.data.strip(),
            ciudad=form.ciudad.data.strip(),
        )
        usuario = Usuario(
            persona=persona,
            id_rol=rol_cliente.id_rol,
            username=form.username.data.strip(),
            activo=True,
        )
        usuario.set_password(form.password.data)

        db.session.add(persona)
        db.session.add(usuario)
        db.session.commit()
        flash("Cuenta creada correctamente. Inicia sesion.", "success")
        return redirect(url_for(LOGIN_ENDPOINT))

    return render_template(REGISTER_TEMPLATE, form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Sesion finalizada", "info")
    return redirect(url_for("catalog.home"))
