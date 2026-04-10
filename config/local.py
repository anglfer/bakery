from config.development import DevelopmentConfig


class LocalConfig(DevelopmentConfig):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = "mysql+pymysql://root:alejandro.com13@127.0.0.1/softbakery2?charset=utf8mb4"
