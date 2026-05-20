from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from extensions import db
from sqlalchemy import CheckConstraint


# ------------------
# Users (as-is)
# ------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), nullable=False, unique=True)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(50), nullable=False, default='employee')

    # --- password utilities ---
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

# ------------------
# Master data
# ------------------
class Client(db.Model):
    __tablename__ = "client"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)           # "Walk-in" allowed
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    is_walk_in = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    invoices = db.relationship("Receipt", backref="client", lazy=True)
    payments = db.relationship("Payment", backref="client", lazy=True)

class Supplier(db.Model):
    __tablename__ = "supplier"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    address = db.Column(db.String(255))
    items_supplied = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)



class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    
    # Pricing Fields - Added a constraint to ensure selling_price is never negative
    buying_price = db.Column(db.Float, nullable=False, server_default="0.0") 
    selling_price = db.Column(db.Float, nullable=False, server_default="0.0")
    
    # Stock Management
    quantity = db.Column(db.Integer, nullable=False, default=0) 
    min_stock_level = db.Column(db.Integer, default=5)
    
    barcode = db.Column(db.String(50), nullable=True, unique=True, index=True)
    expiry_date = db.Column(db.Date, nullable=True)
    category = db.Column(db.String(50), index=True, default="General")

    # ENHANCEMENT: Cascade delete to clean up history when a product is removed
    movements = db.relationship(
        'StockMovement', 
        backref='target_product', 
        lazy=True, 
        cascade="all, delete-orphan"
    )

    # DATABASE CONSTRAINT: Prevents negative prices at the SQL level
    __table_args__ = (
        CheckConstraint('buying_price >= 0', name='check_buying_price_positive'),
        CheckConstraint('selling_price >= 0', name='check_selling_price_positive'),
    )

    # HELPER PROPERTY: Call this in your templates like {{ product.profit_margin }}
    @property
    def profit_margin(self):
        return round(self.selling_price - self.buying_price, 2)

    def __repr__(self):
        return f'<Product {self.name}>'
# ------------------
# Inventory movements (canonical stock ledger)
# ------------------
class StockMovement(db.Model):
    __tablename__ = "stock_movement"
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity_change = db.Column(db.Integer, nullable=False)    # negative for sale, positive for purchase
    reason = db.Column(db.String(30), nullable=False)          # sale, purchase, adjustment, return, reversal
    reference_type = db.Column(db.String(30))                  # receipt, supplier_invoice, manual, payment_reversal
    reference_id = db.Column(db.Integer)                       # id of the reference_type table
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product')

# ------------------
# Sales lines (kept; link product_id optional for legacy)
# ------------------


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_name = db.Column(db.String(100), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Link to product (optional but good for accuracy)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    product = db.relationship('Product', backref='sales', lazy=True)

    # Link to receipt
    receipt_id = db.Column(db.Integer, db.ForeignKey('receipt.id'))

    # Link to client (Hybrid: Walk-in OR registered client)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=True)
    client = db.relationship('Client', backref='sales', lazy=True)

    quantity_sold = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    total_price = db.Column(db.Float, nullable=False)

    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    # NEW FIELDS
    discount = db.Column(db.Float, default=0.0)     # Discount amount (Ksh or % depending on logic)
    tax_rate = db.Column(db.Float, default=0.16)    # Default VAT 16% in Kenya
    

    # Helper method to calculate totals
    def calculate_total(self):
        """Calculate total price after discount and tax"""
        subtotal = self.unit_price * self.quantity_sold

        # If discount is percentage (e.g., 10 = 10%)
        discount_amount = subtotal * (self.discount / 100) if self.discount <= 100 else self.discount

        subtotal_after_discount = subtotal - discount_amount
        tax_amount = subtotal_after_discount * self.tax_rate
        return round(subtotal_after_discount + tax_amount, 2)
    
    @property
    def estimated_profit(self):
        if self.product and self.product.buying_price:
            # Profit = Total Revenue - (Cost per item * quantity)
            return self.total_price - (self.quantity_sold * self.product.buying_price)
        return 0.0


#Expenses
class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(150), nullable=False)  # e.g., Rent, Salary, Utilities
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=True)       # e.g., Operations, Marketing
    date = db.Column(db.Date, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Expense {self.description} - {self.amount}>'


# ------------------
# Cart (as-is)
# ------------------
class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    sale_id = db.Column(db.Integer, db.ForeignKey('sale.id'))
    product_name = db.Column(db.String(100))
    added_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product')
    user = db.relationship('User', backref='cart_items')


class Receipt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    customer_name = db.Column(db.String(255))
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'))
    status = db.Column(db.String(20), default='Open')

    # Financials
    total_amount = db.Column(db.Float, default=0.0)    # Sum of all items (Gross)
    amount_paid = db.Column(db.Float, default=0.0)     # Total payments made
    balance_due = db.Column(db.Float, default=0.0)     # amount remaining
    
    # NEW: Memory for the Receipt UI
    cash_received = db.Column(db.Float, default=0.0)
    change_given = db.Column(db.Float, default=0.0)
    discount = db.Column(db.Float, default=0.0)

    sales = db.relationship('Sale', backref='receipt', lazy=True, cascade="all, delete-orphan")
    payments = db.relationship('Payment', backref='receipt', lazy=True, cascade="all, delete-orphan")

    def recompute_balance(self):
        if self.status == 'Cancelled':
            return
            
        # Standard payment sum
        paid = sum(p.amount for p in self.payments if p.status in ('Success', 'ReversedSuccess') and not p.is_reversal)
        # Handle returns/reversals
        reversed_return = sum(p.amount for p in self.payments if p.is_reversal and p.status in ('Success', 'ReversedSuccess'))
        
        self.amount_paid = round(paid - reversed_return, 2)
        self.balance_due = round((self.total_amount or 0) - self.amount_paid, 2)

        # Update Status based on balance
        if self.balance_due <= 0:
            self.status = 'Paid'
            self.balance_due = 0.0
        elif self.amount_paid > 0:
            self.status = 'Partially Paid'
        else:
            self.status = 'Open'
    
# ------------------
# Payments (cash + mpesa + reversals)
# ------------------
class Payment(db.Model):
    __tablename__ = "payment"
    id = db.Column(db.Integer, primary_key=True)
    receipt_id = db.Column(db.Integer, db.ForeignKey('receipt.id'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'))     # denormalized for quick reports

    method = db.Column(db.String(20), nullable=False)                  # Cash, Mpesa
    amount = db.Column(db.Float, nullable=False)

    status = db.Column(db.String(20), default='Pending')               # Pending, Success, Failed, ReversedPending, ReversedSuccess, ReversedFailed
    reference = db.Column(db.String(120))                              # invoice_id / transaction id / receipt no.
    notes = db.Column(db.String(255))

    # reversal tracking
    is_reversal = db.Column(db.Boolean, default=False)
    reversal_of_id = db.Column(db.Integer, db.ForeignKey('payment.id'))
    reversed_by = db.relationship('Payment', remote_side=[id])

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ------------------
# IntaSend / M-Pesa raw payloads (link to Payment)
# ------------------
class MpesaPayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20))
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20))              # Pending, Success, Failed, Reversed
    reference = db.Column(db.String(100))          # IntaSend invoice_id / mpesa receipt
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # link to unified payment row
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'))
    payment = db.relationship('Payment', backref='mpesa_record')

# ------------------
# Purchases from suppliers (to increase stock)
# ------------------
class SupplierInvoice(db.Model):
    __tablename__ = "supplier_invoice"
    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'), nullable=False)
    total_amount = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    supplier = db.relationship('Supplier', backref='invoices')
    items = db.relationship('SupplierInvoiceItem', backref='supplier_invoice', cascade="all, delete-orphan")

class SupplierInvoiceItem(db.Model):
    __tablename__ = "supplier_invoice_item"
    id = db.Column(db.Integer, primary_key=True)
    supplier_invoice_id = db.Column(db.Integer, db.ForeignKey('supplier_invoice.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_cost = db.Column(db.Float, nullable=False)
    line_total = db.Column(db.Float, nullable=False)

    product = db.relationship('Product')
