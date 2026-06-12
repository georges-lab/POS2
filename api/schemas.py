from flask_marshmallow import Marshmallow
from marshmallow import fields
from models import (
    User, Product, Sale, Receipt, Payment, Client,
    Supplier, StockMovement, SupplierInvoice, Expense
)
 
ma = Marshmallow()
 
# ── USER ────────────────────────────────────────────
class UserSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = User
        load_instance = True
        exclude = ('password_hash',)  # NEVER expose password hash
 
 
# ── PRODUCT ─────────────────────────────────────────
class ProductSchema(ma.SQLAlchemyAutoSchema):
    profit_margin = fields.Float(dump_only=True)  # computed property
    class Meta:
        model = Product
        load_instance = True
        include_fk = True
 
 
# ── STOCK MOVEMENT ──────────────────────────────────
class StockMovementSchema(ma.SQLAlchemyAutoSchema):
    product_name = fields.Method('get_product_name')
    def get_product_name(self, obj):
        return obj.product.name if obj.product else None
    class Meta:
        model = StockMovement
        load_instance = True
        include_fk = True
 
 
# ── SALE ────────────────────────────────────────────
class SaleSchema(ma.SQLAlchemyAutoSchema):
    estimated_profit = fields.Float(dump_only=True)
    class Meta:
        model = Sale
        load_instance = True
        include_fk = True
 
 
# ── PAYMENT ─────────────────────────────────────────
class PaymentSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = Payment
        load_instance = True
        include_fk = True
 
 
# ── RECEIPT (with nested sales and payments) ─────────
class ReceiptSchema(ma.SQLAlchemyAutoSchema):
    sales    = fields.Nested(SaleSchema, many=True, dump_only=True)
    payments = fields.Nested(PaymentSchema, many=True, dump_only=True)
    class Meta:
        model = Receipt
        load_instance = True
        include_fk = True
 
 
# ── CLIENT ──────────────────────────────────────────
class ClientSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = Client
        load_instance = True
 
 
# ── SUPPLIER ────────────────────────────────────────
class SupplierSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = Supplier
        load_instance = True
 
 
# ── EXPENSE ─────────────────────────────────────────
class ExpenseSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = Expense
        load_instance = True
 
 
# ── SINGLETON & LIST INSTANCES ──────────────────────
# Use these in your route files — never re-instantiate manually
user_schema          = UserSchema()
product_schema       = ProductSchema()
products_schema      = ProductSchema(many=True)
movement_schema      = StockMovementSchema()
movements_schema     = StockMovementSchema(many=True)
sale_schema          = SaleSchema()
sales_schema         = SaleSchema(many=True)
payment_schema       = PaymentSchema()
payments_schema      = PaymentSchema(many=True)
receipt_schema       = ReceiptSchema()
receipts_schema      = ReceiptSchema(many=True)
client_schema        = ClientSchema()
clients_schema       = ClientSchema(many=True)
supplier_schema      = SupplierSchema()
suppliers_schema     = SupplierSchema(many=True)
expense_schema       = ExpenseSchema()
expenses_schema      = ExpenseSchema(many=True)
