import os


class TestingConfig:
    DEBUG = False
    TESTING = True
    SECRET_KEY = os.getenv("SECRET_KEY", "test-softbakery-key")
    SQLALCHEMY_DATABASE_URI = os.getenv("TEST_DATABASE_URL", "sqlite:///softbakery_test.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
