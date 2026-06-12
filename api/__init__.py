# api/__init__.py
from api.auth      import auth_bp
from api.products  import products_bp
from api.inventory import inventory_bp
from api.receipts  import receipts_bp
from api.clients   import clients_bp
from api.suppliers import suppliers_bp
from api.expenses  import expenses_bp
from api.reports   import reports_bp
from api.schemas   import ma
 
def register_api(app):
    """Call this from app.py to mount all API blueprints."""
    ma.init_app(app)  # initialise marshmallow
    app.register_blueprint(auth_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(receipts_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(suppliers_bp)
    app.register_blueprint(expenses_bp)
    app.register_blueprint(reports_bp)
