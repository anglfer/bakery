from flask import Blueprint


catalog_bp = Blueprint("catalog", __name__, template_folder="templates")


from app.catalog import routes
