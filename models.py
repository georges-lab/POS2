from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from extensions import db
from sqlalchemy import CheckConstraint



# ══════════════════════════════════════════════════════════════
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from extensions import db
from sqlalchemy import CheckConstraint

# ══════════════════════════════════════════════════════════════
#  USERS
# ══════════════════════════════════════════════════════════════
class User(UserMixin, db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(150), nullable=False, unique=True)
    password_hash = db.Column(db.String(200), nullable=False)

    # ── ROLES ──────────────────────────────────────────────────
    # Default overarching template role groups
    # admin        — full access
    # supervisor   — reports, discounts, refunds, no user mgmt
    # stock_manager— products, supplier purchases, no financials
    # cashier      — cart + checkout only
    role = db.Column(db.String(50), nullable=False, default='cashier')

    # ── GRANULAR TOGGLEABLE PERMISSIONS ─────────────────────────
    # Adding dedicated database columns allows individual overrides per-user
    perm_process_sales    = db.Column(db.Boolean, default=True, server_default='1')
    perm_view_reports     = db.Column(db.Boolean, default=False, server_default='0')
    perm_give_discount    = db.Column(db.Boolean, default=False, server_default='0')
    perm_process_refund   = db.Column(db.Boolean, default=False, server_default='0')
    perm_manage_products  = db.Column(db.Boolean, default=False, server_default='0')
    perm_manage_suppliers = db.Column(db.Boolean, default=False, server_default='0')
    perm_manage_users     = db.Column(db.Boolean, default=False, server_default='0')
    perm_view_profit      = db.Column(db.Boolean, default=False, server_default='0')
    
    # Custom numeric enforcement parameters per profile
    discount_limit_percentage = db.Column(db.Float, default=0.0, server_default='0.0')

    # ── STAFF PROFILE (Tier-1 #1) ──────────────────────────────
    full_name  = db.Column(db.String(150))
    phone      = db.Column(db.String(30))
    id_number  = db.Column(db.String(30))          # National ID
    hire_date  = db.Column(db.Date)
    salary     = db.Column(db.Float, default=0.0)
    is_active  = db.Column(db.Boolean, default=True, server_default='1', nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    shifts = db.relationship('Shift', backref='user', lazy=True,
                             foreign_keys='Shift.user_id')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    # ── ROLE SYNC UTILITY HELPER ───────────────────────────────
    def assign_default_permissions_by_role(self, target_role):
        """
        Helper method to instantly set base values across toggle parameters
        when changing or setting up a user's role from a dashboard interface.
        """
        self.role = target_role.lower()
        
        # Reset base options
        self.perm_process_sales = True
        self.perm_view_reports = False
        self.perm_give_discount = False
        self.perm_process_refund = False
        self.perm_manage_products = False
        self.perm_manage_suppliers = False
        self.perm_manage_users = False
        self.perm_view_profit = False
        self.discount_limit_percentage = 0.0

        if self.role == 'admin':
            self.perm_view_reports = True
            self.perm_give_discount = True
            self.perm_process_refund = True
            self.perm_manage_products = True
            self.perm_manage_suppliers = True
            self.perm_manage_users = True
            self.perm_view_profit = True
            self.discount_limit_percentage = 100.0  # Unlimited
            
        elif self.role in ('supervisor', 'manager'):
            self.perm_view_reports = True
            self.perm_give_discount = True
            self.perm_process_refund = True
            self.perm_view_profit = True
            self.discount_limit_percentage = 15.0   # Configurable ceiling threshold
            
        elif self.role == 'stock_manager':
            self.perm_manage_products = True
            self.perm_manage_suppliers = True

    # ── DYNAMIC PERMISSION EVALUATORS ───────────────────────────
    # These properties retain their existing names so your views/templates 
    # don't break. They check the explicit column OR fallback to role rules.
    @property
    def can_view_reports(self):
        if self.perm_view_reports:
            return True
        return self.role in ('admin', 'manager', 'supervisor')

    @property
    def can_manage_products(self):
        if self.perm_manage_products:
            return True
        return self.role in ('admin', 'manager', 'supervisor', 'stock_manager')

    @property
    def can_manage_suppliers(self):
        if self.perm_manage_suppliers:
            return True
        return self.role in ('admin', 'manager', 'supervisor', 'stock_manager')

    @property
    def can_give_discount(self):
        if self.perm_give_discount:
            return True
        return self.role in ('admin', 'manager', 'supervisor')

    @property
    def can_process_refund(self):
        if self.perm_process_refund:
            return True
        return self.role in ('admin', 'manager', 'supervisor')

    @property
    def can_manage_users(self):
        if self.perm_manage_users:
            return True
        return self.role == 'admin'

    @property
    def can_view_profit(self):
        if self.perm_view_profit:
            return True
        return self.role in ('admin', 'manager', 'supervisor')

    @property
    def is_admin(self):
        if self.perm_manage_users: # Or explicit admin string check
            return True
        return self.role == 'admin'
# ══════════════════════════════════════════════════════════════



# ══════════════════════════════════════════════════════════════
#  SHIFTS  (Tier-1 #1 — Staff & Shift Tracking)
# ══════════════════════════════════════════════════════════════
class Shift(db.Model):
    __tablename__ = 'shift'
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    opened_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))  # admin who opened

    opened_at  = db.Column(db.DateTime, default=datetime.utcnow)
    closed_at  = db.Column(db.DateTime, nullable=True)
    status     = db.Column(db.String(20), default='Open')           # Open / Closed

    # Cash reconciliation
    opening_float    = db.Column(db.Float, default=0.0)   # cash placed in till at open
    expected_cash    = db.Column(db.Float, default=0.0)   # computed: float + cash sales
    actual_cash      = db.Column(db.Float, default=0.0)   # physically counted at close
    cash_variance    = db.Column(db.Float, default=0.0)   # actual - expected
    closing_notes    = db.Column(db.String(500))

    # Shift totals (computed at close)
    total_sales      = db.Column(db.Float, default=0.0)
    total_cash       = db.Column(db.Float, default=0.0)
    total_mpesa      = db.Column(db.Float, default=0.0)
    total_discounts  = db.Column(db.Float, default=0.0)
    transaction_count= db.Column(db.Integer, default=0)

    opened_by = db.relationship('User', foreign_keys=[opened_by_id])


# ══════════════════════════════════════════════════════════════
#  CLIENTS  (extended for Tier-1 #3 — Loyalty & Credit)
# ══════════════════════════════════════════════════════════════
class Client(db.Model):
    __tablename__ = 'client'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(120), nullable=False)
    phone      = db.Column(db.String(30))
    email      = db.Column(db.String(120))
    is_walk_in = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ── LOYALTY ────────────────────────────────────────────────
    loyalty_points   = db.Column(db.Integer, default=0)
    total_spent      = db.Column(db.Float, default=0.0)    # lifetime spend

    # ── CREDIT ACCOUNT ─────────────────────────────────────────
    credit_limit     = db.Column(db.Float, default=0.0)    # 0 = cash-only
    credit_balance   = db.Column(db.Float, default=0.0)    # current debt (positive = owes)
    credit_enabled   = db.Column(db.Boolean, default=False)

    invoices          = db.relationship('Receipt',        backref='client', lazy=True)
    payments          = db.relationship('Payment',        backref='client', lazy=True)
    loyalty_txns      = db.relationship('LoyaltyTransaction', backref='client', lazy=True,
                                        cascade='all, delete-orphan')
    credit_payments   = db.relationship('ClientCreditPayment', backref='client', lazy=True,
                                        cascade='all, delete-orphan')

    @property
    def available_credit(self):
        return round(max(0.0, (self.credit_limit or 0) - (self.credit_balance or 0)), 2)


# ── Loyalty transactions ───────────────────────────────────────
class LoyaltyTransaction(db.Model):
    __tablename__ = 'loyalty_transaction'
    id         = db.Column(db.Integer, primary_key=True)
    client_id  = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    points     = db.Column(db.Integer, nullable=False)     # positive = earn, negative = redeem
    reason     = db.Column(db.String(100))                 # 'sale #123', 'redemption', 'manual'
    reference_id = db.Column(db.Integer)                   # receipt_id
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ── Client credit payments (paying off debt) ───────────────────
class ClientCreditPayment(db.Model):
    __tablename__ = 'client_credit_payment'
    id         = db.Column(db.Integer, primary_key=True)
    client_id  = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False)
    amount     = db.Column(db.Float, nullable=False)
    method     = db.Column(db.String(20))                  # Cash, Mpesa
    reference  = db.Column(db.String(120))
    recorded_by= db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    recorder   = db.relationship('User', foreign_keys=[recorded_by])


# ══════════════════════════════════════════════════════════════
#  SUPPLIER  (unchanged — cascade already set in previous fix)
# ══════════════════════════════════════════════════════════════
class Supplier(db.Model):
    __tablename__ = 'supplier'
    id             = db.Column(db.Integer, primary_key=True)
    name           = db.Column(db.String(120), nullable=False, unique=True)
    phone          = db.Column(db.String(30))
    email          = db.Column(db.String(120))
    address        = db.Column(db.String(255))
    items_supplied = db.Column(db.String(255))
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    invoices = db.relationship('SupplierInvoice', backref='supplier',
                               lazy=True, cascade='all, delete-orphan')
    purchase_orders = db.relationship('PurchaseOrder', backref='supplier',
                                      lazy=True, cascade='all, delete-orphan')


# ══════════════════════════════════════════════════════════════
#  PURCHASE ORDERS  (Tier-1 #2)
# ══════════════════════════════════════════════════════════════
class PurchaseOrder(db.Model):
    __tablename__ = 'purchase_order'
    id          = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'), nullable=False)
    created_by  = db.Column(db.Integer, db.ForeignKey('user.id'))
    po_number   = db.Column(db.String(40), unique=True)    # e.g. PO-2026-0001
    status      = db.Column(db.String(30), default='Draft')
    # Draft → Sent → Partially Received → Received → Cancelled
    notes       = db.Column(db.String(500))
    expected_date = db.Column(db.Date, nullable=True)
    total_amount  = db.Column(db.Float, default=0.0)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    sent_at       = db.Column(db.DateTime, nullable=True)
    received_at   = db.Column(db.DateTime, nullable=True)

    creator = db.relationship('User', foreign_keys=[created_by])
    items   = db.relationship('PurchaseOrderItem', backref='purchase_order',
                              lazy=True, cascade='all, delete-orphan')


class PurchaseOrderItem(db.Model):
    __tablename__ = 'purchase_order_item'
    id                = db.Column(db.Integer, primary_key=True)
    purchase_order_id = db.Column(db.Integer, db.ForeignKey('purchase_order.id'), nullable=False)
    product_id        = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity_ordered  = db.Column(db.Integer, nullable=False)
    quantity_received = db.Column(db.Integer, default=0)
    expected_cost     = db.Column(db.Float, nullable=False)
    

    product = db.relationship('Product')

    @property
    def quantity_outstanding(self):
        return max(0, self.quantity_ordered - (self.quantity_received or 0))

    @property
    def is_fully_received(self):
        return (self.quantity_received or 0) >= self.quantity_ordered


# ══════════════════════════════════════════════════════════════
#  PRODUCTS  (extended: reorder_point for Tier-2 #7)
# ══════════════════════════════════════════════════════════════
class Product(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(100), nullable=False, index=True)
    buying_price  = db.Column(db.Float, nullable=False, server_default='0.0')
    selling_price = db.Column(db.Float, nullable=False, server_default='0.0')
    quantity      = db.Column(db.Integer, nullable=False, default=0)
    min_stock_level  = db.Column(db.Integer, default=5)
    reorder_point    = db.Column(db.Integer, default=10)  # trigger auto-PO draft
    barcode       = db.Column(db.String(50), nullable=True, unique=True, index=True)
    expiry_date   = db.Column(db.Date, nullable=True)
    category      = db.Column(db.String(50), index=True, default='General')

    # ── VARIANTS (Tier-2 #8) ────────────────────────────────────
    # unit_type: 'single' | 'bulk'
    # bulk_size: how many singles in one bulk unit (e.g. 24 for a crate)
    unit_type  = db.Column(db.String(20), default='single')
    bulk_size  = db.Column(db.Integer, default=1)
    bulk_buying_price = db.Column(db.Float, default=0.0)

    # preferred supplier for auto-PO drafts
    preferred_supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'), nullable=True)
    preferred_supplier = db.relationship('Supplier', foreign_keys=[preferred_supplier_id])

    movements = db.relationship('StockMovement', back_populates='product',
                                lazy=True, cascade='all, delete-orphan')

    __table_args__ = (
        CheckConstraint('buying_price >= 0',  name='check_buying_price_positive'),
        CheckConstraint('selling_price >= 0', name='check_selling_price_positive'),
    )

    @property
    def profit_margin(self):
        return round(self.selling_price - self.buying_price, 2)

    @property
    def needs_reorder(self):
        return self.quantity <= (self.reorder_point or self.min_stock_level or 0)

    def __repr__(self):
        return f'<Product {self.name}>'


# ══════════════════════════════════════════════════════════════
#  STOCK MOVEMENT  (unchanged)
# ══════════════════════════════════════════════════════════════
class StockMovement(db.Model):
    __tablename__ = 'stock_movement'
    id              = db.Column(db.Integer, primary_key=True)
    product_id      = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity_change = db.Column(db.Integer, nullable=False)
    reason          = db.Column(db.String(30), nullable=False)
    reference_type  = db.Column(db.String(30))
    reference_id    = db.Column(db.Integer)
    timestamp       = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', back_populates='movements')


# ══════════════════════════════════════════════════════════════
#  SALE  (unchanged)
# ══════════════════════════════════════════════════════════════
class Sale(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    product_name = db.Column(db.String(100), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    product_id   = db.Column(db.Integer, db.ForeignKey('product.id'))
    product      = db.relationship('Product', backref='sales', lazy=True)
    receipt_id   = db.Column(db.Integer, db.ForeignKey('receipt.id'))
    client_id    = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=True)
    client       = db.relationship('Client', backref='sales', lazy=True)
    quantity_sold= db.Column(db.Integer, nullable=False)
    unit_price   = db.Column(db.Float, nullable=False)
    total_price  = db.Column(db.Float, nullable=False)
    timestamp    = db.Column(db.DateTime, default=datetime.utcnow)
    discount     = db.Column(db.Float, default=0.0)
    tax_rate     = db.Column(db.Float, default=0.16)
    tax_amount   = db.Column(db.Float, default=0.0)

    def calculate_total(self):
        subtotal = round((self.unit_price or 0) * (self.quantity_sold or 0), 2)
        discount_amount = min(round(self.discount or 0, 2), subtotal)
        return round(subtotal - discount_amount, 2)

    @property
    def tax_component(self):
        base = self.total_amount or self.total_price or 0
        return round(base * 0.16 / 1.16, 2)

    @property
    def estimated_profit(self):
        if self.product and self.product.buying_price:
            return self.total_price - (self.quantity_sold * self.product.buying_price)
        return 0.0


# ══════════════════════════════════════════════════════════════
#  RETURNS  (Tier-2 #9)
# ══════════════════════════════════════════════════════════════
class Return(db.Model):
    __tablename__ = 'return'
    id            = db.Column(db.Integer, primary_key=True)
    receipt_id    = db.Column(db.Integer, db.ForeignKey('receipt.id'), nullable=False)
    processed_by  = db.Column(db.Integer, db.ForeignKey('user.id'))
    reason        = db.Column(db.String(255))
    refund_method = db.Column(db.String(20))   # Cash, Mpesa, Credit
    refund_amount = db.Column(db.Float, default=0.0)
    status        = db.Column(db.String(20), default='Pending')  # Pending, Completed, Rejected
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    receipt   = db.relationship('Receipt', backref='returns')
    processor = db.relationship('User',    foreign_keys=[processed_by])
    items     = db.relationship('ReturnItem', backref='return_record',
                                lazy=True, cascade='all, delete-orphan')

    @property
    def total_refund(self):
        return round(sum(i.refund_amount for i in self.items), 2)


class ReturnItem(db.Model):
    __tablename__ = 'return_item'
    id           = db.Column(db.Integer, primary_key=True)
    return_id    = db.Column(db.Integer, db.ForeignKey('return.id'), nullable=False)
    sale_id      = db.Column(db.Integer, db.ForeignKey('sale.id'), nullable=False)
    product_id   = db.Column(db.Integer, db.ForeignKey('product.id'))
    qty_returned = db.Column(db.Integer, nullable=False)
    unit_price   = db.Column(db.Float, nullable=False)
    refund_amount= db.Column(db.Float, nullable=False)
    restock      = db.Column(db.Boolean, default=True)  # add back to stock?

    sale    = db.relationship('Sale',    foreign_keys=[sale_id])
    product = db.relationship('Product', foreign_keys=[product_id])


# ══════════════════════════════════════════════════════════════
#  EOD REPORT  (Tier-2 #6)
# ══════════════════════════════════════════════════════════════
class EODReport(db.Model):
    __tablename__ = 'eod_report'
    id           = db.Column(db.Integer, primary_key=True)
    report_date  = db.Column(db.Date, nullable=False, unique=True)
    generated_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Sales summary
    total_revenue      = db.Column(db.Float, default=0.0)
    total_cash         = db.Column(db.Float, default=0.0)
    total_mpesa        = db.Column(db.Float, default=0.0)
    total_credit_sales = db.Column(db.Float, default=0.0)
    total_discounts    = db.Column(db.Float, default=0.0)
    total_vat          = db.Column(db.Float, default=0.0)
    transaction_count  = db.Column(db.Integer, default=0)
    units_sold         = db.Column(db.Integer, default=0)

    # Expenses
    total_expenses     = db.Column(db.Float, default=0.0)

    # Net
    gross_profit       = db.Column(db.Float, default=0.0)
    net_profit         = db.Column(db.Float, default=0.0)

    generator = db.relationship('User', foreign_keys=[generated_by])


# ══════════════════════════════════════════════════════════════
#  EXPENSE  (unchanged)
# ══════════════════════════════════════════════════════════════
class Expense(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(150), nullable=False)
    amount      = db.Column(db.Float, nullable=False)
    category    = db.Column(db.String(50), nullable=True)
    date        = db.Column(db.Date, default=datetime.utcnow)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Expense {self.description} - {self.amount}>'


# ══════════════════════════════════════════════════════════════
#  CART  (unchanged)
# ══════════════════════════════════════════════════════════════
class CartItem(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    quantity   = db.Column(db.Integer, nullable=False, default=1)
    sale_id    = db.Column(db.Integer, db.ForeignKey('sale.id'))
    product_name = db.Column(db.String(100))
    added_at   = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product')
    user    = db.relationship('User', backref='cart_items')


# ══════════════════════════════════════════════════════════════
#  RECEIPT  (unchanged)
# ══════════════════════════════════════════════════════════════
class Receipt(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    timestamp     = db.Column(db.DateTime, default=datetime.utcnow)
    customer_name = db.Column(db.String(255))
    client_id     = db.Column(db.Integer, db.ForeignKey('client.id'))
    status        = db.Column(db.String(20), default='Open')
    total_amount  = db.Column(db.Float, default=0.0)
    amount_paid   = db.Column(db.Float, default=0.0)
    balance_due   = db.Column(db.Float, default=0.0)
    cash_received = db.Column(db.Float, default=0.0)
    change_given  = db.Column(db.Float, default=0.0)
    discount      = db.Column(db.Float, default=0.0)

    sales    = db.relationship('Sale',    backref='receipt', lazy=True, cascade='all, delete-orphan')
    payments = db.relationship('Payment', backref='receipt', lazy=True, cascade='all, delete-orphan')

    def recompute_balance(self):
        if self.status == 'Cancelled':
            return
        paid = sum(p.amount for p in self.payments
                   if p.status in ('Success', 'ReversedSuccess') and not p.is_reversal)
        reversed_return = sum(p.amount for p in self.payments
                              if p.is_reversal and p.status in ('Success', 'ReversedSuccess'))
        self.amount_paid = round(paid - reversed_return, 2)
        self.balance_due = round((self.total_amount or 0) - self.amount_paid, 2)
        if self.balance_due <= 0:
            self.status = 'Paid'; self.balance_due = 0.0
        elif self.amount_paid > 0:
            self.status = 'Partially Paid'
        else:
            self.status = 'Open'


# ══════════════════════════════════════════════════════════════
#  PAYMENT  (unchanged)
# ══════════════════════════════════════════════════════════════
class Payment(db.Model):
    __tablename__ = 'payment'
    id            = db.Column(db.Integer, primary_key=True)
    receipt_id    = db.Column(db.Integer, db.ForeignKey('receipt.id'), nullable=False)
    client_id     = db.Column(db.Integer, db.ForeignKey('client.id'))
    method        = db.Column(db.String(20), nullable=False)
    amount        = db.Column(db.Float, nullable=False)
    status        = db.Column(db.String(20), default='Pending')
    reference     = db.Column(db.String(120))
    notes         = db.Column(db.String(255))
    is_reversal   = db.Column(db.Boolean, default=False)
    reversal_of_id= db.Column(db.Integer, db.ForeignKey('payment.id'))
    reversed_by   = db.relationship('Payment', remote_side=[id])
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)


# ══════════════════════════════════════════════════════════════
#  MPESA PAYMENT  (unchanged)
# ══════════════════════════════════════════════════════════════
class MpesaPayment(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    phone               = db.Column(db.String(20))
    amount              = db.Column(db.Float, nullable=False)
    status              = db.Column(db.String(20))
    reference           = db.Column(db.String(100))
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)
    checkout_request_id = db.Column(db.String(120), unique=True, nullable=True)
    payment_id          = db.Column(db.Integer, db.ForeignKey('payment.id'))
    payment             = db.relationship('Payment', backref='mpesa_record')


# ══════════════════════════════════════════════════════════════
#  SUPPLIER INVOICE  (updated — backref removed, cascade on Supplier side)
# ══════════════════════════════════════════════════════════════
class SupplierInvoice(db.Model):
    __tablename__ = 'supplier_invoice'
    id             = db.Column(db.Integer, primary_key=True)
    supplier_id    = db.Column(db.Integer, db.ForeignKey('supplier.id'), nullable=False)
    total_amount   = db.Column(db.Float, default=0.0)
    amount_paid    = db.Column(db.Float, default=0.0)
    payment_status = db.Column(db.String(20), default='Unpaid')
    notes          = db.Column(db.String(255))
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship('SupplierInvoiceItem', backref='supplier_invoice',
                            cascade='all, delete-orphan')

    @property
    def balance_due(self):
        return round((self.total_amount or 0) - (self.amount_paid or 0), 2)

    def recompute_status(self):
        if (self.amount_paid or 0) >= (self.total_amount or 0):
            self.payment_status = 'Paid'
        elif (self.amount_paid or 0) > 0:
            self.payment_status = 'Partial'
        else:
            self.payment_status = 'Unpaid'


class SupplierInvoiceItem(db.Model):
    __tablename__ = 'supplier_invoice_item'
    id                  = db.Column(db.Integer, primary_key=True)
    supplier_invoice_id = db.Column(db.Integer, db.ForeignKey('supplier_invoice.id'), nullable=False)
    product_id          = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity            = db.Column(db.Integer, nullable=False)
    unit_cost           = db.Column(db.Float, nullable=False)
    line_total          = db.Column(db.Float, nullable=False)

    product = db.relationship('Product')


