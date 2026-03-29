from flask import Blueprint


sales_bp = Blueprint("sales", __name__, template_folder="templates")


from app.sales import routes
