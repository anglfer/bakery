from config.development import DevelopmentConfig


class LocalConfig(DevelopmentConfig):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = "mysql+pymysql://root:root@localhost/softbakery2?charset=utf8mb4"
