import os
from datetime import timedelta


class TestingConfig:
    DEBUG = False
    TESTING = True
    SECRET_KEY = os.getenv("SECRET_KEY", "test-softbakery-key")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "TEST_DATABASE_URL",
        "sqlite:///softbakery_test.db",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=10)
    SESSION_REFRESH_EACH_REQUEST = True
    MONGO_LOGS_ENABLED = os.getenv("MONGO_LOGS_ENABLED", "false").lower() == "true"
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
    MONGO_LOGS_DB = os.getenv("MONGO_LOGS_DB", "softbakery")
    MONGO_LOGS_COLLECTION = os.getenv(
        "MONGO_LOGS_COLLECTION",
        "app_logs",
    )
    MONGO_LOGS_TIMEOUT_MS = int(os.getenv("MONGO_LOGS_TIMEOUT_MS", "2000"))
