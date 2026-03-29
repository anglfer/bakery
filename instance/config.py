import os


ENV_CONFIG_MAP = {
    "development": "config.development.DevelopmentConfig",
    "local": "config.local.LocalConfig",
    "testing": "config.testing.TestingConfig",
    "production": "config.production.ProductionConfig",
}


def get_config_path() -> str:
    env_name = os.getenv("FLASK_ENV", "development")
    return ENV_CONFIG_MAP.get(env_name, ENV_CONFIG_MAP["development"])
