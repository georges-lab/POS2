import os
import sys
import json
import threading
import csv
import io
import time
import requests
from datetime import datetime, timedelta, date
import shutil
import tkinter as tk
from tkinter import messagebox
from datetime import datetime
from flask import Flask
from flask_login import LoginManager


# UI and Desktop Window Management
import webview

# Flask, Security, and Database Extensions
from flask import Flask, render_template, redirect, url_for, request, flash, session, abort, jsonify, make_response, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet
from sqlalchemy import func
from flask_migrate import Migrate

# PDF Generation and Buffer Management
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# ─────────────────────────────────────────
# 2. CORE HELPERS
# ─────────────────────────────────────────




def resource_path(relative_path):
    """
    Get absolute path to bundled resource (templates, static, etc.)

    INNO SETUP + PYINSTALLER NOTES:
    ─────────────────────────────────
    PyInstaller extracts bundled files to sys._MEIPASS (a temp folder).
    Inno Setup installs the .exe to Program Files (or chosen dir).
    This function handles BOTH scenarios so assets are always found.

    ▸ Running as .exe  → sys._MEIPASS  (PyInstaller temp extract dir)
    ▸ Running as .py   → current working directory
    """
    try:
        base_path = sys._MEIPASS          # PyInstaller temp folder
    except AttributeError:
        base_path = os.path.abspath(".")  # Normal dev run
    return os.path.join(base_path, relative_path)


def get_user_data_path():
    if getattr(sys, 'frozen', False):
        # Standard Per-User Install path (AppData\Local\bizTOOL)
        # Bypasses Windows Program Files read-only write blocks entirely without Admin rights
        base = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), "bizTOOL")
    else:
        # Development mode on your Desktop
        base = os.path.join(os.path.abspath(os.path.dirname(__file__)), "instance")

    os.makedirs(base, exist_ok=True)
    return base


def clean_numeric(value):
    """Safely converts currency strings like 'Ksh 1,200' to floats."""
    if not value:
        return 0.0
    try:
        clean_val = (
            str(value)
            .replace('Ksh', '')
            .replace('KES', '')
            .replace(',', '')
            .strip()
        )
        return float(clean_val)
    except (ValueError, TypeError):
        return 0.0


def show_popup(title, message):
    """
    Shows a warning popup then cleanly destroys the tkinter root.
    Uses root.quit() + root.destroy() to properly release the thread
    so pywebview is not blocked after the dialog closes.
    """
    root = tk.Tk()
    root.withdraw()
    messagebox.showwarning(title, message)
    root.quit()
    root.destroy()


# ─────────────────────────────────────────
# DETERMINE BASE DIRECTORY
# ─────────────────────────────────────────
# INNO SETUP NOTE:
# Inno Setup installs your .exe to e.g. C:\Program Files\bizTOOL\
# basedir will correctly point to that install folder.
# We use basedir ONLY to locate the bundled seed database — NOT for
# writing anything (Program Files is read-only without elevation).
#
# ▸ .exe  → folder the .exe was installed to  (Program Files\bizTOOL)
# ▸ dev   → folder containing this .py file
if getattr(sys, 'frozen', False):
    basedir = os.path.dirname(sys.executable)
else:
    basedir = os.path.abspath(os.path.dirname(__file__))


# ─────────────────────────────────────────
# FLASK APP — templates & static from bundle
# ─────────────────────────────────────────
# CRITICAL for PyInstaller + Inno Setup:
# Without explicitly passing template_folder and static_folder,
# Flask looks relative to __file__ which is inside sys._MEIPASS
# and may not resolve correctly after Inno Setup installs the app.
template_dir = resource_path('templates')
static_dir   = resource_path('static')

app = Flask(
    __name__,
    template_folder=template_dir,
    static_folder=static_dir,
)

app.config['SECRET_KEY'] = 'biztool-pro-secure-12345'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


# ─────────────────────────────────────────
# DATABASE PATH  (single source of truth)
# ─────────────────────────────────────────
# INNO SETUP NOTE:
# Do NOT use get_db_path() — it was removed because it pointed to a
# different file (pos.db) next to the .exe in Program Files, which is
# read-only. All writes go through get_user_data_path() → AppData.
#
# When Inno Setup runs an upgrade/reinstall, it replaces the .exe in
# Program Files but leaves AppData\bizTOOL untouched — meaning your
# users' data survives every update automatically.
user_data_path = get_user_data_path()
db_file        = os.path.join(user_data_path, "inventory.db")

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_file


# ─────────────────────────────────────────
# FIRST-RUN: copy seed DB if none exists yet
# ─────────────────────────────────────────
# INNO SETUP NOTE:
# Include your seed inventory.db in the Inno Setup [Files] section:
#
#   [Files]
#   Source: "dist\instance\inventory.db"; DestDir: "{app}\instance"; Flags: onlyifdestfilenotexists
#
# On first install Inno Setup places the seed DB in Program Files\bizTOOL\instance\
# This block then copies it to AppData\bizTOOL\ where the app can write to it.
# The Inno flag "onlyifdestfilenotexists" prevents it from wiping the seed
# on upgrades — and this Python check prevents overwriting user data.
default_db = os.path.join(basedir, "instance", "inventory.db")

if not os.path.exists(db_file) and os.path.exists(default_db):
    shutil.copy(default_db, db_file)


# ─────────────────────────────────────────
# UPLOAD FOLDER
# ─────────────────────────────────────────
# Stored in AppData alongside the DB so Inno Setup upgrades never
# wipe user-uploaded files.
app.config['UPLOAD_FOLDER'] = os.path.join(user_data_path, 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


# ─────────────────────────────────────────
# EXTENSIONS & MODELS
# ─────────────────────────────────────────
from extensions import db, migrate
from models import (
    User, Product, Sale, CartItem, Receipt, MpesaPayment,
    Client, Supplier, Payment, StockMovement,
    SupplierInvoice, SupplierInvoiceItem, Expense
)

db.init_app(app)
migrate.init_app(app, db)


# ─────────────────────────────────────────
# DATABASE INITIALISATION
# ─────────────────────────────────────────
def create_tables_and_users():
    """
    Creates all tables and seeds the default admin user and walk-in client.

    ROLE STORED AS LOWERCASE 'admin':
    The original code stored role='Admin' (capital A).
    Every route guard compares against 'admin' (lowercase) so the
    check always failed inside the .exe, producing "Access Denied".
    Storing it lowercase fixes this permanently.
    """
    with app.app_context():
        db.create_all()

        # ── Admin user ──────────────────────────────────────────────
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                password_hash=generate_password_hash('admin123'),
                role='admin',   # ← lowercase — matches all route guards
            )
            db.session.add(admin)

        # ── Default walk-in client ──────────────────────────────────
        if not Client.query.filter_by(is_walk_in=True).first():
            db.session.add(Client(name="Walk-in Customer", is_walk_in=True))

        db.session.commit()


# ─────────────────────────────────────────
# MIGRATE EXISTING DB: normalise roles
# ─────────────────────────────────────────
def normalise_roles():
    """
    Silently converts any existing 'Admin' (capital A) roles to 'admin'.

    WHY THIS IS NEEDED:
    If users already installed a previous version of the .exe that
    seeded role='Admin', their database in AppData already has the
    wrong casing. Inno Setup upgrades leave AppData untouched so this
    bad role persists across updates.

    This function runs on every startup and fixes it transparently —
    no user action required, no data loss, no manual SQL needed.
    """
    with app.app_context():
        try:
            users   = User.query.all()
            changed = False
            for user in users:
                if user.role and user.role != user.role.lower():
                    user.role = user.role.lower()
                    changed   = True
            if changed:
                db.session.commit()
        except Exception:
            db.session.rollback()


# ─────────────────────────────────────────
# FLASK-LOGIN SETUP
# ─────────────────────────────────────────
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.context_processor
def inject_globals():
    """
    Injects variables available in every template.
    current_user imported inside the function to avoid circular
    import crashes that can occur at PyInstaller bundle time.
    """
    from flask_login import current_user
    return {
        'current_year': datetime.now().year,
        # is_admin available in all templates — use instead of
        # current_user.role == 'admin' in Jinja to avoid case bugs
        'is_admin': (
            current_user.is_authenticated and
            current_user.role.lower() == 'admin'
        ),
    }


# ─────────────────────────────────────────
# STARTUP CALL ORDER
# ─────────────────────────────────────────
# In your main entry point (main.py / app.py) call in this order
# before starting Flask / pywebview:
#
#   create_tables_and_users()   # 1. create schema + seed admin
#   normalise_roles()           # 2. fix any 'Admin' → 'admin' in DB
#
# ─────────────────────────────────────────


# ─────────────────────────────────────────
# ROUTE GUARD PATTERN
# ─────────────────────────────────────────
# Use .lower() on EVERY admin check in your routes.
# Or use the injected is_admin from inject_globals() in templates.
#
#   from flask_login import login_required, current_user
#   from flask import abort
#
#   @app.route('/profit_report')
#   @login_required
#   def profit_report():
#       if current_user.role.lower() != 'admin':   # ← always .lower()
#           abort(403)
#       # ... rest of route
#
# ─────────────────────────────────────────


# ─────────────────────────────────────────
# INNO SETUP — RECOMMENDED .ISS SNIPPETS
# ─────────────────────────────────────────
# Add these to your Inno Setup script (.iss file):
#
# [Setup]
# AppName=bizTOOL POS
# AppVersion=1.0
# DefaultDirName={userpf}\bizTOOL        ; Installs to Local user profile folder instead of Program Files
# DefaultGroupName=bizTOOL POS
# OutputDir=installer_output
# OutputBaseFilename=bizTOOL_Setup
# PrivilegesRequired=lowest              ; No admin rights needed to install or run
#
# [Files]
# ; Bundle the .exe
# Source: "dist\biztool.exe"; DestDir: "{app}"; Flags: ignoreversion
#
# ; Bundle the seed database — only copied on FIRST install, never on upgrade
# Source: "dist\instance\inventory.db";  DestDir: "{app}\instance"; Flags: onlyifdestfilenotexists
#
# ; Bundle templates and static assets (PyInstaller puts these in _MEIPASS
# ; at runtime so they don't need to go to AppData — they stay with the .exe)
# Source: "dist\templates\*"; DestDir: "{app}\templates"; Flags: ignoreversion recursesubdirs
# Source: "dist\static\*";    DestDir: "{app}\static";    Flags: ignoreversion recursesubdirs
#
# [Icons]
# Name: "{group}\bizTOOL POS"; Filename: "{app}\biztool.exe"; WorkingDir: "{app}"
# Name: "{userdesktop}\bizTOOL POS"; Filename: "{app}\biztool.exe"; WorkingDir: "{app}"
#
# [Run]
# Filename: "{app}\biztool.exe"; Description: "Launch bizTOOL POS"; Flags: nowait postinstall skipifsilent


# Routes

@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/license_expired')
def license_expired():
    return render_template('license_expired.html')

# ---- Authentication ----
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            flash('Username already exists.')
            return redirect(url_for('register'))
        hashed_password = generate_password_hash(password)
        new_user = User(username=username, password_hash=hashed_password, role='Employee')
        db.session.add(new_user)
        db.session.commit()
        flash('Registration successful.')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            session['is_admin'] = (user.role.lower() == 'admin')
            return redirect(url_for('dashboard'))
        flash('Invalid credentials.')
    return render_template('login.html', hide_wrapper=True)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))



@app.route('/dashboard')
@login_required
def dashboard():
    today = date.today()
    thirty_days_from_now = today + timedelta(days=30)

    # --- 1. Basic Stats & Expiry Logic ---
    # Fetch all sales records (limited to 50)
    all_sales_records = Sale.query.order_by(Sale.timestamp.desc()).limit(50).all()
    
    total_products = Product.query.count()
    total_clients = Client.query.count()
    
    # Low stock logic
    low_stock_products = Product.query.filter(Product.quantity <= 5).all()
    
    # EXPIRED: Date is strictly less than today
    expired_products = Product.query.filter(Product.expiry_date < today).all()
    
    # EXPIRING SOON: Date is between today and 30 days from now
    expiring_soon = Product.query.filter(
        Product.expiry_date >= today, 
        Product.expiry_date <= thirty_days_from_now
    ).all()

    # --- 2. Financial Totals ---
    total_sales = db.session.query(db.func.sum(Sale.total_amount)).scalar() or 0.0
    total_discount = db.session.query(db.func.sum(Sale.discount)).scalar() or 0.0
    total_expenses = db.session.query(db.func.sum(Expense.amount)).scalar() or 0.0
    
    # Calculate Tax (16% VAT)
    total_tax = total_sales * 0.16 

    # --- 3. Sales Trend by Day ---
    sales_by_day = (
        db.session.query(
            db.func.strftime('%w', Sale.timestamp).label('day_num'),
            db.func.sum(Sale.total_amount)
        )
        .group_by('day_num')
        .all()
    )

    day_map = {'0': 'Sun', '1': 'Mon', '2': 'Tue', '3': 'Wed', '4': 'Thu', '5': 'Fri', '6': 'Sat'}
    chart_labels = []
    chart_values = []

    for d in range(7):
        day_str = str(d)
        chart_labels.append(day_map[day_str])
        val = next((float(x[1]) for x in sales_by_day if x[0] == day_str), 0.0)
        chart_values.append(val)

    # --- 4. Fast Moving Products ---
    fast_moving_products = (
        db.session.query(
            Sale.product_name, 
            db.func.sum(Sale.quantity_sold).label('total_sold')
        )
        .group_by(Sale.product_name)
        .order_by(db.func.sum(Sale.quantity_sold).desc())
        .limit(8)
        .all()
    )

    # --- 5. Render Template ---
    return render_template(
        'dashboard.html',
        # Table data
        sales=all_sales_records,
        fast_moving_products=fast_moving_products,
        low_stock_products=low_stock_products,
        
        # Expiry data for the combined card
        expired_products=expired_products,
        expiring_soon=expiring_soon,
        upcoming_expiries=expired_products + expiring_soon,
        
        # Metric cards
        total_sales=round(total_sales, 2),
        total_products=total_products,
        total_tax=round(total_tax, 2),
        total_discount=round(total_discount, 2),
        total_expenses=round(total_expenses, 2),
        total_clients=total_clients,
        
        # Chart data
        sales_labels=chart_labels,
        sales_data=chart_values,
        sales_trend=chart_values,
        
        # Misc
        today=today, # Needed for template comparisons
        current_date=today,
        current_year=today.year
    )

# ---- Products ----

@app.route('/products')
@login_required
def products():
    # 1. Capture inputs from the URL (Search and Expiry Filters)
    search_query = request.args.get('search', '').strip()
    status_filter = request.args.get('filter', '').strip()
    
    # 2. Start the base query
    query = Product.query
    
    # 3. Date variables for logic and UI
    today = date.today()
    today_plus_30 = today + timedelta(days=30)
    
    # 4. Apply Search Filter (if text is typed in search box)
    if search_query:
        query = query.filter(
            (Product.name.ilike(f'%{search_query}%')) | 
            (Product.category.ilike(f'%{search_query}%'))
        )
    
    # 5. Apply Expiry Filters (if clicked from Dashboard tiles)
    if status_filter == 'expired':
        # Shows products where expiry date is in the past
        query = query.filter(Product.expiry_date < today)
    elif status_filter == 'expiring':
        # Shows products expiring between today and next 30 days
        query = query.filter(Product.expiry_date >= today, 
                             Product.expiry_date <= today_plus_30)
    
    # 6. Get results sorted by most recent
    products = query.order_by(Product.id.desc()).all()
    
    # 7. Role check for UI permissions
    is_admin = current_user.role.lower() == 'admin'
    
    return render_template('products.html', 
                           products=products, 
                           is_admin=is_admin, 
                           search_query=search_query,
                           status_filter=status_filter, # Pass this to keep track of current view
                           today=today,
                           today_plus_30=today_plus_30)



@app.route('/add_product', methods=['GET', 'POST'])
@login_required
def add_product():
    if current_user.role.lower() != 'admin':
        flash("Unauthorized.", "danger")
        return redirect(url_for('products'))
        
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            # Use the new clean_numeric function
            b_price = clean_numeric(request.form.get('buying_price', 0))
            s_price = clean_numeric(request.form.get('selling_price', 0))
            qty = int(request.form.get('quantity', 0))
            
            barcode = request.form.get('barcode', '').strip() or None
            cat = request.form.get('category', 'General')
            
            exp_raw = request.form.get('expiry_date')
            exp_date = datetime.strptime(exp_raw, '%Y-%m-%d').date() if exp_raw else None
            
            new_p = Product(
                name=name,
                buying_price=b_price,
                selling_price=s_price,
                quantity=qty,
                barcode=barcode,
                category=cat,
                expiry_date=exp_date
            )
            
            db.session.add(new_p)
            db.session.commit()
            flash('Product added successfully!', 'success')
            return redirect(url_for('products'))
        except Exception as e:
            db.session.rollback()
            print(f"DEBUG ERROR: {e}") # This will show in your CMD window
            flash(f"Error: {str(e)}", "danger")
            
    return render_template('add_product.html')

@app.route('/edit_product/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_product(id):
    if current_user.role.lower() != 'admin': abort(403)

    product = Product.query.get_or_404(id)

    if request.method == 'POST':
        try:
            product.name = request.form.get('name', product.name).strip()
            product.category = request.form.get('category', product.category)
            product.buying_price = clean_numeric(request.form.get('buying_price', 0))
            product.selling_price = clean_numeric(request.form.get('selling_price', 0))
            product.quantity = int(request.form.get('quantity', 0))
            
            barcode = request.form.get('barcode', '').strip()
            product.barcode = barcode if barcode else None

            exp_raw = request.form.get('expiry_date')
            if exp_raw:
                product.expiry_date = datetime.strptime(exp_raw, '%Y-%m-%d').date()

            db.session.commit()
            flash('Updated successfully!', 'success')
            return redirect(url_for('products'))
        except Exception as e:
            db.session.rollback()
            flash(f"Update failed: {str(e)}", "danger")

    return render_template('edit_product.html', product=product)

@app.route('/delete_product/<int:id>', methods=['POST'])
@login_required
def delete_product(id):
    if current_user.role.lower() != 'admin':
        flash('You do not have permission to delete products.', 'danger')
        return redirect(url_for('dashboard'))

    product = Product.query.get_or_404(id)
    db.session.delete(product)
    db.session.commit()
    flash('Product deleted successfully!')
    return redirect(url_for('products'))




@app.route('/lookup_product', methods=['POST'])
@login_required
def lookup_product():
    barcode = request.form.get('barcode')
    product = Product.query.filter_by(barcode=barcode).first()

    if product:
        flash(f'Product Found: {product.name}, Price: KES {product.price}, Stock: {product.quantity}')
    else:
        flash('No product found with that barcode.')

    return redirect(url_for('products'))

@app.route('/product/waste/<int:id>', methods=['POST'])
@login_required
def mark_product_waste(id):
    # 1. Permission Check
    if current_user.role.lower() != 'admin':
        flash("Unauthorized action.", "danger")
        return redirect(url_for('products'))

    # 2. Fetch Product
    product = Product.query.get_or_404(id)
    old_quantity = product.quantity
    
    # 3. Inventory Check
    if old_quantity <= 0:
        flash(f"{product.name} is already out of stock.", "info")
        return redirect(url_for('products'))

    try:
        # 4. Zero out the stock
        product.quantity = 0
        
        # 5. Record the Loss as an Expense
        # We calculate the total loss based on the buying price
        total_loss = (product.buying_price or 0) * old_quantity
        
        waste_entry = Expense(
            amount=total_loss,
            category="Stock Waste/Expiry",
            description=f"Cleared {old_quantity} units of {product.name} (ID: #{product.id})",
            timestamp=datetime.now()  # Using local time for consistent reporting
        )
        
        db.session.add(waste_entry)
        db.session.commit()
        
        flash(f"Stock for {product.name} cleared. KES {total_loss:,.2f} recorded as business waste.", "warning")
        
    except Exception as e:
        db.session.rollback()
        print(f"Error marking waste: {e}")
        flash("System error recording waste. Please try again.", "danger")

    return redirect(url_for('products'))

@app.route('/sales')
@login_required
def view_sales():  # Renamed to avoid conflict with 'sales' variable in template
    all_sales = Sale.query.order_by(Sale.timestamp.desc()).all()
    print(" Sales fetched from DB:", all_sales)
    return render_template('sales.html', sales=all_sales)



@app.route('/add_sale', methods=['POST'])
@login_required
def add_sale():
    product_id = request.form.get('product_id')
    quantity = int(request.form.get('quantity'))
    discount = float(request.form.get('discount') or 0)
    tax_rate = float(request.form.get('tax_rate') or 0.16)

    product = Product.query.get_or_404(product_id)

    # Validate stock
    if product.quantity < quantity:
        flash("Not enough stock available.", "danger")
        return redirect(url_for('sales'))

    # Create sale record
    sale = Sale(
        product_name=product.name,
        product_id=product.id,
        quantity_sold=quantity,
        unit_price=product.price,
        discount=discount,
        tax_rate=tax_rate
    )

    sale.total_price = sale.calculate_total()

    # Update product stock
    product.quantity -= quantity

    db.session.add(sale)
    db.session.commit()

    flash(f"Sale recorded: {product.name} x{quantity}", "success")
    return redirect(url_for('sales'))

@app.route('/expense_report', methods=['GET', 'POST'])
@login_required
def expense_report():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    query = Expense.query

    if start_date and end_date:
        query = query.filter(Expense.date.between(start_date, end_date))

    expenses = query.order_by(Expense.date.desc()).all()
    total_filtered = db.session.query(func.sum(Expense.amount)).filter(Expense.date.between(start_date, end_date)).scalar() or 0

    return render_template(
        'expense_report.html',
        expenses=expenses,
        total_filtered=total_filtered,
        start_date=start_date,
        end_date=end_date
    )


@app.route('/record_sale', methods=['POST'])
@login_required
def record_sale():
    try:
        product_id = int(request.form['product_id'])
        quantity_sold = int(request.form['quantity_sold'])
    except (KeyError, ValueError):
        flash('Invalid product or quantity.', 'danger')
        return redirect(url_for('products'))

    product = Product.query.get_or_404(product_id)

    if product.quantity < quantity_sold:
        flash('Not enough stock available.', 'danger')
        return redirect(url_for('products'))

    unit_price = product.price
    if unit_price is None:
        flash('Product price is missing.', 'danger')
        return redirect(url_for('products'))

    total_price = quantity_sold * unit_price

    sale = Sale(product_name=product.name, quantity_sold=quantity_sold, unit_price=product.price, total_price=total_price)
    product.quantity -= quantity_sold

    print(">>> Attempting to record sale:")
    print("Product:", product.name)
    print("Quantity sold:", quantity_sold)
    print("Unit price:", unit_price)
    print("Total price:", total_price)
    print("Database URI:", app.config['SQLALCHEMY_DATABASE_URI'])

    try:
        db.session.add(sale)
        db.session.commit()
        print(">>> SALE SAVED with ID:", sale.id)
    except Exception as e:
        db.session.rollback()
        print(">>> ERROR saving sale:", str(e))

    flash('Sale recorded successfully!', 'success')
    return redirect(url_for('sales'))


@app.route('/delete_all_sales', methods=['POST'])
@login_required
def delete_all_sales():
    # .lower() prevents "Admin" vs "admin" bugs
    if current_user.role.lower() != 'admin':
        flash("Unauthorized action.", "danger")
        return redirect(url_for('dashboard'))

    try:
        Sale.query.delete()
        db.session.commit()
        flash("All sales records deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash("Error deleting records. Please try again.", "danger")

    # Redirecting back to profit_analysis keeps the user in the same context
    return redirect(url_for('profit_report'))


@app.route('/receipt/<int:receipt_id>')
@login_required
def receipt(receipt_id):
    receipt_obj = Receipt.query.get_or_404(receipt_id)
    sales = receipt_obj.sales
    subtotal = sum(s.total_price for s in sales)
    total_discounts = sum(s.discount for s in sales if s.discount)
    payment = Payment.query.filter_by(receipt_id=receipt_obj.id).first()

    return render_template('receipt.html', 
                           receipt=receipt_obj, 
                           sales=sales, 
                           payment=payment,
                           subtotal=subtotal,
                           total_discounts=total_discounts,
                           is_pdf=False)

@app.route('/download_invoice/<int:receipt_id>')
@login_required
def download_invoice(receipt_id):  # Changed from download_invoice_file to download_invoice
    receipt_obj = Receipt.query.get_or_404(receipt_id)
    sales = receipt_obj.sales
    subtotal = sum(s.total_price for s in sales)
    total_discounts = sum(s.discount for s in sales if s.discount)
    payment = Payment.query.filter_by(receipt_id=receipt_obj.id).first()

    rendered = render_template('receipt.html', 
                               receipt=receipt_obj, 
                               sales=sales, 
                               payment=payment,
                               subtotal=subtotal,
                               total_discounts=total_discounts,
                               is_pdf=True)
    try:
        import pdfkit
        pdf = pdfkit.from_string(rendered, False)
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename=Invoice_{receipt_id}.pdf'
        return response
    except Exception as e:
        print(f"PDF Error: {e}")
        return rendered

@app.route('/daily_sales')
@login_required
def daily_sales():
    sales_data = (
        db.session.query(
            func.date(Sale.timestamp).label('sale_date'),
            func.sum(Sale.total_price).label('total_sales'),
            func.count(Sale.id).label('transactions'),
        )
        .group_by(func.date(Sale.timestamp))
        .order_by(func.date(Sale.timestamp).desc())
        .all()
    )

    return render_template('daily_sales.html', sales_data=sales_data)


@app.route('/sales/report')
@login_required
def sales_report():
    from sqlalchemy import func

    report_data = (
        db.session.query(Sale.product_name, func.sum(Sale.quantity_sold).label('total_sold'))
        .group_by(Sale.product_name)
        .order_by(func.sum(Sale.quantity_sold).desc())
        .limit(5)
        .all()
    )

    labels = [r.product_name for r in report_data]
    values = [r.total_sold for r in report_data]

    return jsonify({'labels': labels, 'values': values})


@app.route('/retry_payment/<int:sale_id>', methods=['POST'])
@login_required
def retry_payment(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    phone = request.form['phone']

    headers = {
        "Authorization": f"Bearer {INTASEND_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "currency": "KES",
        "amount": sale.total_price,
        "phone_number": phone,
        "narrative": f"Retry for Sale #{sale.id}",
        "api_ref": f"RETRY-{datetime.utcnow().timestamp()}",
        "public_key": INTASEND_PUBLIC_KEY
    }

    try:
        response = requests.post(
            f"{INTASEND_URL}/api/v1/payment/mpesa-stk-push/",
            headers=headers,
            json=data
        )
        result = response.json()
        if response.status_code == 200:
            flash("STK push resent successfully.", "info")
        else:
            flash("Retry failed: " + result.get("message", "Unknown error"), "danger")
    except Exception as e:
        flash("Retry error: " + str(e), "danger")

    return redirect(url_for('receipt', sales_id=sale.id))




@app.route('/add_expense', methods=['GET', 'POST'])
@login_required
def add_expense():
    if request.method == 'POST':
        # 1. Use .get() to avoid KeyError/400 errors
        description = request.form.get('description')
        amount_raw = request.form.get('amount')
        category = request.form.get('category')
        expense_date_str = request.form.get('date')

        # 2. Manual Validation
        if not description or not amount_raw:
            flash("Description and Amount are required!", "danger")
            return redirect(url_for('add_expense'))

        try:
            # 3. Safe Date Conversion
            # If date is empty string from HTML, use today's date
            if expense_date_str:
                final_date = datetime.strptime(expense_date_str, '%Y-%m-%d').date()
            else:
                final_date = date.today()

            # 4. Create and Save
            expense = Expense(
                description=description,
                amount=float(amount_raw),
                category=category,
                date=final_date
            )
            db.session.add(expense)
            db.session.commit()

            flash("Expense added successfully!", "success")
            return redirect(url_for('dashboard')) # Or 'view_expenses'
            
        except Exception as e:
            db.session.rollback()
            print(f"Debug Error: {e}") # This shows up in your terminal
            flash("An error occurred while saving. Please try again.", "danger")
            return redirect(url_for('add_expense'))

    return render_template('add_expense.html')




@app.route('/view_expenses')
@login_required
def view_expenses():
    if current_user.role.lower() != 'admin':
        flash('Access Denied: Admin privileges required.', 'danger')
        return redirect(url_for('dashboard'))

    try:
        # Fetch expenses
        expenses = Expense.query.order_by(Expense.date.desc()).all()
        
        # Safe Calculation: handle None and database connection issues
        total_val = db.session.query(func.sum(Expense.amount)).scalar()
        total_expenses = float(total_val) if total_val is not None else 0.0
        
        return render_template('view_expenses.html', 
                               expenses=expenses, 
                               total_expenses=total_expenses)
    except Exception as e:
        # This will tell you the REAL error in your terminal (e.g., missing column)
        print(f"DATABASE ERROR on view_expenses: {e}")
        flash("Could not load expense data. Check database connection.", "warning")
        return redirect(url_for('dashboard'))

@app.route('/delete_all_expenses', methods=['POST'])
@login_required
def delete_all_expenses():
    # Final security check: Only allow Admin
    if current_user.role.lower() != 'admin':
        flash('Unauthorized: Admin access required.', 'danger')
        return redirect(url_for('view_expenses'))

    try:
        # This deletes every row in your Expense table
        db.session.query(Expense).delete() 
        db.session.commit()
        flash('All expense records have been successfully deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"Delete Error: {e}")
        flash('An error occurred while trying to delete records.', 'danger')

    return redirect(url_for('view_expenses'))        

@app.route('/mpesa_dashboard')
@login_required
def mpesa_dashboard():
    if current_user.role != 'admin':
        flash("Access denied: Admins only.", "danger")
        return render_template('access_denied.html')
    
    payments = MpesaPayment.query.order_by(MpesaPayment.created_at.desc()).all()
    return render_template('mpesa_dashboard.html', payments=payments)






@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_password = request.form['current_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        if not check_password_hash(current_user.password_hash, current_password):
            flash('Current password is incorrect.', 'danger')
            return redirect(url_for('change_password'))

        if new_password != confirm_password:
            flash('New passwords do not match.', 'danger')
            return redirect(url_for('change_password'))

        current_user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        flash('Password changed successfully!', 'success')
        return redirect(url_for('dashboard'))
    

    return render_template('change_password.html')

@app.route('/barcode/<barcode>', methods=['GET'])
@login_required
def get_product_by_barcode(barcode):
    product = Product.query.filter_by(barcode=barcode).first()
    if product:
        return {
            "id": product.id,
            "name": product.name,
            "price": product.price,
            "quantity": product.quantity
        }
    return {"error": "Product not found"}, 404




@app.route('/fast_moving')
@login_required
def fast_moving():
    from sqlalchemy import func

    top_products = db.session.query(
        Sale.product_name,
        func.sum(Sale.quantity_sold).label('total_sold')
    ).group_by(Sale.product_name).order_by(func.sum(Sale.quantity_sold).desc()).limit(7).all()

    return render_template('fast_moving.html', top_products=top_products)


@app.route('/cart', methods=['GET'])
@login_required
def cart():
    # 1. Fetch items and clients
    cart_items = CartItem.query.filter_by(user_id=current_user.id).all()
    clients = Client.query.order_by(Client.name).all()

    # 2. Calculate Grand Total in Python (Safer for heavy carts)
    total = 0
    for item in cart_items:
        # Use .get() or check for None to prevent crashes
        price = item.product.selling_price if item.product.selling_price else 0
        total += item.quantity * price

    # 3. Pass the total to the template
    return render_template(
        'cart.html', 
        cart_items=cart_items, 
        clients=clients, 
        grand_total=total  # Now the HTML can just use {{ grand_total }}
    )


@app.route('/add_to_cart/<int:product_id>', methods=['POST'])
@login_required
def add_to_cart(product_id):
    product = Product.query.get_or_404(product_id)
    quantity = int(request.form.get('quantity', 1))

    existing_item = CartItem.query.filter_by(product_id=product.id, user_id=current_user.id).first()

    if existing_item:
        existing_item.quantity += quantity
    else:
        cart_item = CartItem(product_id=product.id, user_id=current_user.id, quantity=quantity)
        db.session.add(cart_item)

    db.session.commit()
    flash(f"{product.name} added to cart.", "success")
    return redirect(url_for('products'))

@app.route('/upload_products', methods=['GET', 'POST'])
@login_required
def upload_products():
    if request.method == 'POST':
        file = request.files.get('file')
        
        if not file or file.filename == '':
            flash('No file selected', 'danger')
            return redirect(request.url)

        products_added = 0
        products_skipped = 0
        filename = secure_filename(file.filename).lower()

        try:
            # --- 1. CSV HANDLER (Stress-Proofed) ---
            if filename.endswith('.csv'):
                # Handle potential encoding issues (UTF-8-SIG handles Excel BOM)
                raw_data = file.stream.read().decode("utf-8-sig", errors="ignore")
                stream = io.StringIO(raw_data)
                reader = csv.DictReader(stream)
                
                # If DictReader fails to find headers, skip
                if not reader.fieldnames:
                    flash("CSV has no valid headers.", "danger")
                    return redirect(request.url)

                for row_idx, row in enumerate(reader, start=1):
                    try:
                        # Normalize keys: lowercase, strip, and remove None keys
                        data = {str(k).lower().strip(): v for k, v in row.items() if k is not None}
                        
                        # Name is mandatory
                        name = data.get('name') or data.get('product') or data.get('item')
                        if not name or not str(name).strip():
                            products_skipped += 1
                            continue

                        # Robust Numeric Parsing
                        b_price = clean_numeric(data.get('buying_price') or data.get('price'))
                        s_price = clean_numeric(data.get('selling_price')) or (b_price * 1.25)
                        qty = int(float(data.get('quantity') or 0)) # Handle "10.0" as int

                        # Expiry Date multi-format logic
                        expiry_date = None
                        expiry_raw = data.get('expiry_date') or data.get('expiry')
                        if expiry_raw and str(expiry_raw).strip():
                            for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%Y/%m/%d'):
                                try:
                                    expiry_date = datetime.strptime(expiry_raw.strip(), fmt).date()
                                    break
                                except: continue

                        new_product = Product(
                            name=name.strip(),
                            category=data.get('category', 'General').strip() or 'General',
                            quantity=qty,
                            barcode=data.get('barcode', '').strip() or None,
                            buying_price=round(b_price, 2),
                            selling_price=round(s_price, 2),
                            expiry_date=expiry_date
                        )
                        db.session.add(new_product)
                        products_added += 1

                    except Exception as row_err:
                        print(f"Skipping Row {row_idx} due to error: {row_err}")
                        products_skipped += 1
                        continue # Keep going even if one row is broken

            elif filename.endswith('.pdf'):
                import pdfplumber
                file.stream.seek(0)
                with pdfplumber.open(file.stream) as pdf:
                    for page in pdf.pages:
                        # intersection_x_tolerance is more compatible with older versions
                        table = page.extract_table(table_settings={
                            "vertical_strategy": "text",
                            "horizontal_strategy": "text",
                            "intersection_x_tolerance": 15
                        })
                        
                        if not table or len(table) < 2:
                            continue
                        
                        # Clean headers
                        headers = [str(h).lower().strip() if h else '' for h in table[0]]
                        print(f"DEBUG: Headers found -> {headers}")

                        for row_data in table[1:]:
                            try:
                                # Create a dictionary from headers
                                row = dict(zip(headers, row_data))
                                
                                # --- FALLBACK LOGIC ---
                                # If headers were empty quotes (''), we use index positions instead
                                name = row.get('name') or row.get('product') or row_data[0]
                                if not name or str(name).lower() in ['name', 'none', '']:
                                    continue

                                # Get prices using the cleaner helper
                                b_raw = row.get('buying_price') or row.get('price') or row_data[1]
                                s_raw = row.get('selling_price') or row_data[2]
                                
                                b_price = clean_numeric(b_raw)
                                s_price = clean_numeric(s_raw)
                                
                                # Professional Markup if selling price is missing
                                if s_price <= 0:
                                    s_price = b_price * 1.25
                                
                                new_product = Product(
                                    name=str(name).strip(),
                                    category=str(row.get('category') or row_data[4] or 'General').strip(),
                                    quantity=int(float(clean_numeric(row.get('quantity') or row_data[3]))),
                                    buying_price=round(b_price, 2),
                                    selling_price=round(s_price, 2)
                                )
                                db.session.add(new_product)
                                products_added += 1
                            except Exception as e:
                                print(f"Row Error: {e}")
                                products_skipped += 1
                db.session.commit()
            
            msg = f"Import complete! Added: {products_added}"
            if products_skipped > 0:
                msg += f" | Skipped: {products_skipped} (Invalid rows)"
            flash(msg, 'success' if products_added > 0 else 'warning')

        except Exception as e:
            db.session.rollback()
            print(f"CRITICAL IMPORT ERROR: {str(e)}")
            flash(f"Major Error: {str(e)}", "danger")

        return redirect(url_for('products'))

    return render_template('upload_products.html')

@app.route('/remove_from_cart/<int:item_id>', methods=['GET', 'POST'])
@login_required
def remove_from_cart(item_id):
    # This debug line will show up in your VS Code terminal
    from flask import request
    print(f"DEBUG: Request Method is {request.method}")

    item = CartItem.query.get_or_404(item_id)
    
    if item.user_id != current_user.id:
        flash("Unauthorized access.", "danger")
        return redirect(url_for('cart'))

    db.session.delete(item)
    db.session.commit()
    flash(f"Item removed.", "info")
    return redirect(url_for('cart'))

from datetime import datetime
from flask import render_template, redirect, url_for
from flask_login import login_required, current_user
from sqlalchemy import func

@app.route('/admin_dashboard')
@login_required
def admin_dashboard():
    if current_user.role != 'admin':
        return redirect(url_for('index'))

    # Fetch sales & products
    sales = Sale.query.order_by(Sale.timestamp.desc()).all()
    products = Product.query.all()

    # --- Dashboard metrics ---
    # 1. Total sales (assuming Sale has 'amount' field)
    total_sales = sum(s.amount for s in sales) if sales else 0

    # 2. Total products
    total_products = len(products)

    # 3. Low stock products (adjust threshold as needed)
    low_stock_products = [p for p in products if getattr(p, "quantity", 0) < 5]

    # 4. Expired products (assuming Product has 'expiry_date')
    expired_products = [
        p for p in products 
        if getattr(p, "expiry_date", None) and p.expiry_date < datetime.now()
    ]

    # 5. Fast moving products (Top 5 by quantity sold)
    fast_moving_products = (
        db.session.query(
            Product.name.label("product_name"),
            func.sum(Sale.quantity).label("total_sold")
        )
        .join(Sale, Sale.product_id == Product.id)
        .group_by(Product.id)
        .order_by(func.sum(Sale.quantity).desc())
        .limit(5)
        .all()
    )

    # Convert query result to list of dicts for JSON in template
    fast_moving_products_dict = [
        {"product_name": row.product_name, "total_sold": row.total_sold}
        for row in fast_moving_products
    ]

    return render_template(
        "admin_dashboard.html",
        sales=sales,
        products=products,
        total_sales=total_sales,
        total_products=total_products,
        low_stock_products=low_stock_products,
        expired_products=expired_products,
        fast_moving_products=fast_moving_products_dict,
        current_year=datetime.now().year
    )


           
@app.route('/update_cart_quantity/<int:item_id>', methods=['POST'])
@login_required
def update_cart_quantity(item_id):
    # 1. Access Data from Form (Fixes the 415 error)
    new_quantity = request.form.get('quantity')

    # 2. Get the Cart Item (The specific row in the cart)
    # Ensure your model name is 'CartItem' or whatever you named it
    cart_item = CartItem.query.get(item_id)
    
    if cart_item:
        try:
            # 3. Update the cart quantity, not the master product stock
            cart_item.quantity = int(new_quantity)
            db.session.commit()
            flash("Quantity updated successfully!", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating quantity: {str(e)}", "danger")
    else:
        flash("Item not found in cart.", "warning")

    # 4. Redirect back to the cart page (Instead of returning JSON)
    return redirect(url_for('view_cart'))


@app.route('/payments/cash', methods=['POST'])
@login_required
def cash_payment():
    receipt_id = request.form.get('receipt_id', type=int)
    amount = request.form.get('amount', type=float)
    
    # --- NEW: Capture the actual cash the customer handed over ---
    cash_received = request.form.get('cash_received', default=0, type=float)

    if not receipt_id or amount is None or amount <= 0:
        flash("Invalid payment amount.", "danger")
        return redirect(url_for('dashboard'))

    receipt = Receipt.query.get_or_404(receipt_id)

    # Make sure you never pay more than balance due
    balance_due = receipt.total_amount - (receipt.amount_paid or 0)
    if amount > balance_due:
        amount = balance_due  # automatically adjust
        flash(f"Payment reduced to remaining balance: {amount:.2f}", "warning")

    # Create Payment record
    payment = Payment(
        receipt_id=receipt.id,
        method="Cash",
        amount=amount,
        status="Success",
        reference=f"CASH-{receipt.id}-{int(datetime.now().timestamp())}"
    )
    db.session.add(payment)

    # Update the Receipt totals
    receipt.amount_paid = (receipt.amount_paid or 0) + amount
    receipt.balance_due = max(receipt.total_amount - receipt.amount_paid, 0)

    # Optional: recompute balance if you have a method
    if hasattr(receipt, 'recompute_balance'):
        receipt.recompute_balance()

    try:
        db.session.commit()
        flash(f"Payment of {amount:.2f} recorded successfully.", "success")
        
        # --- NEW: We pass 'cash_received' in the URL so the template can read it ---
        return redirect(url_for('invoice_view', 
                                receipt_id=receipt.id, 
                                print='true', 
                                cash_received=cash_received))
    except Exception as e:
        db.session.rollback()
        flash(f"Error recording payment: {str(e)}", "danger")
        return redirect(url_for('invoice_view', receipt_id=receipt.id))


@app.route('/mpesa_callback', methods=['POST'])
def mpesa_callback():
    """Handle M-Pesa payment confirmation from IntaSend (success/failed/reversed)."""
    try:
        payload = request.get_json(force=True)
        invoice_id = payload.get("invoice_id") or payload.get("reference")
        status = (payload.get("status") or "").capitalize()  # 'success', 'failed', 'reversed' etc.

        mpesa = MpesaPayment.query.filter_by(reference=invoice_id).first()
        payment = Payment.query.filter_by(reference=invoice_id, method="Mpesa").first()

        if not payment:
            return jsonify({"error": "Payment not found"}), 404

        # Update raw record
        if mpesa:
            mpesa.status = status if status else mpesa.status

        # Normalize statuses
        if status.lower() == 'success':
            payment.status = "Success"
        elif status.lower() in ('reversed', 'reverse', 'reversal'):
            payment.status = "ReversedSuccess"
        elif status.lower() == 'failed':
            payment.status = "Failed"
        else:
            payment.status = status or payment.status

        db.session.commit()

        # Recompute invoice balance
        receipt = payment.receipt
        receipt.recompute_balance()
        db.session.commit()

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/payments/<int:payment_id>/reverse', methods=['POST'])
@login_required
def reverse_payment(payment_id):
    original = Payment.query.get_or_404(payment_id)
    receipt = original.receipt

    if original.status not in ('Success', 'ReversedSuccess'):
        flash("Only successful payments can be reversed.", "danger")
        return redirect(url_for('receipt', receipt_id=receipt.id))

    # Create a reversal payment (negative effect through is_reversal flag)
    reversal = Payment(
        receipt_id=receipt.id,
        client_id=receipt.client_id,
        method=original.method,
        amount=original.amount,
        status="ReversedSuccess",
        is_reversal=True,
        reversal_of_id=original.id,
        reference=f"REV-{original.reference}",
        notes="Manual reversal"
    )
    db.session.add(reversal)
    db.session.commit()

    # Recompute invoice balance
    receipt.recompute_balance()
    db.session.commit()

    flash("Payment reversed.", "info")
    return redirect(url_for('receipt', receipt_id=receipt.id))

@app.route('/clients', methods=['GET', 'POST'])
@login_required
def clients():
    if request.method == 'POST':
        name = request.form['name'].strip()
        phone = request.form.get('phone', '').strip()
        email = request.form.get('email', '').strip()
        db.session.add(Client(name=name, phone=phone, email=email, is_walk_in=False))
        db.session.commit()
        flash('Client saved.', 'success')
        return redirect(url_for('clients'))
    return render_template('clients.html', clients=Client.query.order_by(Client.created_at.desc()).all())

@app.route('/suppliers', methods=['GET', 'POST'])
@login_required
def suppliers():
    if request.method == 'POST':
        # 1. READ THE FORM DATA
        name = request.form.get('name')
        phone = request.form.get('phone', '')
        email = request.form.get('email', '')
        address = request.form.get('address', '')
        items_supplied = request.form.get('items_supplied', '')
        
        # Safe conversion for the amount
        try:
            initial_amount = float(request.form.get('initial_amount', 0) or 0)
        except (ValueError, TypeError):
            initial_amount = 0.0

        # 2. VALIDATE AND SAVE
        if not name:
            flash('Supplier name is required!', 'danger')
            return redirect(url_for('suppliers'))

        # Create the supplier record
        new_supplier = Supplier(
            name=name,
            phone=phone,
            email=email,
            address=address,
            items_supplied=items_supplied
        )
        db.session.add(new_supplier)
        
        # We flush so the database gives us a supplier ID for the invoice
        db.session.flush() 

        # 3. LOG THE INITIAL AMOUNT (If provided)
        if initial_amount > 0:
            first_invoice = SupplierInvoice(
                supplier_id=new_supplier.id,
                total_amount=initial_amount
            )
            db.session.add(first_invoice)

        # 4. FINAL SAVE
        db.session.commit()
        flash(f'Supplier {name} added successfully!', 'success')
        return redirect(url_for('suppliers'))

    # --- GET REQUEST LOGIC (Viewing the list) ---
    all_suppliers = Supplier.query.order_by(Supplier.created_at.desc()).all()
    
    for s in all_suppliers:
        # This calculates the total for the column we added earlier
        total = db.session.query(func.sum(SupplierInvoice.total_amount))\
                  .filter(SupplierInvoice.supplier_id == s.id).scalar()
        s.total_value = total or 0.0

    return render_template('suppliers.html', suppliers=all_suppliers)


@app.route('/supplier_purchase', methods=['POST'])
@login_required
def supplier_purchase():
    supplier_id = request.form.get('supplier_id', type=int)
    items_json = request.form.get('items_json')
    
    try:
        items = json.loads(items_json or "[]")
    except Exception:
        flash("Invalid items format.", "danger")
        return redirect(url_for('suppliers'))

    if not supplier_id or not items:
        flash("Missing supplier or items.", "danger")
        return redirect(url_for('suppliers'))

    # Initialize invoice with 0, we will update after the loop
    inv = SupplierInvoice(supplier_id=supplier_id, total_amount=0.0)
    db.session.add(inv)
    db.session.flush() # This generates the inv.id before the final commit
    
    total = 0.0

    for it in items:
        product = Product.query.get(int(it['product_id']))
        if not product: continue
        
        qty = int(it['quantity'])
        unit_cost = float(it['unit_cost'])
        line_total = qty * unit_cost

        sii = SupplierInvoiceItem(
            supplier_invoice=inv,
            product_id=product.id,
            quantity=qty,
            unit_cost=unit_cost,
            line_total=line_total
        )
        db.session.add(sii)

        # Update stock
        product.quantity += qty
        
        # Record movement with the invoice ID immediately
        record_stock_movement(product.id, qty, "purchase", "supplier_invoice", inv.id)
        
        total += line_total

    # Update the final total on the invoice
    inv.total_amount = round(total, 2)
    db.session.commit()

    flash(f"Purchase of KES {inv.total_amount} recorded and stock updated.", "success")
    return redirect(url_for('suppliers'))

@app.route('/edit_supplier/<int:id>', methods=['POST'])
@login_required
def edit_supplier(id):
    supplier = Supplier.query.get_or_404(id)
    
    # Use .get() with a default empty string '' to prevent crashes
    supplier.name = request.form.get('name')
    supplier.phone = request.form.get('phone', '')
    supplier.email = request.form.get('email', '')
    supplier.address = request.form.get('address', '')
    supplier.items_supplied = request.form.get('items_supplied', '')
    
    # Optional: Basic validation to ensure name isn't wiped out
    if not supplier.name:
        flash('Supplier name cannot be empty!', 'danger')
        return redirect(url_for('suppliers'))

    db.session.commit()
    flash(f'Supplier {supplier.name} updated successfully!', 'success')
    return redirect(url_for('suppliers'))


@app.route('/profit')
@login_required
def profit_report():
    # FIX: Use .lower() to handle 'Admin' vs 'admin'
    if current_user.role.lower() != 'admin':
        flash('Access Denied.', 'danger')
        return redirect(url_for('dashboard'))

    try:
        # Join Sale and Product to get cost and revenue
        # FIX: Ensure we use Sale.quantity_sold
        stats = db.session.query(
            func.sum(Sale.total_price).label('revenue'),
            func.sum(Sale.quantity_sold * Product.buying_price).label('total_cost')
        ).join(Product, Sale.product_id == Product.id).first()

        revenue = float(stats.revenue or 0.0)
        cost = float(stats.total_cost or 0.0)
        profit = revenue - cost

        # Calculate percentages safely for the progress bars
        # This prevents "Division by Zero" errors and stops VSCode errors
        cost_pc = (cost / revenue * 100) if revenue > 0 else 0
        profit_pc = (profit / revenue * 100) if revenue > 0 else 0
        
    except Exception as e:
        print(f"Error: {e}")
        revenue, cost, profit, cost_pc, profit_pc = 0.0, 0.0, 0.0, 0, 0

    recent_sales = Sale.query.order_by(Sale.created_at.desc()).limit(10).all()

    return render_template(
        'profit.html', 
        revenue=revenue, 
        cost=cost, 
        profit=profit, 
        cost_pc=cost_pc, 
        profit_pc=profit_pc,
        recent_sales=recent_sales
    )

@app.route('/delete_supplier/<int:id>')
@login_required
def delete_supplier(id):
    supplier = Supplier.query.get_or_404(id)
    # Note: If your DB has foreign key constraints, 
    # you might need to handle associated invoices first.
    db.session.delete(supplier)
    db.session.commit()
    flash('Supplier deleted successfully!', 'info')
    return redirect(url_for('suppliers'))


@app.route('/stock/adjust', methods=['POST'])
@login_required
def stock_adjust():
    product_id = request.form.get('product_id', type=int)
    qty_change = request.form.get('qty_change', type=int)  # + or -
    note = request.form.get('note', 'manual')

    product = Product.query.get_or_404(product_id)
    new_qty = product.quantity + qty_change
    if new_qty < 0:
        flash("Cannot reduce below zero.", "danger")
        return redirect(url_for('products'))

    product.quantity = new_qty
    record_stock_movement(product.id, qty_change, "adjustment", "manual", 0)
    db.session.commit()

    # set ref id after commit
    last = StockMovement.query.order_by(StockMovement.id.desc()).first()
    if last and last.reference_type == "manual" and last.reference_id == 0:
        last.reference_id = last.id
        db.session.commit()

    flash("Stock adjusted.", "success")
    return redirect(url_for('products'))


@app.route('/invoices')
@login_required
def invoices():
    rows = Receipt.query.order_by(Receipt.timestamp.desc()).all()
    return render_template('invoices.html', invoices=rows)



@app.route('/pay', methods=['POST'])
@login_required
def pay():
    try:
        phone = request.form['phone']
        amount = float(request.form['amount'])
        receipt_id = request.form.get('receipt_id', type=int)

        if not receipt_id:
            flash("No invoice selected for payment.", "danger")
            return redirect(url_for('cart'))

        # --- Get receipt and linked client ---
        receipt = Receipt.query.get_or_404(receipt_id)
        client_id = receipt.client_id

        # --- Prepare IntaSend STK push ---
        headers = {
            "Authorization": f"Bearer {INTASEND_API_KEY}",
            "Content-Type": "application/json"
        }
        api_ref = f"TX-{datetime.utcnow().timestamp()}"
        data = {
            "currency": "KES",
            "amount": amount,
            "phone_number": phone,
            "narrative": f"M-Pesa Payment for Invoice #{receipt.id}",
            "api_ref": api_ref,
            "public_key": INTASEND_PUBLIC_KEY
        }

        response = requests.post(
            f"{INTASEND_URL}/api/v1/payment/mpesa-stk-push/",
            headers=headers,
            json=data
        )
        result = response.json()

        # --- Create unified Payment row ---
        pay_row = Payment(
            receipt_id=receipt.id,
            client_id=client_id,
            method="Mpesa",
            amount=round(amount, 2),
            status="Pending",
            reference=result.get("invoice_id") or api_ref,
            notes="STK push initiated"
        )
        db.session.add(pay_row)
        db.session.commit()  # flush to get pay_row.id

        # --- Create raw MpesaPayment row linked to Payment ---
        mp = MpesaPayment(
            phone=phone,
            amount=round(amount, 2),
            status="Pending",
            reference=result.get("invoice_id") or api_ref,
            payment_id=pay_row.id
        )
        db.session.add(mp)
        db.session.commit()

        # --- Feedback to user ---
        if response.status_code == 200 and "invoice_id" in result:
            flash("STK push sent. Confirm payment on your phone.", "success")
        else:
            pay_row.status = "Failed"
            db.session.commit()
            flash(f"Payment initiation failed: {result.get('message', 'Unknown error')}", "danger")

        # --- Optional: recompute receipt balance if needed ---
        # This should be triggered when payment is confirmed via webhook
        # receipt.recompute_balance()
        # db.session.commit()

    except Exception as e:
        db.session.rollback()
        flash(f"Payment error: {str(e)}", "danger")

    return redirect(url_for('invoice_view', receipt_id=receipt.id))




# ================================
# M-PESA TILL NUMBER PAYMENT
# ================================
@app.route('/pay_till', methods=['POST'])
@login_required
def pay_till():
    """Initiate M-Pesa payment via Till Number using IntaSend."""
    amount = float(request.form['amount'])
    till_number = os.getenv("MPESA_TILL_NUMBER", "YOUR_TILL_NUMBER_HERE")

    headers = {
        "Authorization": f"Bearer {INTASEND_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "currency": "KES",
        "amount": amount,
        "till_number": till_number,
        "narrative": f"M-Pesa Till Payment for {current_user.username}",
        "api_ref": f"TILL-{datetime.utcnow().timestamp()}",
        "public_key": INTASEND_PUBLIC_KEY
    }

    try:
        response = requests.post(
            f"{INTASEND_URL}/api/v1/payment/mpesa-c2b/",
            headers=headers,
            json=data
        )
        result = response.json()

        if response.status_code == 200 and "invoice_id" in result:
            payment = MpesaPayment(
                phone=None,  # Till payment doesn't need phone
                amount=amount,
                status="Pending",
                reference=result.get("invoice_id")
            )
            db.session.add(payment)
            db.session.commit()
            flash("Till payment initiated. Awaiting confirmation.", "success")
        else:
            flash(f"Till payment failed: {result.get('message', 'Unknown error')}", "danger")

    except Exception as e:
        flash(f"Till payment error: {str(e)}", "danger")

    return redirect(url_for('cart'))

from decimal import Decimal

@app.route('/checkout', methods=['POST'])
@login_required
def checkout():
    try:
        # --- 1. Form Data Extraction ---
        client_id = request.form.get('client_id')
        payment_method = request.form.get('payment_method', 'Cash')
        
        # SAFETY NET: Handle empty strings for cash fields
        raw_amt = request.form.get('payment_amount', '')
        raw_cash = request.form.get('cash_received', '')
        raw_change = request.form.get('change_given', '')
        
        # Convert with safety checks
        cash_received = float(raw_cash) if (raw_cash and raw_cash.strip()) else 0.0
        change_given = float(raw_change) if (raw_change and raw_change.strip()) else 0.0
        
        # Get the arrays sent from the updated cart.html
        item_ids = request.form.getlist('item_ids[]')
        discounts = request.form.getlist('discounts[]')

        # --- 2. Client Handling ---
        if client_id and client_id != "walkin":
            client = Client.query.get(int(client_id))
        else:
            client = Client.query.filter_by(is_walk_in=True).first()
            if not client:
                client = Client(name="Walk-in Customer", is_walk_in=True)
                db.session.add(client)
                db.session.flush() 

        # --- 3. Cart Validation ---
        cart_items = CartItem.query.filter_by(user_id=current_user.id).all()
        if not cart_items:
            flash("Your cart is empty.", "warning")
            return redirect(url_for('cart'))

        # --- 4. Initialize Receipt ---
        receipt = Receipt(
            client_id=client.id,
            customer_name=client.name,
            total_amount=0.0,
            status="Paid" if payment_method == "Cash" else "Pending"
        )
        
        # Save to DB only if columns exist
        if hasattr(receipt, 'cash_received'):
            receipt.cash_received = cash_received
        if hasattr(receipt, 'change_given'):
            receipt.change_given = change_given

        db.session.add(receipt)
        db.session.flush() 

        total_checkout_amount = 0.0

        # --- 5. Process Items ---
        for i, item in enumerate(cart_items):
            product = Product.query.get(item.product_id)
            
            if not product or product.quantity < item.quantity:
                db.session.rollback() 
                flash(f"Insufficient stock for {product.name if product else 'Unknown Item'}", "danger")
                return redirect(url_for('cart'))

            unit_price = float(product.selling_price or 0)
            base_line_total = round(item.quantity * unit_price, 2)
            
            # Safety net for discounts array
            current_discount_val = discounts[i] if (discounts and i < len(discounts)) else '0'
            item_discount = float(current_discount_val) if (current_discount_val and current_discount_val.strip()) else 0.0
            
            final_line_total = round(base_line_total - item_discount, 2)

            sale = Sale(
                product_name=product.name,
                product_id=product.id,
                quantity_sold=item.quantity,
                unit_price=unit_price, 
                total_price=base_line_total, 
                discount=item_discount,      
                total_amount=final_line_total, 
                receipt_id=receipt.id,
                client_id=client.id,
                tax_rate=0.16
            )
            db.session.add(sale)

            # Record Stock Movement
            movement = StockMovement(
                product_id=product.id,
                quantity_change=-item.quantity,
                reason="sale",
                reference_type="receipt",
                reference_id=receipt.id
            )
            db.session.add(movement)

            # Update Physical Stock
            product.quantity -= item.quantity
            total_checkout_amount += final_line_total

        # --- 6. Finalize Totals & Payments ---
        receipt.total_amount = round(total_checkout_amount, 2)
        
        # Final safety net for paid_amount
        paid_amount = float(raw_amt) if (raw_amt and raw_amt.strip()) else receipt.total_amount

        payment = Payment(
            receipt_id=receipt.id,
            client_id=client.id,
            method=payment_method,
            amount=paid_amount,
            status="Success" if payment_method == "Cash" else "Pending"
        )
        db.session.add(payment)

        db.session.flush() 
        if hasattr(receipt, 'recompute_balance'):
            receipt.recompute_balance()

        # --- 7. Cleanup & Commit ---
        CartItem.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()

        flash(f"Sale successful! Receipt #{receipt.id} generated.", "success")
        
        # --- 8. THE REDIRECT FIX ---
        return redirect(url_for('invoice_view', 
                                receipt_id=receipt.id, 
                                cash_received=cash_received, 
                                change_given=change_given))

    except Exception as e:
        db.session.rollback()
        import traceback
        print(f"CRITICAL CHECKOUT ERROR: {str(e)}")
        print(traceback.format_exc())
        flash(f"Checkout Error: {str(e)}", "danger")
        return redirect(url_for('cart'))

@app.route('/invoice/<int:receipt_id>')
@login_required
def invoice_view(receipt_id):
    receipt = Receipt.query.get_or_404(receipt_id)
    updated = False

    # FIX: If total_amount is None, calculate it from the sales lines immediately
    if receipt.total_amount is None:
        sales_items = Sale.query.filter_by(receipt_id=receipt.id).all()
        receipt.total_amount = sum(s.total_price for s in sales_items)
        updated = True

    # Recompute balance safely
    if hasattr(receipt, 'recompute_balance'):
        old_balance = receipt.balance_due
        receipt.recompute_balance()
        if receipt.balance_due != old_balance:
            updated = True
    else:
        # Use Decimal for precision to avoid floating point errors
        total = Decimal(str(receipt.total_amount or 0))
        paid = Decimal(str(receipt.amount_paid or 0))
        new_balance = float(total - paid)

        if receipt.balance_due != new_balance:
            receipt.balance_due = new_balance
            updated = True

    if updated:
        db.session.commit()

    # ... rest of your route (fetching payments, etc.)
    sales = Sale.query.filter_by(receipt_id=receipt.id).all()
    payments = Payment.query.filter_by(receipt_id=receipt.id).order_by(Payment.id.desc()).all()
    
    return render_template('receipt.html', receipt=receipt, sales=sales, payments=payments)

@app.route('/api/fast-moving')
def fast_moving_api():
    rows = (
        db.session.query(
            Sale.product_name,
            func.sum(Sale.quantity_sold).label('total_sold')
        )
        .group_by(Sale.product_name)
        .order_by(func.sum(Sale.quantity_sold).desc())
        .limit(7)
        .all()
    )
    data = [{'product_name': name, 'total_sold': total} for name, total in rows]
    return jsonify(data)

@app.route('/api/product', methods=['POST'])
def product_api():
    payload = request.get_json() or {}
    code = payload.get('barcode', '')
    product = Product.query.filter_by(barcode=code).first()
    if not product:
        return jsonify({'error': 'Product not found'}), 404
    return jsonify({
        'name': product.name,
        'quantity': product.quantity,
        'expiry_date': product.expiry_date.strftime('%Y-%m-%d')
    })



# existing app definition and routes remain above this...

def show_native_error(title, message):
    """Shows an error using Windows system calls, bypassing Tkinter entirely."""
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)

if __name__ == '__main__':
    try:
        # 1. DATABASE & AUDIT LOGIC
        with app.app_context():
            print("--- Initializing Database...")
            from models import Product, Receipt, User
            db.create_all()
            create_tables_and_users()
            
            # Price Audit
            print("--- Running Price Audit...")
            try:
                products_to_fix = Product.query.filter(
                    (Product.selling_price == None) | (Product.selling_price == 0)
                ).all()
                if products_to_fix:
                    for p in products_to_fix:
                        b_price = float(p.buying_price) if p.buying_price else 0.0
                        p.selling_price = round(b_price * 1.25, 2) if b_price > 0 else 10.0
                    db.session.commit()
            except Exception as e:
                print(f"Audit skipped: {e}")

        # 2. START FLASK
        def start_flask():
            app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)

        flask_thread = threading.Thread(target=start_flask, daemon=True)
        flask_thread.start()

        # 3. GUI LAUNCH
        print("\n" + "="*50)
        print("LAUNCHING BIZTOOL POS")
        print("="*50 + "\n")
        
        # Give Flask 2 seconds to bind to the port
        time.sleep(2)

        # Create window with a delay-safe initialization
        window = webview.create_window(
            'BizTool POS', 
            'http://127.0.0.1:5000',
            width=1280, 
            height=800,
            min_size=(1024, 768),
            confirm_close=True
        )

        # Force 'edgechromium'
        webview.start(gui='edgechromium')

    except Exception as e:
        error_msg = f"Startup Failed: {str(e)}"
        print(error_msg)
        show_native_error("System Error", error_msg)
        input("Press Enter to exit...")