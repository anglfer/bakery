import json
import random
import re
import smtplib
import urllib.parse
import urllib.request
from datetime import timedelta
from email.message import EmailMessage

from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from app.auth import auth_bp
from app.auth.forms import LoginForm, RegisterClientForm, Verify2FAForm
from app.common.security import log_audit_event
from app.extensions import db
from app.models import BitacoraAcceso, Persona, Rol, Usuario, utc_now

LOGIN_TEMPLATE = "auth/login.html"
REGISTER_TEMPLATE = "auth/registro_cliente.html"
LOGIN_ENDPOINT = "auth.login"


def _resolve_home_by_role(usuario: Usuario) -> str:
    role_name = usuario.rol.nombre if usuario.rol else ""
    if role_name in {"Administrador", "Ventas", "Produccion"}:
        return "admin.dashboard"
    return "catalog.catalogo"


def _build_cliente_default_email(username: str) -> str:
    base = f"{username.lower()}@cliente.softbakery.local"
    email = base
    suffix = 1
    while Persona.query.filter_by(correo=email).first():
        email = f"{username.lower()}+{suffix}@cliente.softbakery.local"
        suffix += 1
    return email


def _is_valid_person_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-zÁÉÍÓÚáéíóúÑñÜü\s'\-]{2,}", value or ""))


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


def _verify_recaptcha(token: str) -> bool:
    if not token:
        return False
    secret = current_app.config.get("RECAPTCHA_PRIVATE_KEY", "")
    if not secret:
        return False
    data = {"secret": secret, "response": token}
    try:
        data_encoded = urllib.parse.urlencode(data).encode()
        with urllib.request.urlopen(
            "https://www.google.com/recaptcha/api/siteverify",
            data=data_encoded,
            timeout=10,
        ) as resp:
            result = json.loads(resp.read().decode())
    except Exception:
        return False

    if current_app.config.get("RECAPTCHA_VERSION", "v2") == "v3":
        score = float(result.get("score", 0.0))
        threshold = float(current_app.config.get("RECAPTCHA_SCORE_THRESHOLD", 0.5))
        return result.get("success", False) and score >= threshold

    return result.get("success", False)


def _redirect_authenticated_user():
    usuario_actual = Usuario.query.get(int(current_user.get_id()))
    if usuario_actual:
        return redirect(url_for(_resolve_home_by_role(usuario_actual)))
    return redirect(url_for("catalog.home"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return _redirect_authenticated_user()

    form = LoginForm()
    if form.validate_on_submit():
        usuario = Usuario.query.filter_by(
            username=form.username.data.strip(), activo=True
        ).first()
        if not usuario:
            flash("Credenciales invalidas", "danger")
            return render_template(
                LOGIN_TEMPLATE,
                form=form,
                show_captcha=True,
                recaptcha_site_key=current_app.config.get("RECAPTCHA_PUBLIC_KEY", ""),
            )

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
                LOGIN_TEMPLATE,
                form=form,
                show_captcha=needs_captcha,
                recaptcha_site_key=current_app.config.get("RECAPTCHA_PUBLIC_KEY", ""),
            )
        # Verify reCAPTCHA if required
        if needs_captcha:
            captcha_token = request.form.get("g-recaptcha-response", "")
            if not _verify_recaptcha(captcha_token):
                flash("Por favor completa el reCAPTCHA", "danger")
                return render_template(
                    LOGIN_TEMPLATE,
                    form=form,
                    show_captcha=True,
                    recaptcha_site_key=current_app.config.get(
                        "RECAPTCHA_PUBLIC_KEY", ""
                    ),
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
                recaptcha_site_key=current_app.config.get("RECAPTCHA_PUBLIC_KEY", ""),
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

    return render_template(
        LOGIN_TEMPLATE,
        form=form,
        show_captcha=True,
        recaptcha_site_key=current_app.config.get("RECAPTCHA_PUBLIC_KEY", ""),
    )


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
    es_registro_nuevo = session.get("es_registro_nuevo", False)

    if form.validate_on_submit():
        now = utc_now()
        if not usuario.expiracion_2fa or now > usuario.expiracion_2fa:
            flash("El codigo 2FA expiro", "danger")
            return redirect(url_for(LOGIN_ENDPOINT))

        if form.code.data != usuario.token_2fa:
            flash("Codigo 2FA incorrecto", "danger")
            return render_template(
                "auth/verificacion_2fa.html",
                form=form,
                es_registro_nuevo=es_registro_nuevo,
            )

        usuario.ultimo_acceso = now
        usuario.token_2fa = None
        usuario.expiracion_2fa = None

        # Si es registro nuevo, marcar como verificado y activar
        if es_registro_nuevo:
            usuario.activo = True
            log_audit_event(
                "CLIENTE_EMAIL_VERIFICADO",
                f"id_usuario={usuario.id_usuario}",
            )
            session.pop("es_registro_nuevo", None)

        _registrar_bitacora(usuario, True)
        db.session.commit()

        login_user(usuario)
        session.permanent = True
        session.pop("pending_2fa_user", None)

        if es_registro_nuevo:
            flash("Bienvenido a SoftBakery. Tu cuenta ha sido activada.", "success")
        else:
            flash("Bienvenido a SoftBakery", "success")
        return redirect(url_for(_resolve_home_by_role(usuario)))

    return render_template(
        "auth/verificacion_2fa.html", form=form, es_registro_nuevo=es_registro_nuevo
    )


@auth_bp.route("/registro-cliente", methods=["GET", "POST"])
def registro_cliente():
    form = RegisterClientForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        correo = form.correo.data.strip()

        # Verificar que el usuario no exista
        existe_user = Usuario.query.filter(
            db.func.lower(Usuario.username) == username.lower()
        ).first()
        if existe_user:
            flash(
                "El nombre de usuario ya existe. Elige uno diferente.",
                "danger",
            )
            return render_template(REGISTER_TEMPLATE, form=form)

        # Verificar que el correo no exista
        existe_correo = Persona.query.filter(
            db.func.lower(Persona.correo) == correo.lower()
        ).first()
        if existe_correo:
            flash(
                "El correo ya está registrado. Usa otro correo.",
                "danger",
            )
            return render_template(REGISTER_TEMPLATE, form=form)

        rol_cliente = Rol.query.filter_by(
            nombre="Cliente",
            activo=True,
        ).first()
        if not rol_cliente:
            flash(
                "No fue posible crear la cuenta: rol Cliente no configurado.",
                "danger",
            )
            return render_template(REGISTER_TEMPLATE, form=form)

        nombre = form.nombre.data.strip()
        apellidos = form.apellidos.data.strip()
        telefono = form.telefono.data.strip()

        if not (_is_valid_person_name(nombre) and _is_valid_person_name(apellidos)):
            flash("Nombre o apellido invalido.", "danger")
            return render_template(REGISTER_TEMPLATE, form=form)

        persona = Persona(
            nombre=nombre,
            apellidos=apellidos,
            telefono=telefono,
            correo=correo,
            direccion="No especificada",
            ciudad="No especificada",
        )
        usuario = Usuario(
            persona=persona,
            id_rol=rol_cliente.id_rol,
            username=username,
            activo=True,
        )
        usuario.set_password(form.password.data)
        usuario.token_2fa = f"{random.randint(0, 999999):06d}"
        usuario.expiracion_2fa = utc_now() + timedelta(minutes=5)

        db.session.add(persona)
        db.session.add(usuario)
        db.session.commit()

        # Intentar enviar código por email
        sent = _send_2fa_code_email(usuario)
        if sent:
            log_audit_event(
                "CLIENTE_REGISTRADO_PENDIENTE_VERIFICACION",
                f"id_usuario={usuario.id_usuario}; username={usuario.username}",
            )
            session["pending_2fa_user"] = usuario.id_usuario
            session["es_registro_nuevo"] = True
            flash(
                "Cuenta creada. Te enviamos un código de verificación a tu correo.",
                "info",
            )
            return redirect(url_for("auth.verify_2fa"))
        else:
            # Si no se puede enviar email, mostrar error y marcarlo como no activo
            usuario.activo = False
            db.session.commit()
            flash(
                "Error al enviar correo de verificación. Contacta a soporte.",
                "danger",
            )
            return render_template(REGISTER_TEMPLATE, form=form)

    return render_template(REGISTER_TEMPLATE, form=form)


@auth_bp.route("/mi-cuenta", methods=["GET", "POST"])
@login_required
def mi_cuenta():
    usuario = Usuario.query.get_or_404(int(current_user.get_id()))
    if not usuario.rol or usuario.rol.nombre != "Cliente":
        flash("Esta seccion solo esta disponible para clientes.", "warning")
        return redirect(url_for(_resolve_home_by_role(usuario)))

    if not usuario.activo:
        logout_user()
        flash("Tu cuenta esta inactiva. Contacta a soporte.", "danger")
        return redirect(url_for(LOGIN_ENDPOINT))

    persona = usuario.persona
    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        apellidos = (request.form.get("apellidos") or "").strip()
        telefono = (request.form.get("telefono") or "").strip()
        ciudad = (request.form.get("ciudad") or "").strip()
        direccion = (request.form.get("direccion") or "").strip()

        if not all([nombre, apellidos, telefono]):
            flash("Nombre, apellido y telefono son obligatorios.", "warning")
            return redirect(url_for("auth.mi_cuenta"))

        if not (_is_valid_person_name(nombre) and _is_valid_person_name(apellidos)):
            flash("Nombre o apellido invalido.", "warning")
            return redirect(url_for("auth.mi_cuenta"))

        if not re.fullmatch(r"[\d\s\-\+\(\)]{7,30}", telefono):
            flash("Telefono invalido.", "warning")
            return redirect(url_for("auth.mi_cuenta"))

        persona.nombre = nombre
        persona.apellidos = apellidos
        persona.telefono = telefono
        persona.ciudad = ciudad or "No especificada"
        persona.direccion = direccion or "No especificada"
        db.session.commit()
        log_audit_event(
            "CLIENTE_ACTUALIZA_DATOS",
            f"id_usuario={usuario.id_usuario}; username={usuario.username}",
        )
        flash("Datos actualizados correctamente.", "success")
        return redirect(url_for("auth.mi_cuenta"))

    return render_template("auth/mi_cuenta.html", usuario=usuario)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Sesion finalizada", "info")
    return redirect(url_for("catalog.home"))
