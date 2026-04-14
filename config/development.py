import os
from datetime import timedelta


class DevelopmentConfig:
    DEBUG = True
    TESTING = False
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-softbakery-key")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "mysql+pymysql://root:alejandro.com13@127.0.0.1/softbakery2?charset=utf8mb4",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=10)
    SESSION_REFRESH_EACH_REQUEST = True
    REMEMBER_COOKIE_HTTPONLY = True
    WTF_CSRF_ENABLED = True
    RECAPTCHA_PUBLIC_KEY = os.getenv("RECAPTCHA_SITE_KEY", "")
    RECAPTCHA_PRIVATE_KEY = os.getenv("RECAPTCHA_SECRET", "")
    RECAPTCHA_VERSION = os.getenv("RECAPTCHA_VERSION", "v2")
    RECAPTCHA_SCORE_THRESHOLD = float(os.getenv("RECAPTCHA_SCORE_THRESHOLD", "0.5"))
    MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", MAIL_USERNAME)
    AUTO_DB_INIT = os.getenv("AUTO_DB_INIT", "false").lower() == "true"
    MONGO_LOGS_ENABLED = os.getenv("MONGO_LOGS_ENABLED", "false").lower() == "true"
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
    MONGO_LOGS_DB = os.getenv("MONGO_LOGS_DB", "softbakery")
    MONGO_LOGS_COLLECTION = os.getenv("MONGO_LOGS_COLLECTION", "app_logs")
    MONGO_LOGS_TIMEOUT_MS = int(os.getenv("MONGO_LOGS_TIMEOUT_MS", "2000"))
