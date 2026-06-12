import os
import sys
import json
import threading
import csv
import io
import time
import shutil
import logging
import base64
import tkinter as tk
from tkinter import messagebox
from datetime import datetime, timedelta, date

# ── Timezone support (Kenya = EAT = UTC+3) ────────────────────────────────
# zoneinfo ships with Python 3.9+. For earlier Python/PyInstaller builds,
# we fall back to a simple fixed UTC+3 offset so no external package is needed.
try:
    from zoneinfo import ZoneInfo
    EAT = ZoneInfo("Africa/Nairobi")
    def now_eat():
        """Return current datetime in East Africa Time (UTC+3)."""
        return datetime.now(EAT).replace(tzinfo=None)  # naive EAT datetime
except ImportError:
    # Fallback: manual UTC+3 offset (works even without tzdata on Windows)
    _EAT_OFFSET = timedelta(hours=3)
    def now_eat():
        """Return current datetime in East Africa Time (UTC+3) via fixed offset."""
        return datetime.utcnow() + _EAT_OFFSET

# UI and Desktop Window Management
import webview

# Flask and extensions
import requests
from requests.auth import HTTPBasicAuth
from flask import Flask, render_template, redirect, url_for, request, flash, session, abort, jsonify, make_response, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from flask_cors import CORS
from flask_migrate import Migrate
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet
from sqlalchemy import func

# PDF Generation
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


# ─────────────────────────────────────────
# LOGGING — replaces all print() debug calls
# ─────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# 2. CORE HELPERS
# ─────────────────────────────────────────

def load_config():
    """
    Load secrets from %LOCALAPPDATA%\\bizTOOL\\config.json.
    Falls back to empty dict so the app still starts if the file is missing —
    M-Pesa routes will fail gracefully rather than crashing on import.

    config.json format:
    {
        "CONSUMER_KEY":      "your_live_consumer_key",
        "CONSUMER_SECRET":   "your_live_consumer_secret",
        "BUSINESS_SHORTCODE":"your_live_shortcode",
        "PASSKEY":           "your_live_passkey",
        "CALLBACK_BASE_URL": "https://your-permanent-callback-url.com",
        "SECRET_KEY":        "randomly-generated-64-char-string",
        "JWT_SECRET_KEY":    "another-randomly-generated-64-char-string"
    }
    """
    config_dir  = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), "bizTOOL")
    config_path = os.path.join(config_dir, "config.json")
    os.makedirs(config_dir, exist_ok=True)

    if not os.path.exists(config_path):
        logger.warning(
            "config.json not found at %s. M-Pesa and secret-key features will not work. "
            "Create the file with your live credentials before deploying.", config_path
        )
        return {}

    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to read config.json: %s", e)
        return {}


# Load once at startup — available everywhere in this module
_CONFIG = load_config()


def get_mpesa_access_token():
    """Fetch a Safaricom Daraja OAuth token using credentials from config.json."""
    consumer_key    = _CONFIG.get("CONSUMER_KEY", "")
    consumer_secret = _CONFIG.get("CONSUMER_SECRET", "")

    if not consumer_key or not consumer_secret:
        logger.error("M-Pesa consumer key/secret missing from config.json")
        return None

    # Live URL — sandbox was: https://sandbox.safaricom.co.ke/oauth/v1/generate
    url = "https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"

    try:
        response = requests.get(
            url,
            auth=HTTPBasicAuth(consumer_key, consumer_secret),
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        if response.status_code == 200:
            logger.info("M-Pesa access token obtained successfully")
            return response.json().get('access_token')
        else:
            logger.error("Safaricom auth failed — HTTP %s", response.status_code)
            return None
    except Exception as e:
        logger.error("Exception fetching M-Pesa token: %s", e)
        return None

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



def clean_numeric(value):
    """Safely converts currency strings to floats. 
    Raises ValueError for genuine text errors so the row parser can skip them.
    """
    if value is None:
        return 0.0
        
    # Convert to string and clean common formatting symbols
    clean_val = (
        str(value)
        .upper()
        .replace('KSH', '')
        .replace('KES', '')
        .replace('$', '')
        .replace(',', '')
        .strip()
    )
    
    # Handle empty rows or deliberate placeholders safely
    if not clean_val or clean_val in ['NONE', '', 'AUTO-25%', 'MARKUP']:
        return 0.0
        
    try:
        return float(clean_val)
    except (ValueError, TypeError):
        # CRITICAL: Re-raise the exception so the bulk importer skips this row entirely!
        raise ValueError(f"Invalid currency or numeric format cell: '{value}'")

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

app.config['SECRET_KEY'] = _CONFIG.get(
    'SECRET_KEY',
    os.urandom(32).hex()   # random per-process fallback if config.json missing — sessions won't persist across restarts
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = _CONFIG.get(
    'JWT_SECRET_KEY',
    os.urandom(32).hex()   # same fallback; set a stable value in config.json for production
)
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=10)



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
default_db = resource_path(os.path.join("instance", "inventory.db"))

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
from extensions import db, migrate, jwt


db.init_app(app)
migrate.init_app(app, db)
jwt.init_app(app) 
CORS(app, origins=["http://127.0.0.1:5000", "http://localhost:5000"])

from api import register_api
register_api(app)     # mounts all /api/v1/... blueprints

POINTS_PER_KES    = 0.1    # 1 point per KES 10 spent
POINTS_REDEEM_KES = 0.50   # KES 0.50 per point when redeeming
MIN_REDEMPTION    = 100    # minimum points to redeem

from models import * # This pulls all registered models safely in one pass


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
# SAFE SCHEMA MIGRATION
# ─────────────────────────────────────────
def safe_migrate():
    """
    Adds any new columns to existing client databases without
    destroying data. Uses SQLite PRAGMA to check before altering.
    Safe to run on every startup — skips columns that already exist.
    """
    with app.app_context():
        try:
            from sqlalchemy import text
            cols_to_add = [
                # (table_name, column_name, column_definition)
                ('purchase_order_item', 'expected_cost', 'FLOAT NOT NULL DEFAULT 0.0'),
                
                # Granular toggleable permission overrides for User mapping
                ('user', 'perm_process_sales', 'BOOLEAN NOT NULL DEFAULT 1'),
                ('user', 'perm_view_reports', 'BOOLEAN NOT NULL DEFAULT 0'),
                ('user', 'perm_give_discount', 'BOOLEAN NOT NULL DEFAULT 0'),
                ('user', 'perm_process_refund', 'BOOLEAN NOT NULL DEFAULT 0'),
                ('user', 'perm_manage_products', 'BOOLEAN NOT NULL DEFAULT 0'),
                ('user', 'perm_manage_suppliers', 'BOOLEAN NOT NULL DEFAULT 0'),
                ('user', 'perm_manage_users', 'BOOLEAN NOT NULL DEFAULT 0'),
                ('user', 'perm_view_profit', 'BOOLEAN NOT NULL DEFAULT 0'),
                ('user', 'discount_limit_percentage', 'FLOAT NOT NULL DEFAULT 0.0'),
            ]
            
            for table, col, definition in cols_to_add:
                result = db.session.execute(
                    text(f"PRAGMA table_info({table})")
                ).fetchall()
                existing = [row[1] for row in result]
                if col not in existing:
                    db.session.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
                    )
                    db.session.commit()
            
            # Data Patch: Ensure the core 'admin' user profile is initialized
            # with explicit flags enabled so they don't get locked out.
            admin_user = User.query.filter_by(username='admin').first()
            if admin_user:
                # If they don't have user management permissions enabled explicitly yet, sync them up
                if not admin_user.perm_manage_users:
                    admin_user.perm_process_sales = True
                    admin_user.perm_view_reports = True
                    admin_user.perm_give_discount = True
                    admin_user.perm_process_refund = True
                    admin_user.perm_manage_products = True
                    admin_user.perm_manage_suppliers = True
                    admin_user.perm_manage_users = True
                    admin_user.perm_view_profit = True
                    admin_user.discount_limit_percentage = 100.0  # Unlimited full control
                    db.session.commit()
                    
        except Exception as e:
            db.session.rollback()
            logger.warning("safe_migrate warning: %s", e)



from functools import wraps

def permission_required(permission_attr):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login'))
            # Check the status of the specific boolean permission field dynamically
            if not getattr(current_user, permission_attr, False):
                flash("Access Denied: You do not possess the required privilege.", "danger")
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator            

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
# Add to app.py — anywhere after app is defined
 
from flask_jwt_extended import JWTManager
from flask import jsonify
 
# JWT-specific error handlers
@jwt.unauthorized_loader
def unauthorized_callback(reason):
    return jsonify({'success': False, 'error': 'Missing or invalid token', 'detail': reason}), 401
 
@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_data):
    return jsonify({'success': False, 'error': 'Token has expired. Please log in again.'}), 401
 
@jwt.invalid_token_loader
def invalid_token_callback(reason):
    return jsonify({'success': False, 'error': 'Invalid token', 'detail': reason}), 422
 
# Generic HTTP error handlers
@app.errorhandler(404)
def not_found(e):
    return jsonify({'success': False, 'error': 'Resource not found'}), 404
 
@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({'success': False, 'error': 'HTTP method not allowed for this endpoint'}), 405
 
@app.errorhandler(500)
def internal_error(e):
    return jsonify({'success': False, 'error': 'Internal server error'}), 500


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

    # ── Greeting — computed in EAT (UTC+3) so it matches the cashier's wall clock ──
    # datetime.now() on most servers returns UTC which is 3 hours behind Nairobi.
    # now_eat() adds the +3 offset so "Good Morning" doesn't say "Good Evening".
    eat_now  = now_eat()
    eat_hour = eat_now.hour
    if eat_hour < 12:
        time_of_day = "Good Morning"
    elif eat_hour < 17:
        time_of_day = "Good Afternoon"
    else:
        time_of_day = "Good Evening"

    # ── 1. Basic Stats & Expiry Logic ─────────────────────────────────────────
    all_sales_records = Sale.query.order_by(Sale.timestamp.desc()).limit(50).all()

    total_products = Product.query.count()
    total_clients  = Client.query.count()

    low_stock_products = Product.query.filter(Product.quantity <= 5).all()
    expired_products   = Product.query.filter(Product.expiry_date < today).all()
    expiring_soon      = Product.query.filter(
        Product.expiry_date >= today,
        Product.expiry_date <= thirty_days_from_now
    ).all()

    # ── 2. Financial Totals ───────────────────────────────────────────────────
    total_sales    = db.session.query(db.func.sum(Sale.total_amount)).scalar() or 0.0
    total_discount = db.session.query(db.func.sum(Sale.discount)).scalar() or 0.0
    total_expenses = db.session.query(db.func.sum(Expense.amount)).scalar() or 0.0
    total_tax      = round(total_sales * 0.16 / 1.16, 2)

    # ── 3. Supplier Payables ──────────────────────────────────────────────────
    # SupplierInvoice has no balance_owed column — compute as total_amount - amount_paid
    total_payables = db.session.query(
        db.func.sum(
            SupplierInvoice.total_amount -
            db.func.coalesce(SupplierInvoice.amount_paid, 0)
        )
    ).scalar() or 0.0

    suppliers_data = (
        db.session.query(
            Supplier.id,
            Supplier.name,
            Supplier.items_supplied,
            (
                db.func.sum(SupplierInvoice.total_amount) -
                db.func.sum(db.func.coalesce(SupplierInvoice.amount_paid, 0))
            ).label('balance_owed')
        )
        .join(SupplierInvoice, Supplier.id == SupplierInvoice.supplier_id)
        .group_by(Supplier.id)
        .having(
            (
                db.func.sum(SupplierInvoice.total_amount) -
                db.func.sum(db.func.coalesce(SupplierInvoice.amount_paid, 0))
            ) > 0
        )
        .order_by(db.text('balance_owed DESC'))
        .limit(5)
        .all()
    )

    # ── 4. Sales Trend — last 7 days, EAT-aware ──────────────────────────────
    # Sale.timestamp is stored as UTC (datetime.utcnow default in models.py).
    # Adding +3 hours in SQLite converts each timestamp to EAT before grouping,
    # so a sale at 01:00 EAT (22:00 UTC previous day) lands on the correct date.
    today_eat  = (datetime.utcnow() + timedelta(hours=3)).date()
    last_7_days = [today_eat - timedelta(days=i) for i in range(6, -1, -1)]
    # last_7_days[0] = 6 days ago, last_7_days[6] = today (EAT)

    eat_date_expr = db.func.strftime(
        '%Y-%m-%d',
        db.func.datetime(Sale.timestamp, '+3 hours')
    )

    sales_by_date = (
        db.session.query(
            eat_date_expr.label('eat_date'),
            db.func.sum(Sale.total_amount).label('revenue')
        )
        .filter(eat_date_expr >= str(last_7_days[0]))   # last 7 days only
        .group_by('eat_date')
        .all()
    )

    # Map date string → revenue float for O(1) lookup
    sales_map   = {row.eat_date: float(row.revenue) for row in sales_by_date}
    chart_labels = [d.strftime('%a') for d in last_7_days]          # ['Mon','Tue',...]
    chart_values = [sales_map.get(str(d), 0.0) for d in last_7_days]

    # ── 5. Pie Chart — top 6 products by all-time revenue ────────────────────
    # Kept separate from the bar chart so each chart shows meaningful data:
    #   bar  = daily revenue trend (last 7 days)
    #   pie  = which products earn the most overall
    top_products = (
        db.session.query(
            Sale.product_name,
            db.func.sum(Sale.total_amount).label('revenue')
        )
        .group_by(Sale.product_name)
        .order_by(db.func.sum(Sale.total_amount).desc())
        .limit(6)
        .all()
    )
    pie_labels = [row.product_name for row in top_products]
    pie_values = [float(row.revenue)  for row in top_products]

    # ── 6. Fast Moving Products ───────────────────────────────────────────────
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

    # ── 7. Render Template ────────────────────────────────────────────────────
    return render_template(
        'dashboard.html',

        # Greeting
        time_of_day=time_of_day,

        # Table data
        sales=all_sales_records,
        fast_moving_products=fast_moving_products,
        low_stock_products=low_stock_products,

        # Supplier payables
        total_supplier_payables=round(total_payables, 2),
        suppliers_with_balances=suppliers_data,

        # Expiry data
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

        # Bar chart — daily revenue last 7 days
        sales_labels=chart_labels,
        sales_trend=chart_values,

        # Pie chart — top products by revenue (separate from bar data)
        sales_data=pie_values,
        pie_labels=pie_labels,

        # Misc
        today=today,
        current_date=today,
        current_year=today.year,
        today_date=today.strftime('%Y-%m-%d'),   # for expense modal date input
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
@permission_required('perm_manage_products') # Replacing string role check
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
            logger.error("Product save error: %s", e)
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
        logger.error("Mark waste error: %s", e)
        flash("System error recording waste. Please try again.", "danger")

    return redirect(url_for('products'))

@app.route('/sales')
@login_required
def view_sales():  # Renamed to avoid conflict with 'sales' variable in template
    # Cashiers or users without global report privileges can only see their own recorded receipts
    if not current_user.perm_view_reports:
        all_sales = Sale.query.filter_by(user_id=current_user.id).order_by(Sale.timestamp.desc()).all()
        logger.debug("Filtered sales history fetched from DB for current user ID: %s", current_user.id)
    else:
        # Supervisors, Admins, or custom authorized roles get full access to all transactions
        all_sales = Sale.query.order_by(Sale.timestamp.desc()).all()
        logger.debug("Sales fetched from DB")
        
    return render_template('sales.html', sales=all_sales)


@app.route('/add_sale', methods=['POST'])
@login_required
def add_sale():
    product_id = request.form.get('product_id')
    quantity = int(request.form.get('quantity'))
    discount = float(request.form.get('discount') or 0)
    tax_rate = float(request.form.get('tax_rate') or 0.16)

    product = Product.query.get_or_404(product_id)

    # ── DISCOUNT THRESHOLD CAP ENFORCEMENT ─────────────────────
    if discount > 0:
        if not current_user.can_give_discount:
            flash("Access Denied: You do not have permission to authorize discounts.", "danger")
            return redirect(url_for('sales'))
            
        if discount > current_user.discount_limit_percentage:
            flash(f"Unauthorized: Your discount authorization tier caps at {current_user.discount_limit_percentage}%.", "danger")
            return redirect(url_for('sales'))

    # Validate stock
    if product.quantity < quantity:
        flash("Not enough stock available.", "danger")
        return redirect(url_for('sales'))

    # Create sale record
    sale = Sale(
        product_name=product.name,
        product_id=product.id,
        quantity_sold=quantity,
        unit_price=float(product.selling_price or 0.0),
        discount=discount,
        tax_rate=tax_rate
    )

    # 2. Run the calculation. 
    # This sets sale.tax_amount internally AND returns the correct VAT-inclusive total.
    final_payable_amount = sale.calculate_total()

    # 3. Assign the returned amount directly to total_price
    sale.total_price = final_payable_amount

    # Update product stock
    product.quantity -= quantity

    db.session.add(sale)
    db.session.commit()

    flash(f"Sale recorded: {product.name} x{quantity}", "success")
    return redirect(url_for('sales'))


@app.route('/expense_report', methods=['GET', 'POST'])
@login_required
@permission_required('perm_view_reports')
def expense_report():
    if current_user.role.lower() != 'admin':
        flash('Access Denied.', 'danger')
        return redirect(url_for('dashboard'))

    start_date = request.args.get('start_date')
    end_date   = request.args.get('end_date')

    query = Expense.query

    if start_date and end_date:
        query = query.filter(Expense.date.between(start_date, end_date))
        # Total for the filtered date range only
        total_filtered = db.session.query(func.sum(Expense.amount))\
            .filter(Expense.date.between(start_date, end_date)).scalar() or 0.0
    else:
        # No date filter — total covers ALL expenses
        total_filtered = db.session.query(func.sum(Expense.amount)).scalar() or 0.0

    expenses = query.order_by(Expense.date.desc()).all()

    # Category breakdown — useful for the template to show a pie/bar chart
    category_totals = db.session.query(
        Expense.category,
        func.sum(Expense.amount).label('total')
    ).group_by(Expense.category).all()

    return render_template(
        'expense_report.html',
        expenses=expenses,
        total_filtered=round(total_filtered, 2),
        start_date=start_date,
        end_date=end_date,
        category_totals=category_totals,
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

    unit_price=float(product.selling_price or product.price or 0)
    if unit_price is None:
        flash('Product price is missing.', 'danger')
        return redirect(url_for('products'))

    total_price = quantity_sold * unit_price

    sale = Sale(product_name=product.name, quantity_sold=quantity_sold, unit_price=float(product.selling_price or product.price or 0), total_price=total_price)
    product.quantity -= quantity_sold

    logger.debug("Attempting to record sale")
    logger.debug("Product: %s", product.name)
    logger.debug("Quantity sold: %s", quantity_sold)
    logger.debug("Unit price: %s", unit_price)
    logger.debug("Total price: %s", total_price)
    logger.debug("DB URI configured")

    try:
        db.session.add(sale)
        db.session.commit()
        logger.info("Sale saved — ID: %s", sale.id)
    except Exception as e:
        db.session.rollback()
        logger.error("Sale save error: %s", e)

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
    sales            = receipt_obj.sales
    payment          = Payment.query.filter_by(receipt_id=receipt_obj.id).first()

    subtotal         = round(sum(s.total_amount or 0 for s in sales), 2)
    total_discounts  = round(sum(s.discount or 0  for s in sales), 2)
    total_tax        = round(subtotal * 0.16 / 1.16, 2)

    cash_received = float(
        receipt_obj.cash_received
        if (hasattr(receipt_obj, 'cash_received') and receipt_obj.cash_received)
        else request.args.get('cash_received', 0)
    )
    change_given = float(
        receipt_obj.change_given
        if (hasattr(receipt_obj, 'change_given') and receipt_obj.change_given)
        else request.args.get('change_given', 0)
    )

    return render_template(
        'receipt.html',
        receipt=receipt_obj,
        sales=sales,
        payment=payment,
        subtotal=subtotal,
        total_discounts=total_discounts,
        total_tax=total_tax,
        cash_received=cash_received,
        change_given=change_given,
        is_pdf=False,
    )


@app.route('/receipt/<int:receipt_id>/escpos')
@login_required
def receipt_escpos(receipt_id):
    """
    Generates raw ESC/POS binary data for 58mm or 80mm thermal printers
    using exact logic from your standard receipt route.
    """
    receipt_obj = Receipt.query.get_or_404(receipt_id)
    sales       = receipt_obj.sales
    payment     = Payment.query.filter_by(receipt_id=receipt_obj.id).first()

    # Calculate totals matching your HTML view logic
    subtotal        = round(sum(s.total_amount or 0 for s in sales), 2)
    total_discounts = round(sum(s.discount or 0  for s in sales), 2)
    total_tax       = round(subtotal * 0.16 / 1.16, 2)

    # Safely extract cash details matching your route attributes
    cash_received = float(receipt_obj.cash_received if (hasattr(receipt_obj, 'cash_received') and receipt_obj.cash_received) else 0)
    change_given  = float(receipt_obj.change_given if (hasattr(receipt_obj, 'change_given') and receipt_obj.change_given) else 0)

    # 1. Load setup limits from your configuration file (_CONFIG)
    shop_name = _CONFIG.get("SHOP_NAME", "BIZTOOL ENTERPRISES")
    address   = _CONFIG.get("SHOP_ADDRESS", "Nairobi, Kenya")
    phone     = _CONFIG.get("SHOP_PHONE", "")
    pin       = _CONFIG.get("SHOP_PIN", "KRA PIN PENDING")
    footer    = _CONFIG.get("RECEIPT_FOOTER", "Thank you for shopping with us!\nWelcome again.")
    
    # Auto-fallback width handling (58mm = 32 chars per line, 80mm = 48 chars)
    width_mode = int(_CONFIG.get("PRINTER_WIDTH", 58))
    max_chars = 32 if width_mode == 58 else 48

    # 2. ESC/POS Command Hex Bytes 
    ESC = b'\x1b'
    GS = b'\x1d'
    INITIALIZE    = ESC + b'@'
    ALIGN_CENTER  = ESC + b'a\x01'
    ALIGN_LEFT    = ESC + b'a\x00'
    BOLD_ON       = ESC + b'E\x01'
    BOLD_OFF      = ESC + b'E\x00'
    DBL_SIZE      = GS + b'!\x11' 
    TXT_NORMAL    = GS + b'!\x00'
    FEED_CUT      = ESC + b'd\x03' + GS + b'V\x41\x03' # Feed 3 lines and cut paper

    # 3. Stream Byte Compilation
    buf = bytearray()
    buf.extend(INITIALIZE)

    # Header section
    buf.extend(ALIGN_CENTER)
    buf.extend(BOLD_ON)
    buf.extend(DBL_SIZE)
    buf.extend(f"{shop_name}\n".encode('ascii', errors='ignore'))
    buf.extend(TXT_NORMAL)
    buf.extend(BOLD_OFF)
    
    buf.extend(f"{address}\n".encode('ascii', errors='ignore'))
    if phone:
        buf.extend(f"Tel: {phone}\n".encode('ascii', errors='ignore'))
    buf.extend(f"PIN: {pin}\n".encode('ascii', errors='ignore'))
    buf.extend(f"{'-'*max_chars}\n".encode('ascii'))

    # Metadata
    buf.extend(ALIGN_LEFT)
    ts_str = receipt_obj.created_at.strftime('%Y-%m-%d %H:%M') if hasattr(receipt_obj, 'created_at') else now_eat().strftime('%Y-%m-%d %H:%M')
    buf.extend(f"Receipt No: #{receipt_obj.id}\n".encode('ascii'))
    buf.extend(f"Date: {ts_str}\n".encode('ascii'))
    buf.extend(f"Cashier: {current_user.username.capitalize()}\n".encode('ascii'))
    if payment:
        buf.extend(f"Payment Method: {payment.payment_method}\n".encode('ascii'))
    buf.extend(f"{'-'*max_chars}\n".encode('ascii'))

    # Items Table Heading
    buf.extend(BOLD_ON)
    if max_chars == 32:
        buf.extend(f"Item            Qty     Total\n".encode('ascii'))
    else:
        buf.extend(f"Item Description         Qty    Price     Total\n".encode('ascii'))
    buf.extend(BOLD_OFF)
    buf.extend(f"{'-'*max_chars}\n".encode('ascii'))

    # Process items loop
    for sale in sales:
        qty = sale.quantity_sold
        price = sale.unit_price or 0.0
        total = sale.total_amount or (qty * price)

        if max_chars == 32:
            # Clean layout for 58mm printer widths
            buf.extend(f"{sale.product_name[:max_chars]}\n".encode('ascii', errors='ignore'))
            details_line = f"  {qty} x {int(price)}".ljust(max_chars - 10) + f"{int(total):>10}\n"
            buf.extend(details_line.encode('ascii'))
        else:
            # Tabular layout optimized for standard wide 80mm printers
            name_truncated = sale.product_name[:max_chars-25]
            line = f"{name_truncated.ljust(max_chars-25)} {qty:>4} {price:>9.2f} {total:>9.2f}\n"
            buf.extend(line.encode('ascii', errors='ignore'))

    buf.extend(f"{'-'*max_chars}\n".encode('ascii'))

    # Totals Section Alignment Helper
    def build_total_line(label, value):
        val_str = f"KES {value:,.2f}"
        return f"{label.ljust(max_chars - len(val_str))}{val_str}\n".encode('ascii')

    buf.extend(build_total_line("Subtotal:", subtotal + total_discounts))
    if total_discounts > 0:
        buf.extend(build_total_line("Discounts:", total_discounts))
    buf.extend(build_total_line("16% VAT Incl:", total_tax))
    
    buf.extend(BOLD_ON)
    buf.extend(build_total_line("TOTAL AMOUNT:", subtotal))
    buf.extend(BOLD_OFF)
    buf.extend(f"{'.'*max_chars}\n".encode('ascii'))
    
    buf.extend(build_total_line("Cash Received:", cash_received))
    buf.extend(build_total_line("Change Due:", change_given))
    buf.extend(f"{'='*max_chars}\n".encode('ascii'))

    # Footer messages text output parsing
    buf.extend(ALIGN_CENTER)
    for line in footer.split('\n'):
        buf.extend(f"{line}\n".encode('ascii', errors='ignore'))
    
    buf.extend(FEED_CUT)

    response = make_response(bytes(buf))
    response.headers['Content-Type'] = 'application/octet-stream'
    return response

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

@app.route('/receipt/<int:receipt_id>/email', methods=['POST'])
@login_required
def email_receipt(receipt_id):
    """
    Processes receipt data and securely dispatches a clean text summary 
    to the client via the Resend HTTP API cloud gateway, bypassing local SMTP.
    """
    target_email = request.form.get('client_email', '').strip()
    if not target_email:
        return jsonify({'success': False, 'message': 'Valid email address required'}), 400

    # 1. Fetch exact matching business records
    receipt_obj = Receipt.query.get_or_404(receipt_id)
    sales       = receipt_obj.sales
    payment     = Payment.query.filter_by(receipt_id=receipt_obj.id).first()

    shop_name  = _CONFIG.get("SHOP_NAME", "BIZTOOL ENTERPRISES")
    resend_key = _CONFIG.get("RESEND_API_KEY")

    # Guard check: Ensure user has initialized the Resend API configuration
    if not resend_key or "YOUR_RAW_RESEND_API_KEY" in resend_key:
        return jsonify({
            'success': False, 
            'message': 'Email skipped: Please update RESEND_API_KEY variable inside config.json'
        }), 200

    # 2. Build a clean, structured Plain-Text Digital Receipt Body
    email_body = f"========================================\n"
    email_body += f"       {shop_name} DIGITAL RECEIPT      \n"
    email_body += f"========================================\n"
    email_body += f"Receipt ID: #{receipt_obj.id}\n"
    email_body += f"Date: {receipt_obj.timestamp.strftime('%Y-%m-%d %H:%M') if hasattr(receipt_obj, 'timestamp') else 'Recent'}\n"
    email_body += f"Cashier: {current_user.username.capitalize()}\n"
    
    # Secure Attribute Guard: Check for .method, then .payment_mode, fallback cleanly if missing
    if payment:
        payment_mode = getattr(payment, 'method', getattr(payment, 'payment_mode', 'M-Pesa/Cash'))
        email_body += f"Payment Method: {payment_mode}\n"
    else:
        email_body += f"Payment Method: M-Pesa/Cash\n"
        
    email_body += f"----------------------------------------\n\n"
    
    email_body += f"ITEMS PURCHASED:\n"
    for sale in sales:
        email_body += f"- {sale.product_name} (x{sale.quantity_sold}): KES {sale.total_amount:,.2f}\n"
        
    email_body += f"\n----------------------------------------\n"
    email_body += f"GRAND TOTAL: KES {receipt_obj.total_amount:,.2f}\n"
    email_body += f"========================================\n"
    email_body += f"\nThank you for shopping with us! Welcome again."

    try:
        # 3. Deliver via secure cloud web API on outbound web port 443
        # Free sandboxes send from onboarding@resend.dev until a custom domain is verified
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {resend_key}",
                "Content-Type": "application/json"
            },
            json={
                "from": f"{shop_name} <onboarding@resend.dev>",
                "to": target_email,
                "subject": f"Your Receipt #{receipt_obj.id} from {shop_name}",
                "text": email_body
            },
            timeout=10
        )

        if response.status_code in [200, 201]:
            return jsonify({'success': True, 'message': f'Receipt successfully emailed to {target_email}!'})
        else:
            return jsonify({
                'success': False, 
                'message': f'Cloud Mail Delivery Error ({response.status_code}): {response.text}'
            }), 400

    except Exception as e:
        logger.error("Cloud Email Delivery Exception: %s", str(e))
        return jsonify({'success': False, 'message': f'Failed to connect to mail cloud gateway: {str(e)}'}), 500

@app.route('/download_invoice/<int:receipt_id>')
@login_required
def download_invoice(receipt_id):
    receipt_obj = Receipt.query.get_or_404(receipt_id)
    sales            = receipt_obj.sales
    payment          = Payment.query.filter_by(receipt_id=receipt_obj.id).first()

    subtotal         = round(sum(s.total_amount or 0 for s in sales), 2)
    total_discounts  = round(sum(s.discount or 0  for s in sales), 2)
    total_tax        = round(subtotal * 0.16 / 1.16, 2)

    cash_received = float(
        receipt_obj.cash_received
        if (hasattr(receipt_obj, 'cash_received') and receipt_obj.cash_received)
        else 0
    )
    change_given = float(
        receipt_obj.change_given
        if (hasattr(receipt_obj, 'change_given') and receipt_obj.change_given)
        else 0
    )

    rendered = render_template(
        'receipt.html',
        receipt=receipt_obj,
        sales=sales,
        payment=payment,
        subtotal=subtotal,
        total_discounts=total_discounts,
        total_tax=total_tax,
        cash_received=cash_received,
        change_given=change_given,
        is_pdf=True,
    )
    try:
        import pdfkit
        pdf = pdfkit.from_string(rendered, False)
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'inline; filename=Invoice_{receipt_id}.pdf'
        return response
    except Exception as e:
        logger.error("PDF error: %s", e)
        return rendered

# =============================================================================
# SALES ANALYTICS ROUTES
# Replace the existing daily_sales route (lines 1061-1075) with this block.
# The old /sales/report JSON endpoint can stay as-is — it is not affected.
# =============================================================================


@app.route('/sales/analytics')
@login_required
def sales_analytics():
    """
    Unified daily / weekly / monthly sales analytics page.
    Accessible to all logged-in users; export and full breakdown admin-only.

    Query parameters:
        mode        — 'daily' | 'weekly' | 'monthly'  (default: daily)
        start_date  — YYYY-MM-DD  (optional, restricts range)
        end_date    — YYYY-MM-DD  (optional, restricts range)
        search      — product name substring filter
    """

    # ── Mode selector ─────────────────────────────────────────────────────────
    mode = request.args.get('mode', 'daily')
    if mode not in ('daily', 'weekly', 'monthly'):
        mode = 'daily'

    # ── Date range params ─────────────────────────────────────────────────────
    start_str = request.args.get('start_date', '').strip()
    end_str   = request.args.get('end_date',   '').strip()
    search    = request.args.get('search', '').strip()

    today_eat = (datetime.utcnow() + timedelta(hours=3)).date()

    # Default ranges per mode
    if mode == 'daily':
        default_start = today_eat - timedelta(days=29)   # last 30 days
        default_end   = today_eat
    elif mode == 'weekly':
        default_start = today_eat - timedelta(weeks=11)  # last 12 weeks
        default_end   = today_eat
    else:  # monthly
        # Go back 11 months to the 1st of that month
        first_of_this_month = today_eat.replace(day=1)
        y = first_of_this_month.year
        m = first_of_this_month.month - 11
        if m <= 0:
            m += 12; y -= 1
        default_start = date(y, m, 1)
        default_end   = today_eat

    try:
        start_date = date.fromisoformat(start_str) if start_str else default_start
    except ValueError:
        start_date = default_start

    try:
        end_date = date.fromisoformat(end_str) if end_str else default_end
    except ValueError:
        end_date = default_end

    # Clamp so start <= end
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    # ── EAT-aware date expression ─────────────────────────────────────────────
    # Sale.timestamp is stored as UTC. +3 hours converts to EAT before grouping.
    eat_date_expr = func.date(func.datetime(Sale.timestamp, '+3 hours'))

    # ── Base query filter ─────────────────────────────────────────────────────
    base_filter = [
        eat_date_expr >= str(start_date),
        eat_date_expr <= str(end_date),
    ]
    if search:
        base_filter.append(Sale.product_name.ilike(f'%{search}%'))

    # ── GROUP BY expression depends on mode ───────────────────────────────────
    if mode == 'daily':
        period_expr  = eat_date_expr.label('period')
        period_label = 'Date'

    elif mode == 'weekly':
        # SQLite: strftime('%Y-W%W') gives year + ISO week number
        period_expr  = func.strftime(
            '%Y-W%W',
            func.datetime(Sale.timestamp, '+3 hours')
        ).label('period')
        period_label = 'Week'

    else:  # monthly
        period_expr  = func.strftime(
            '%Y-%m',
            func.datetime(Sale.timestamp, '+3 hours')
        ).label('period')
        period_label = 'Month'

    # ── Main aggregated query ─────────────────────────────────────────────────
    rows = (
        db.session.query(
            period_expr,
            func.sum(Sale.total_amount).label('revenue'),
            func.sum(Sale.quantity_sold).label('units'),
            func.count(Sale.id).label('transactions'),
            func.sum(Sale.discount).label('discounts'),
        )
        .filter(*base_filter)
        .group_by('period')
        .order_by(db.text('period DESC'))
        .all()
    )

    # ── Summary totals for the selected range ─────────────────────────────────
    total_revenue      = round(sum(r.revenue      or 0 for r in rows), 2)
    total_units        = int(  sum(r.units         or 0 for r in rows))
    total_transactions = int(  sum(r.transactions  or 0 for r in rows))
    total_discounts    = round(sum(r.discounts     or 0 for r in rows), 2)
    total_tax          = round(total_revenue * 0.16 / 1.16, 2)
    avg_per_period     = round(total_revenue / len(rows), 2) if rows else 0.0

    # ── Top products for the selected range ───────────────────────────────────
    top_products = (
        db.session.query(
            Sale.product_name,
            func.sum(Sale.total_amount).label('revenue'),
            func.sum(Sale.quantity_sold).label('units'),
            func.count(Sale.id).label('transactions'),
        )
        .filter(*base_filter)
        .group_by(Sale.product_name)
        .order_by(func.sum(Sale.total_amount).desc())
        .limit(10)
        .all()
    )

    # ── Individual sale lines (for search results / detailed drill-down) ──────
    sale_lines = (
        Sale.query
        .filter(*base_filter)
        .order_by(Sale.timestamp.desc())
        .limit(200)
        .all()
    )

    # ── Chart data (chronological for the chart — reverse of table order) ─────
    chart_rows   = list(reversed(rows))
    chart_labels = []
    chart_values = []

    for r in chart_rows:
        p = r.period or ''
        if mode == 'monthly' and len(p) == 7:
            # '2026-03' → 'Mar 26'
            try:
                lbl = date(int(p[:4]), int(p[5:7]), 1).strftime('%b %y')
            except Exception:
                lbl = p
        elif mode == 'weekly':
            lbl = p   # '2026-W23'
        else:
            # '2026-06-09' → '09 Jun'
            try:
                lbl = date.fromisoformat(p).strftime('%d %b')
            except Exception:
                lbl = p
        chart_labels.append(lbl)
        chart_values.append(round(float(r.revenue or 0), 2))

    return render_template(
        'sales_analytics.html',
        # Mode / filters
        mode=mode,
        period_label=period_label,
        start_date=str(start_date),
        end_date=str(end_date),
        search=search,
        # Table rows
        rows=rows,
        sale_lines=sale_lines,
        top_products=top_products,
        # Summary cards
        total_revenue=total_revenue,
        total_units=total_units,
        total_transactions=total_transactions,
        total_discounts=total_discounts,
        total_tax=total_tax,
        avg_per_period=avg_per_period,
        # Chart
        chart_labels=chart_labels,
        chart_values=chart_values,
        # Misc
        today=today_eat,
    )


# Keep the old URL alive so existing bookmarks / nav links still work
@app.route('/daily_sales')
@login_required
def daily_sales():
    return redirect(url_for('sales_analytics', mode='daily'))

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
    """
    Triggers an instant Safaricom M-Pesa STK PIN prompt directly onto the customer's phone line
    associated with this specific Sale. Implements strict verification loops and data-tracking sanitization.
    """
    # 1. Grab phone and amount from the request form or active cart container
    raw_phone = request.form.get('phone_number', '').strip()
    amount = request.form.get('amount')
    
    if not raw_phone or not amount:
        flash("Phone number and amount are required to push M-Pesa payment.", "danger")
        return redirect(url_for('view_sale', sale_id=sale_id))

    # FIX: Strict input sanitization to ensure phone number format is exactly 12 pure numeric digits (2547XXXXXXXX)
    # This strips out accidentally added '+', spaces, dashes, or leading zeros.
    phone_number = raw_phone.replace("+", "").replace("-", "").replace(" ", "")
    if phone_number.startswith("0"):
        phone_number = "254" + phone_number[1:]
    
    # Validation step to ensure payload reliability before wasting execution memory
    if not phone_number.isdigit() or len(phone_number) != 12:
        flash(f"Invalid M-Pesa phone number format ('{raw_phone}'). Use 2547XXXXXXXX layout with exactly 12 numbers.", "danger")
        return redirect(url_for('view_sale', sale_id=sale_id))

    # 2. TWEAK: Prevent duplicate database row state conflicts or double-prompting customer handsets
    existing_pending = MpesaPayment.query.filter_by(sale_id=sale_id, status="Pending").first()
    if existing_pending:
        flash("An active M-Pesa prompt is already pending for this sale. Please verify your phone or wait for it to time out.", "warning")
        return redirect(url_for('view_sale', sale_id=sale_id))

    # Use the module-level get_mpesa_access_token (loads from config.json)
    access_token = get_mpesa_access_token()
    if not access_token:
        flash("Failed to authenticate with Safaricom Daraja Gateway.", "danger")
        return redirect(url_for('view_sale', sale_id=sale_id))

    # All credentials from config.json — never hardcoded
    business_shortcode = _CONFIG.get("BUSINESS_SHORTCODE", "")
    passkey            = _CONFIG.get("PASSKEY", "")

    if not business_shortcode or not passkey:
        flash("M-Pesa shortcode/passkey not configured. Add them to config.json.", "danger")
        return redirect(url_for('view_sale', sale_id=sale_id))
    
    # FIX: Maintain explicit server timestamp in EAT for Safaricom
    timestamp = now_eat().strftime('%Y%m%d%H%M%S')

    # Dynamically construct base64 authorization password
    raw_password = f"{business_shortcode}{passkey}{timestamp}"
    password = base64.b64encode(raw_password.encode('utf-8')).decode('utf-8')

    # Callback URL loaded from config.json — never hardcoded
    callback_base_url = _CONFIG.get("CALLBACK_BASE_URL", "")
    if not callback_base_url:
        flash("Callback URL not configured. Add CALLBACK_BASE_URL to config.json.", "danger")
        return redirect(url_for('view_sale', sale_id=sale_id))

    cleaned_amount = clean_numeric(amount)
    if cleaned_amount <= 0:
        flash("Invalid payment amount formatting or transaction total empty.", "danger")
        return redirect(url_for('view_sale', sale_id=sale_id))

    final_saf_amount = int(cleaned_amount)

    saf_payload = {
        "BusinessShortCode": business_shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": final_saf_amount,
        "PartyA": phone_number,
        "PartyB": business_shortcode,
        "PhoneNumber": phone_number,
        "CallBackURL": f"{callback_base_url}/api/v1/payments/callback",
        "AccountReference": f"SaleID-{sale_id}",
        "TransactionDesc": "Desktop POS Sale Checkout"
    }

    try:
        url = "https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        saf_res = requests.post(url, json=saf_payload, headers=headers, timeout=10)
        res_data = saf_res.json()

        logger.info("STK Push response — HTTP %s", saf_res.status_code)

        if res_data.get("ResponseCode") == "0":
            checkout_id = res_data.get("CheckoutRequestID")
            new_payment = MpesaPayment(
                checkout_request_id=checkout_id,
                phone=phone_number,
                amount=cleaned_amount,
                status="Pending",
            )
            db.session.add(new_payment)
            db.session.commit()
            flash("STK Push prompt initialized successfully. Please enter your M-Pesa PIN on your handset.", "success")
        else:
            error_msg = res_data.get('errorMessage', res_data.get('CustomerMessage', 'Unknown error'))
            flash(f"M-Pesa STK Push rejected: {error_msg}", "warning")

    except Exception as e:
        db.session.rollback()
        logger.error("STK Push network error: %s", e)
        flash(f"Network error connecting to Safaricom: {str(e)}", "danger")

    return redirect(url_for('view_sale', sale_id=sale_id))

@app.route('/api/v1/payments/stk-push', methods=['POST'])
@login_required
def api_stk_push():
    """
    Handles async STK Push requests from the cart modal.
    All credentials loaded from config.json — nothing hardcoded.
    """
    data      = request.get_json() or {}
    raw_phone = data.get('phone', '').strip()
    amount    = data.get('amount')

    if not raw_phone or not amount:
        return jsonify({"success": False, "error": "Missing phone number or transaction amount."}), 400

    # Normalise to 2547XXXXXXXX
    phone_number = raw_phone.replace("+", "").replace("-", "").replace(" ", "")
    if phone_number.startswith("0"):
        phone_number = "254" + phone_number[1:]
    if not phone_number.isdigit() or len(phone_number) != 12:
        return jsonify({"success": False, "error": f"Invalid format '{raw_phone}'. Use 2547XXXXXXXX."}), 400

    access_token = get_mpesa_access_token()
    if not access_token:
        return jsonify({"success": False, "error": "Failed to authenticate with Safaricom. Check config.json."}), 500

    # All sensitive values from config.json
    business_shortcode = _CONFIG.get("BUSINESS_SHORTCODE", "")
    passkey            = _CONFIG.get("PASSKEY", "")
    callback_base_url  = _CONFIG.get("CALLBACK_BASE_URL", "")

    if not all([business_shortcode, passkey, callback_base_url]):
        return jsonify({"success": False, "error": "M-Pesa config incomplete. Check config.json."}), 500

    # Timestamp in EAT — Safaricom uses East Africa Time
    timestamp    = now_eat().strftime('%Y%m%d%H%M%S')
    raw_password = f"{business_shortcode}{passkey}{timestamp}"
    password     = base64.b64encode(raw_password.encode('utf-8')).decode('utf-8')

    saf_payload = {
        "BusinessShortCode": business_shortcode,
        "Password":          password,
        "Timestamp":         timestamp,
        "TransactionType":   "CustomerPayBillOnline",
        "Amount":            int(float(amount)),
        "PartyA":            phone_number,
        "PartyB":            business_shortcode,
        "PhoneNumber":       phone_number,
        "CallBackURL":       f"{callback_base_url}/api/v1/payments/callback",
        "AccountReference":  "bizTOOL-POS",
        "TransactionDesc":   "POS Checkout"
    }

    try:
        url      = "https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
        headers  = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        saf_res  = requests.post(url, json=saf_payload, headers=headers, timeout=10)
        res_data = saf_res.json()

        logger.info("STK Push (cart) — HTTP %s", saf_res.status_code)

        if res_data.get("ResponseCode") == "0":
            checkout_id    = res_data.get("CheckoutRequestID")
            payment_record = MpesaPayment(
                checkout_request_id=checkout_id,
                amount=amount,
                phone=phone_number,
                status="Pending"
            )
            db.session.add(payment_record)
            db.session.commit()
            return jsonify({"success": True, "checkout_request_id": checkout_id}), 200
        else:
            error_description = res_data.get('errorMessage', res_data.get('CustomerMessage', 'Rejection from Safaricom.'))
            return jsonify({"success": False, "error": error_description}), 400

    except Exception as e:
        db.session.rollback()
        logger.error("STK Push (cart) fatal error: %s", e)
        return jsonify({"success": False, "error": "Internal error triggering STK Push."}), 500

@app.route('/api/v1/payments/check-status/<checkout_request_id>', methods=['GET'])
@login_required
def check_payment_status(checkout_request_id):
    """
    Returns pure JSON tracking state for the UI frontend polling loop.
    Protected defensively against structural model mismatches.
    """
    try:
        mpesa_record = MpesaPayment.query.filter_by(checkout_request_id=checkout_request_id).first()

        if not mpesa_record:
            return jsonify({
                "status": "NOTFOUND",
                "message": "Transaction record not found.",
                "result_code": 999
            }), 404

        normalized_status = str(mpesa_record.status).strip().upper()

        # DEFENSIVE EVALUATION: Extract message dynamically from whatever column exists
        err_msg = ""
        if hasattr(mpesa_record, 'message') and mpesa_record.message:
            err_msg = mpesa_record.message
        elif hasattr(mpesa_record, 'description') and mpesa_record.description:
            err_msg = mpesa_record.description
        elif hasattr(mpesa_record, 'result_desc') and mpesa_record.result_desc:
            err_msg = mpesa_record.result_desc

        logger.info("M-Pesa poll — %s | status: %s | reason: %s", checkout_request_id, normalized_status, err_msg)

        # FIX: Only compute result_code when the transaction has actually resolved.
        # The old code ran is_insufficient even during PENDING (when err_msg is empty),
        # which always produced result_code 1032 — never signalling the JS correctly.
        if normalized_status == "SUCCESS":
            result_code = 0
        elif normalized_status == "FAILED":
            # result_code 1 = insufficient funds (JS uses this to trigger the flash banner)
            # result_code 1032 = any other failure (cancelled, timed out, wrong PIN, etc.)
            result_code = 1 if ("balance" in err_msg.lower() or "insufficient" in err_msg.lower()) else 1032
        else:
            # Still PENDING — return a neutral code; the JS loop continues polling
            result_code = -1

        # Build the human-readable message for the frontend
        if normalized_status == "FAILED":
            display_message = err_msg or "Transaction declined on customer device."
        elif normalized_status == "SUCCESS":
            display_message = "Payment completed successfully."
        else:
            display_message = "Processing..."

        return jsonify({
            "status": normalized_status,
            "receipt": getattr(mpesa_record, 'reference', '') or getattr(mpesa_record, 'mpesa_receipt_number', '') or "",
            "message": display_message,
            "result_code": result_code
        }), 200

    except Exception as e:
        logger.error("check_payment_status error: %s", e, exc_info=True)
        return jsonify({
            "status": "ERROR",
            "message": "Internal processing failure checking status parameters."
        }), 500


@app.route('/api/v1/payments/callback', methods=['POST'])
def mpesa_callback_api():
    """
    Public webhook hit by Safaricom.
    Handles Success, User Cancellation, and Insufficient Funds.
    """
    saf_data = request.get_json() or {}
    try:
        stk_callback = saf_data.get('Body', {}).get('stkCallback', {})
        checkout_id = stk_callback.get('CheckoutRequestID')
        result_code = stk_callback.get('ResultCode')
        result_desc = stk_callback.get('ResultDesc')  # Safaricom's specific error message

        mpesa_record = MpesaPayment.query.filter_by(checkout_request_id=checkout_id).first()

        if mpesa_record:
            if result_code == 0:
                # Success Logic
                meta = stk_callback.get('CallbackMetadata', {}).get('Item', [])
                receipt = next((item['Value'] for item in meta if item['Name'] == 'MpesaReceiptNumber'), None)

                mpesa_record.status = "SUCCESS"       # FIX: was "Success" — now strict uppercase so
                mpesa_record.reference = receipt       #      check_payment_status comparison never mismatches
                mpesa_record.message = "Payment Successful"
            else:
                # Failure Logic (Cancelled, Insufficient Funds, etc)
                mpesa_record.status = "FAILED"         # FIX: was "Failed" — now strict uppercase
                mpesa_record.message = result_desc     # Safaricom sends the human-readable reason here

            db.session.commit()
            logger.info("M-Pesa callback — %s → %s. Reason: %s", checkout_id, mpesa_record.status, result_desc)

    except Exception as e:
        db.session.rollback()
        logger.error("M-Pesa webhook error: %s", e)

    # Safaricom ALWAYS expects a 200 OK response, even if the transaction failed.
    return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"}), 200


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
            logger.error("Expense save error: %s", e)
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
        logger.error("DB error view_expenses: %s", e)
        flash("Could not load expense data. Check database connection.", "warning")
        return redirect(url_for('dashboard'))


@app.route('/delete_all_expenses', methods=['POST'])
@login_required
def delete_all_expenses():
    """
    Deletes all expense ledger records from the database cleanly 
    and returns a structured JSON confirmation back to the frontend.
    """
    try:
        # 1. Clear out all rows inside your Expense database table
        Expense.query.delete()
        db.session.commit()

        # 2. Return a valid JSON dictionary payload to satisfy the Flask compiler
        return jsonify({
            'success': True,
            'message': 'All expenses have been successfully deleted from the system.'
        }), 200

    except Exception as e:
        db.session.rollback()
        logger.error("Wipe operations failure tracking exception: %s", str(e))
        return jsonify({
            'success': False,
            'message': f'Failed to wipe expenses data ledger: {str(e)}'
        }), 500

# =============================================================================
# STAFF MANAGEMENT & SHIFT TRACKING ROUTES
# -----------------------------------------------------------------------------
# WHERE TO ADD IN app.py:
#   1. Line 294-298 — add new models to the import:
#      from models import (... Shift, LoyaltyTransaction, ClientCreditPayment,
#                          PurchaseOrder, PurchaseOrderItem, Return, ReturnItem,
#                          EODReport)
#
#   2. Line 395-401 — update inject_globals() to expose role helpers:
#      Replace the existing inject_globals with the one at the bottom of
#      this file.
#
#   3. Paste all routes below anywhere after the dashboard() route.
#
# MIGRATION — run after updating models.py:
#   flask db migrate -m "staff shift tracking"
#   flask db upgrade
# =============================================================================


# ─────────────────────────────────────────────────────────────────────────────
# UPDATED inject_globals  —  replace the existing one at line 386
# Exposes permission helpers to every template so you never write
# current_user.role.lower() == 'admin' in Jinja again.
# ─────────────────────────────────────────────────────────────────────────────
@app.context_processor
def inject_globals():
    from flask_login import current_user
    is_auth = current_user.is_authenticated
    return {
        'current_year':        datetime.now().year,
        'is_admin':            is_auth and current_user.role.lower() == 'admin',
        'is_manager':          is_auth and current_user.role.lower() in ('admin', 'manager'),
        'is_stock_manager':    is_auth and current_user.role.lower() in ('admin', 'manager', 'stock_manager'),
        'can_view_reports':    is_auth and current_user.role.lower() in ('admin', 'manager'),
        'can_manage_users':    is_auth and current_user.role.lower() == 'admin',
        'can_give_discount':   is_auth and current_user.role.lower() in ('admin', 'manager'),
        'can_process_refund':  is_auth and current_user.role.lower() in ('admin', 'manager'),
        'can_view_profit':     is_auth and current_user.role.lower() in ('admin', 'manager'),
        'open_shift':          Shift.query.filter_by(
                                   user_id=current_user.id if is_auth else 0,
                                   status='Open'
                               ).first() if is_auth else None,
    }


# =============================================================================
#  STAFF LIST + ADD
# =============================================================================
from datetime import date
from flask import render_template, request, redirect, url_for, flash
from flask_login import current_user, login_required

@app.route('/staff', methods=['GET', 'POST'])
@login_required
def staff():
    # 1. Enforce strict Admin-only validation
    if current_user.role.lower() != 'admin':
        flash('Access Denied — Admin only.', 'danger')
        return redirect(url_for('dashboard'))

    # 2. Handle POST operations (Forms / Modals submissions)
    if request.method == 'POST':
        action = request.form.get('action', 'add')

        # ACTION A: Creating a Brand New User
        if action == 'add':
            username      = request.form.get('username', '').strip()
            full_name     = request.form.get('full_name', '').strip()
            phone         = request.form.get('phone', '').strip()
            id_number     = request.form.get('id_number', '').strip()
            role          = request.form.get('role', 'cashier').strip()
            salary_str    = request.form.get('salary', '0').strip()
            hire_date_str = request.form.get('hire_date', '').strip()
            password      = request.form.get('password', '').strip()

            if not username or not password:
                flash('Username and password are required.', 'danger')
                return redirect(url_for('staff'))

            if User.query.filter_by(username=username).first():
                flash(f'Username "{username}" is already taken.', 'warning')
                return redirect(url_for('staff'))

            if role not in ('admin', 'manager', 'stock_manager', 'cashier'):
                role = 'cashier'

            try:
                salary = float(salary_str) if salary_str else 0.0
            except ValueError:
                salary = 0.0

            hire_date = None
            if hire_date_str:
                try:
                    hire_date = date.fromisoformat(hire_date_str)
                except ValueError:
                    pass

            new_user = User(
                username=username, full_name=full_name, phone=phone,
                id_number=id_number, role=role, salary=salary,
                hire_date=hire_date, is_active=True
            )
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.commit()
            flash(f'Staff member "{full_name or username}" added successfully.', 'success')

        # ACTION B: Updating Permissions/Profile from Modals
        elif action == 'edit':
            user_id = request.form.get('user_id')
            user_to_edit = User.query.get(user_id)
            
            if user_to_edit:
                user_to_edit.full_name = request.form.get('full_name', '').strip()
                user_to_edit.id_number = request.form.get('id_number', '').strip()
                user_to_edit.phone = request.form.get('phone', '').strip()
                user_to_edit.role = request.form.get('role', 'cashier')
                user_to_edit.is_active = request.form.get('is_active') == '1'
                
                try:
                    user_to_edit.salary = float(request.form.get('salary', 0.0))
                    user_to_edit.discount_limit_percentage = float(request.form.get('discount_limit_percentage', 0.0))
                except ValueError:
                    pass

                # HTML checkboxes don't submit keys if unchecked; test for presence
                user_to_edit.perm_process_sales = 'perm_process_sales' in request.form
                user_to_edit.perm_view_reports = 'perm_view_reports' in request.form
                user_to_edit.perm_give_discount = 'perm_give_discount' in request.form
                user_to_edit.perm_process_refund = 'perm_process_refund' in request.form
                user_to_edit.perm_manage_products = 'perm_manage_products' in request.form
                user_to_edit.perm_manage_suppliers = 'perm_manage_suppliers' in request.form
                user_to_edit.perm_manage_users = 'perm_manage_users' in request.form
                user_to_edit.perm_view_profit = 'perm_view_profit' in request.form

                db.session.commit()
                flash(f'Configuration saved successfully for {user_to_edit.username}.', 'success')
            else:
                flash('User profile modification target not found.', 'danger')

        return redirect(url_for('staff'))

    # 3. GET Request Processing: Assemble dashboard data
    all_staff = User.query.order_by(User.is_active.desc(), User.full_name).all()

    shift_stats = {}
    for u in all_staff:
        shifts_done = Shift.query.filter_by(user_id=u.id, status='Closed').count()
        last_shift  = Shift.query.filter_by(user_id=u.id).order_by(Shift.opened_at.desc()).first()
        open_shift  = Shift.query.filter_by(user_id=u.id, status='Open').first()
        shift_stats[u.id] = {
            'shifts_done': shifts_done,
            'last_shift':  last_shift.opened_at if last_shift else None,
            'open_shift':  open_shift,
        }

    # Pass all_staff as 'users' explicitly to fuel the template loops correctly
    return render_template(
        'staff_control.html',
        users=all_staff,
        shift_stats=shift_stats,
        today=date.today()
    )

# =============================================================================
#  EDIT STAFF
# =============================================================================
@app.route('/staff/<int:user_id>/edit', methods=['POST'])
@login_required
def edit_staff(user_id):
    if current_user.role.lower() != 'admin':
        flash('Access Denied.', 'danger')
        return redirect(url_for('staff'))

    u = User.query.get_or_404(user_id)

    # Prevent demoting the last admin
    if u.role.lower() == 'admin' and request.form.get('role', '').lower() != 'admin':
        admin_count = User.query.filter(
            func.lower(User.role) == 'admin', User.is_active == True
        ).count()
        if admin_count <= 1:
            flash('Cannot change role — this is the only active admin account.', 'danger')
            return redirect(url_for('staff'))

    full_name = request.form.get('full_name', '').strip()
    phone     = request.form.get('phone', '').strip()
    id_number = request.form.get('id_number', '').strip()
    role      = request.form.get('role', u.role).strip()
    salary    = float(request.form.get('salary', u.salary or 0) or 0)
    new_pw    = request.form.get('new_password', '').strip()

    if role not in ('admin', 'manager', 'stock_manager', 'cashier'):
        role = u.role

    u.full_name = full_name
    u.phone     = phone
    u.id_number = id_number
    u.role      = role
    u.salary    = salary
    if new_pw:
        u.set_password(new_pw)

    db.session.commit()
    flash(f'"{u.full_name or u.username}" updated.', 'success')
    return redirect(url_for('staff'))


# =============================================================================
#  TOGGLE ACTIVE / INACTIVE
# =============================================================================
@app.route('/staff/<int:user_id>/toggle', methods=['POST'])
@login_required
def toggle_staff(user_id):
    if current_user.role.lower() != 'admin':
        flash('Access Denied.', 'danger')
        return redirect(url_for('staff'))
    if user_id == current_user.id:
        flash('You cannot deactivate your own account.', 'warning')
        return redirect(url_for('staff'))

    u = User.query.get_or_404(user_id)

    # Prevent deactivating last admin
    if u.is_active and u.role.lower() == 'admin':
        admin_count = User.query.filter(
            func.lower(User.role) == 'admin', User.is_active == True
        ).count()
        if admin_count <= 1:
            flash('Cannot deactivate the only active admin account.', 'danger')
            return redirect(url_for('staff'))

    u.is_active = not u.is_active
    db.session.commit()
    status = 'activated' if u.is_active else 'deactivated'
    flash(f'"{u.username}" has been {status}.', 'info')
    return redirect(url_for('staff'))


# =============================================================================
#  OPEN SHIFT
# =============================================================================
@app.route('/shifts/open', methods=['POST'])
@login_required
def open_shift():
    # Only one open shift per user at a time
    existing = Shift.query.filter_by(user_id=current_user.id, status='Open').first()
    if existing:
        flash('You already have an open shift. Close it before opening a new one.', 'warning')
        return redirect(url_for('shifts'))

    try:
        opening_float = float(request.form.get('opening_float', 0) or 0)
    except ValueError:
        opening_float = 0.0

    s = Shift(
        user_id=current_user.id,
        opened_by_id=current_user.id,
        opening_float=opening_float,
        status='Open'
    )
    db.session.add(s)
    db.session.commit()

    logger.info('Shift %s opened by user %s with float KES %.2f',
                s.id, current_user.username, opening_float)
    flash(f'Shift opened. Opening float: KES {opening_float:,.2f}', 'success')
    return redirect(url_for('shifts'))


# =============================================================================
#  CLOSE SHIFT
# =============================================================================
@app.route('/shifts/<int:shift_id>/close', methods=['POST'])
@login_required
def close_shift(shift_id):
    s = Shift.query.get_or_404(shift_id)

    if s.user_id != current_user.id and current_user.role.lower() not in ('admin', 'manager'):
        flash('Access Denied.', 'danger')
        return redirect(url_for('shifts'))

    if s.status == 'Closed':
        flash('This shift is already closed.', 'warning')
        return redirect(url_for('shift_detail', shift_id=shift_id))

    try:
        actual_cash = float(request.form.get('actual_cash', 0) or 0)
    except ValueError:
        actual_cash = 0.0
    closing_notes = request.form.get('closing_notes', '').strip()

    # ── Compute totals from all payments made during this shift window ────────
    shift_payments = Payment.query.join(Receipt).filter(
        Receipt.timestamp >= s.opened_at
    ).all()

    total_cash   = 0.0
    total_mpesa  = 0.0
    txn_set      = set()
    total_disc   = 0.0
    total_rev    = 0.0

    for p in shift_payments:
        if p.status == 'Success' and not p.is_reversal:
            if p.method == 'Cash':
                total_cash  += p.amount
            elif p.method == 'Mpesa':
                total_mpesa += p.amount
            if p.receipt_id:
                txn_set.add(p.receipt_id)

    # Compute revenue and discounts from receipts in window
    receipts_in_shift = Receipt.query.filter(
        Receipt.timestamp >= s.opened_at
    ).all()
    for r in receipts_in_shift:
        total_rev  += r.total_amount or 0
        total_disc += r.discount or 0

    expected_cash = round((s.opening_float or 0) + total_cash, 2)

    s.closed_at         = datetime.utcnow()
    s.status            = 'Closed'
    s.actual_cash       = round(actual_cash, 2)
    s.expected_cash     = expected_cash
    s.cash_variance     = round(actual_cash - expected_cash, 2)
    s.closing_notes     = closing_notes
    s.total_sales       = round(total_rev, 2)
    s.total_cash        = round(total_cash, 2)
    s.total_mpesa       = round(total_mpesa, 2)
    s.total_discounts   = round(total_disc, 2)
    s.transaction_count = len(txn_set)

    db.session.commit()

    variance_msg = ''
    if s.cash_variance > 0:
        variance_msg = f' Cash surplus: KES {s.cash_variance:,.2f}.'
    elif s.cash_variance < 0:
        variance_msg = f' Cash shortage: KES {abs(s.cash_variance):,.2f}.'

    logger.info('Shift %s closed by %s. Revenue=%.2f  Variance=%.2f',
                s.id, current_user.username, s.total_sales, s.cash_variance)
    flash(f'Shift closed. Total revenue: KES {s.total_sales:,.2f}.{variance_msg}', 'success')
    return redirect(url_for('shift_detail', shift_id=shift_id))


# =============================================================================
#  SHIFTS LIST
# =============================================================================
@app.route('/shifts')
@login_required
def shifts():
    # Admins/managers see all shifts; cashiers see only their own
    if current_user.role.lower() in ('admin', 'manager'):
        all_shifts = Shift.query.order_by(Shift.opened_at.desc()).limit(200).all()
    else:
        all_shifts = Shift.query.filter_by(user_id=current_user.id)\
                        .order_by(Shift.opened_at.desc()).limit(30).all()

    my_open_shift = Shift.query.filter_by(
        user_id=current_user.id, status='Open'
    ).first()

    # Summary stats for admin view
    total_shifts   = len(all_shifts)
    total_revenue  = round(sum(s.total_sales or 0 for s in all_shifts if s.status == 'Closed'), 2)
    total_variance = round(sum(s.cash_variance or 0 for s in all_shifts if s.status == 'Closed'), 2)

    return render_template('shifts.html',
                           all_shifts=all_shifts,
                           my_open_shift=my_open_shift,
                           total_shifts=total_shifts,
                           total_revenue=total_revenue,
                           total_variance=total_variance,
                           today=date.today())


# =============================================================================
#  SHIFT DETAIL
# =============================================================================
@app.route('/shifts/<int:shift_id>')
@login_required
def shift_detail(shift_id):
    s = Shift.query.get_or_404(shift_id)

    if s.user_id != current_user.id and \
       current_user.role.lower() not in ('admin', 'manager'):
        flash('Access Denied.', 'danger')
        return redirect(url_for('shifts'))

    # Receipts processed during this shift window
    receipts = Receipt.query.filter(
        Receipt.timestamp >= s.opened_at,
        Receipt.timestamp <= (s.closed_at or datetime.utcnow())
    ).order_by(Receipt.timestamp.desc()).all()

    return render_template('shift_detail.html',
                           shift=s,
                           receipts=receipts)
    return redirect(url_for('new_expense_reports')) 




@app.route('/mpesa_dashboard')
@login_required
def mpesa_dashboard():
    if current_user.role.lower() != 'admin': # Added .lower()
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

    # 3. Pass the total to the template.
    # NOTE: The variable is named base_grand_total to match the Jinja namespace
    # object of the same name computed inside the cart.html for-loop. Both must
    # agree so the initial hidden field value and JS seed value are in sync.
    return render_template(
        'cart.html',
        cart_items=cart_items,
        clients=clients,
        base_grand_total=total
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
            flash('No file selected.', 'danger')
            return redirect(request.url)

        products_added   = 0
        products_skipped = 0
        filename = secure_filename(file.filename).lower()

        try:
            # ─────────────────────────────────────────────────────────────
            # CSV HANDLER  (unchanged — was working fine)
            # ─────────────────────────────────────────────────────────────
            if filename.endswith('.csv'):
                raw_data = file.stream.read().decode("utf-8-sig", errors="ignore")
                stream   = io.StringIO(raw_data)
                reader   = csv.DictReader(stream)

                if not reader.fieldnames:
                    flash("CSV has no valid headers.", "danger")
                    return redirect(request.url)

                for row_idx, row in enumerate(reader, start=1):
                    try:
                        data = {}
                        for k, v in row.items():
                            if k is not None:
                                data[str(k).lower().strip()] = (
                                    str(v).replace('\n', ' ').strip() if v is not None else ''
                                )

                        name = data.get('name') or data.get('product') or data.get('item')
                        if not name or not str(name).strip():
                            products_skipped += 1
                            continue

                        b_price = clean_numeric(data.get('buying_price') or data.get('price'))
                        s_price = clean_numeric(data.get('selling_price'))
                        if s_price <= 0:
                            s_price = round(b_price * 1.25, 2)

                        qty_raw = data.get('quantity')
                        qty = int(float(clean_numeric(qty_raw))) if qty_raw else 0

                        expiry_date = None
                        expiry_raw  = data.get('expiry_date') or data.get('expiry')
                        if expiry_raw and str(expiry_raw).strip() and str(expiry_raw).lower() != 'none':
                            for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%Y/%m/%d'):
                                try:
                                    expiry_date = datetime.strptime(expiry_raw.strip(), fmt).date()
                                    break
                                except Exception:
                                    continue

                        barcode_val = data.get('barcode', '').strip() or None

                        # Skip duplicate barcode
                        if barcode_val and Product.query.filter_by(barcode=barcode_val).first():
                            logger.warning("CSV row %s skipped — duplicate barcode: %s", row_idx, barcode_val)
                            products_skipped += 1
                            continue

                        # Skip duplicate name
                        if Product.query.filter_by(name=name.strip()).first():
                            logger.warning("CSV row %s skipped — duplicate name: %s", row_idx, name.strip())
                            products_skipped += 1
                            continue

                        new_product = Product(
                            name=name.strip(),
                            category=data.get('category', 'General').strip() or 'General',
                            quantity=qty,
                            barcode=barcode_val,
                            buying_price=round(b_price, 2),
                            selling_price=round(s_price, 2),
                            expiry_date=expiry_date
                        )
                        db.session.add(new_product)
                        products_added += 1

                    except Exception as row_err:
                        # DO NOT rollback here — only skip the bad row
                        logger.warning("CSV import — skipping row %s: %s", row_idx, row_err)
                        products_skipped += 1
                        continue

                db.session.commit()

            # ─────────────────────────────────────────────────────────────
            # PDF HANDLER  (fully rewritten — robust to missing cols/rows)
            # ─────────────────────────────────────────────────────────────
            elif filename.endswith('.pdf'):
                import pdfplumber

                file.stream.seek(0)
                extracted_rows = []   # collects raw row lists from all pages

                with pdfplumber.open(file.stream) as pdf:
                    for page_num, page in enumerate(pdf.pages, start=1):
                        try:
                            # ── Strategy A: visual table extraction ──────
                            page_rows = []
                            for settings in [
                                # Try tight settings first, then looser
                                {"vertical_strategy": "lines",
                                 "horizontal_strategy": "lines"},
                                {"vertical_strategy": "text",
                                 "horizontal_strategy": "text",
                                 "intersection_x_tolerance": 15},
                            ]:
                                table = page.extract_table(table_settings=settings)
                                if table and len(table) >= 1:
                                    for row in table:
                                        if row and any(cell for cell in row if cell):
                                            cleaned = [
                                                str(c).replace('\n', ' ').strip() if c else ''
                                                for c in row
                                            ]
                                            page_rows.append(cleaned)
                                    break   # stop trying settings once we get rows

                            if page_rows:
                                extracted_rows.extend(page_rows)

                            # ── Strategy B: raw text parsing ─────────────
                            # Always try text parsing too — catches pages where
                            # table extraction returns nothing or partial data.
                            text_content = page.extract_text()
                            if text_content and not page_rows:
                                for line in text_content.split('\n'):
                                    line = line.strip()
                                    if not line:
                                        continue
                                    # Parse comma-separated or tab-separated lines
                                    if '\t' in line:
                                        parts = [p.strip() for p in line.split('\t')]
                                        if len(parts) >= 2 and parts[0]:
                                            extracted_rows.append(parts)
                                    elif ',' in line:
                                        f_stream   = io.StringIO(line)
                                        csv_parser = csv.reader(f_stream)
                                        for parsed_row in csv_parser:
                                            if parsed_row and len(parsed_row) >= 2:
                                                extracted_rows.append(
                                                    [str(c).strip() for c in parsed_row]
                                                )

                        except Exception as page_err:
                            logger.warning("PDF page %s parsing failed: %s", page_num, page_err)
                            continue

                if not extracted_rows:
                    flash("No data could be extracted from this PDF. "
                          "Make sure it contains a product table or structured list.", "danger")
                    return redirect(request.url)

                # ── Detect header row ─────────────────────────────────────
                # A header row contains keyword cells like 'name', 'price', etc.
                # If none found, assume first row is data and use default column order.
                HEADER_KEYWORDS = {'name', 'product', 'item', 'price', 'buying',
                                   'selling', 'qty', 'quantity', 'category',
                                   'barcode', 'expiry', 'sku'}

                def row_looks_like_header(row):
                    return any(
                        any(kw in str(cell).lower() for kw in HEADER_KEYWORDS)
                        for cell in row
                    )

                def row_looks_like_data(row):
                    """
                    A data row has at least one cell that looks like a product name
                    (non-empty, not purely a number) and at least one numeric cell.
                    """
                    has_name    = any(c and not c.replace('.','').replace(',','').isdigit()
                                      for c in row)
                    has_numeric = any(c and c.replace('.','').replace(',','').isdigit()
                                      for c in row)
                    return has_name and has_numeric

                # Separate header from data rows
                header_row = None
                data_rows  = []

                for i, row in enumerate(extracted_rows):
                    if header_row is None and row_looks_like_header(row):
                        header_row = row
                    elif row_looks_like_data(row):
                        data_rows.append(row)

                # If no header was ever found, use a safe default column map
                if header_row is None:
                    header_row = ['name', 'buying_price', 'selling_price',
                                  'quantity', 'category', 'barcode', 'expiry_date']

                # Normalise header names to match our expected keys
                def normalise_header(h):
                    h = str(h).lower().strip()
                    h = h.replace('buying price', 'buying_price')
                    h = h.replace('selling price', 'selling_price')
                    h = h.replace('unit cost', 'buying_price')
                    h = h.replace('unit price', 'selling_price')
                    h = h.replace('sale price', 'selling_price')
                    h = h.replace('retail price', 'selling_price')
                    h = h.replace('qty', 'quantity')
                    h = h.replace('stock', 'quantity')
                    h = h.replace('exp', 'expiry_date')
                    h = h.replace('expiry', 'expiry_date')
                    h = h.replace('expire', 'expiry_date')
                    h = h.replace('cat', 'category')
                    h = h.replace('product name', 'name')
                    h = h.replace('item name', 'name')
                    h = h.replace('item', 'name')
                    h = h.replace('product', 'name')
                    return h

                headers = [normalise_header(h) for h in header_row]

                if not data_rows:
                    flash("Headers were detected but no product data rows found in the PDF. "
                          f"({len(extracted_rows)} total rows extracted, 0 matched data pattern.)",
                          "warning")
                    return redirect(request.url)

                # ── Process each data row ─────────────────────────────────
                for row_idx, row_data in enumerate(data_rows, start=1):
                    try:
                        # Pad short rows / trim long rows to match header count
                        if len(row_data) < len(headers):
                            row_data = row_data + [''] * (len(headers) - len(row_data))
                        elif len(row_data) > len(headers):
                            row_data = row_data[:len(headers)]

                        row = dict(zip(headers, row_data))

                        # ── Product name (required) ───────────────────────
                        name = (row.get('name') or
                                row.get('product') or
                                row.get('item') or
                                (row_data[0] if row_data else ''))
                        name = str(name).strip()

                        # Skip header-like rows that slipped through
                        if not name or name.lower() in ('name', 'product', 'item', 'none', ''):
                            products_skipped += 1
                            continue

                        # ── Prices ────────────────────────────────────────
                        b_raw = (row.get('buying_price') or
                                 row.get('price') or
                                 (row_data[1] if len(row_data) > 1 else '0'))
                        s_raw = (row.get('selling_price') or
                                 (row_data[2] if len(row_data) > 2 else '0'))

                        # Strip markup annotations like "auto-25%" or "markup"
                        if any(kw in str(s_raw).lower()
                               for kw in ('auto', 'markup', '%', 'calc')):
                            s_raw = '0'

                        b_price = clean_numeric(b_raw)
                        s_price = clean_numeric(s_raw)

                        # Default selling price to 25% markup if missing/zero
                        if s_price <= 0:
                            s_price = round(b_price * 1.25, 2)

                        # Default buying price to selling price if missing
                        if b_price <= 0 and s_price > 0:
                            b_price = s_price

                        # ── Quantity (optional — defaults to 0) ───────────
                        qty_raw = (row.get('quantity') or
                                   (row_data[3] if len(row_data) > 3 else '0'))
                        try:
                            qty = int(float(clean_numeric(qty_raw))) if qty_raw else 0
                        except (ValueError, TypeError):
                            qty = 0

                        # ── Category (optional — defaults to General) ─────
                        cat_raw  = (row.get('category') or
                                    (row_data[4] if len(row_data) > 4 else ''))
                        category = str(cat_raw).strip() if cat_raw else ''
                        if not category or category.lower() in ('none', 'n/a', ''):
                            category = 'General'

                        # ── Barcode (optional) ────────────────────────────
                        barcode_raw = (row.get('barcode') or
                                       (row_data[5] if len(row_data) > 5 else ''))
                        barcode = str(barcode_raw).strip() or None

                        # ── Expiry date (optional) ────────────────────────
                        expiry_date = None
                        expiry_raw  = (row.get('expiry_date') or
                                       row.get('expiry') or
                                       (row_data[6] if len(row_data) > 6 else ''))
                        if expiry_raw and str(expiry_raw).strip().lower() not in ('none', 'n/a', ''):
                            for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y',
                                        '%Y/%m/%d', '%d-%m-%Y', '%d %b %Y'):
                                try:
                                    expiry_date = datetime.strptime(
                                        str(expiry_raw).strip(), fmt
                                    ).date()
                                    break
                                except Exception:
                                    continue

                        # Skip duplicate barcode
                        if barcode and Product.query.filter_by(barcode=barcode).first():
                            logger.warning("PDF row %s skipped — duplicate barcode: %s", row_idx, barcode)
                            products_skipped += 1
                            continue

                        # Skip duplicate name
                        if Product.query.filter_by(name=name).first():
                            logger.warning("PDF row %s skipped — duplicate name: %s", row_idx, name)
                            products_skipped += 1
                            continue

                        new_product = Product(
                            name=name,
                            category=category,
                            quantity=qty,
                            barcode=barcode,
                            buying_price=round(b_price, 2),
                            selling_price=round(s_price, 2),
                            expiry_date=expiry_date
                        )
                        db.session.add(new_product)
                        products_added += 1

                    except Exception as row_err:
                        # DO NOT rollback — only skip this one row
                        logger.warning("PDF row %s skipped: %s", row_idx, row_err)
                        products_skipped += 1
                        continue

                db.session.commit()

            else:
                flash("Unsupported file type. Please upload a CSV or PDF.", "danger")
                return redirect(request.url)

            # ─────────────────────────────────────────────────────────────
            # RESULT FLASH
            # ─────────────────────────────────────────────────────────────
            if products_added > 0:
                msg = f"Import complete! {products_added} product(s) added."
                if products_skipped > 0:
                    msg += f" {products_skipped} row(s) skipped (invalid or unreadable)."
                flash(msg, 'success')
            else:
                flash(
                    f"Import finished but no products were added. "
                    f"{products_skipped} row(s) were skipped. "
                    f"Check that the PDF has a readable product table with at least "
                    f"a name column and one price column.",
                    'warning'
                )

        except Exception as e:
            db.session.rollback()
            logger.error("Critical import crash: %s", e)
            flash(f"Import failed: {str(e)}", "danger")

        return redirect(url_for('products'))

    return render_template('upload_products.html')

@app.route('/remove_from_cart/<int:item_id>', methods=['GET', 'POST'])
@login_required
def remove_from_cart(item_id):
    # This debug line will show up in your VS Code terminal
    from flask import request
    logger.debug("remove_from_cart method: %s", request.method)

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
    if current_user.role.lower() != 'admin':
        return redirect(url_for('index'))

    # Fetch sales & products
    sales = Sale.query.order_by(Sale.timestamp.desc()).all()
    products = Product.query.all()

    # --- Dashboard metrics ---
    # 1. Total sales (assuming Sale has 'amount' field)
    total_sales = sum(s.total_amount or 0 for s in sales)

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
        .order_by(func.sum(Sale.quantity_sold).desc())
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
    return redirect(url_for('cart'))


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

# =============================================================================
# FEATURE 3 — CUSTOMER LOYALTY & CREDIT ACCOUNTS
# =============================================================================
# WHERE TO ADD IN app.py:
#
#  1. Add to imports (line 294):
#     from models import (... LoyaltyTransaction, ClientCreditPayment)
#
#  2. Replace the existing tiny /clients route (lines 1970-1981) with
#     the full clients() route below.
#
#  3. Paste all remaining routes anywhere after the clients() route.
#
#  4. In your checkout() route (line 2483, after CartItem.query.delete()
#     and BEFORE db.session.commit()), paste the LOYALTY + CREDIT HOOK
#     block shown at the bottom of this file.
#
# LOYALTY RULES (adjust to taste):
#   POINTS_PER_KES  = 1 point per KES 10 spent
#   POINTS_REDEEM   = 1 point = KES 0.50 off
#   MIN_REDEMPTION  = 100 points minimum to redeem
#
# MIGRATION:
#   flask db migrate -m "loyalty credit accounts"
#   flask db upgrade
# =============================================================================

POINTS_PER_KES   = 0.1    # points earned per KES spent  (1 pt per KES 10)
POINTS_REDEEM_KES = 0.50  # KES value of 1 point when redeeming
MIN_REDEMPTION   = 100    # minimum points needed to redeem


# =============================================================================
#  CLIENTS LIST + ADD  (replaces the old 3-line route)
# =============================================================================
@app.route('/clients', methods=['GET', 'POST'])
@login_required
def clients():
    if request.method == 'POST':
        action = request.form.get('action', 'add')

        if action == 'add':
            name          = request.form.get('name', '').strip()
            phone         = request.form.get('phone', '').strip()
            email         = request.form.get('email', '').strip()
            credit_limit  = float(request.form.get('credit_limit', 0) or 0)
            credit_enabled= request.form.get('credit_enabled') == '1'

            if not name:
                flash('Client name is required.', 'danger')
                return redirect(url_for('clients'))

            if phone and Client.query.filter_by(phone=phone).first():
                flash(f'A client with phone {phone} already exists.', 'warning')
                return redirect(url_for('clients'))

            c = Client(
                name=name, phone=phone, email=email,
                is_walk_in=False,
                credit_limit=credit_limit,
                credit_enabled=credit_enabled,
                credit_balance=0.0,
                loyalty_points=0,
                total_spent=0.0
            )
            db.session.add(c)
            db.session.commit()
            flash(f'Client "{name}" added successfully.', 'success')

        return redirect(url_for('clients'))

    # ── GET ───────────────────────────────────────────────────────────────────
    search = request.args.get('search', '').strip()
    query  = Client.query.filter_by(is_walk_in=False)
    if search:
        query = query.filter(
            db.or_(
                Client.name.ilike(f'%{search}%'),
                Client.phone.ilike(f'%{search}%'),
                Client.email.ilike(f'%{search}%')
            )
        )
    all_clients = query.order_by(Client.name).all()

    # Summary stats
    total_clients      = Client.query.filter_by(is_walk_in=False).count()
    credit_clients     = Client.query.filter_by(is_walk_in=False, credit_enabled=True).count()
    total_credit_owed  = db.session.query(
        func.sum(Client.credit_balance)
    ).filter(Client.is_walk_in==False, Client.credit_balance > 0).scalar() or 0.0
    total_points       = db.session.query(
        func.sum(Client.loyalty_points)
    ).filter(Client.is_walk_in==False).scalar() or 0

    return render_template(
        'clients.html',
        clients=all_clients,
        search=search,
        total_clients=total_clients,
        credit_clients=credit_clients,
        total_credit_owed=round(total_credit_owed, 2),
        total_points=total_points,
    )


# =============================================================================
#  CLIENT DETAIL — full history, loyalty, credit
# =============================================================================
@app.route('/clients/<int:client_id>')
@login_required
def client_detail(client_id):
    c = Client.query.get_or_404(client_id)

    # Purchase history
    receipts = Receipt.query.filter_by(client_id=client_id)\
                   .order_by(Receipt.timestamp.desc()).limit(50).all()

    # Loyalty history
    loyalty_txns = LoyaltyTransaction.query.filter_by(client_id=client_id)\
                       .order_by(LoyaltyTransaction.created_at.desc()).limit(30).all()

    # Credit payment history
    credit_payments = ClientCreditPayment.query.filter_by(client_id=client_id)\
                          .order_by(ClientCreditPayment.created_at.desc()).limit(30).all()

    # Summary figures
    total_purchases   = len(receipts)
    total_revenue     = round(sum(r.total_amount or 0 for r in receipts), 2)
    points_earned     = sum(t.points for t in loyalty_txns if t.points > 0)
    points_redeemed   = abs(sum(t.points for t in loyalty_txns if t.points < 0))

    return render_template(
        'client_detail.html',
        client=c,
        receipts=receipts,
        loyalty_txns=loyalty_txns,
        credit_payments=credit_payments,
        total_purchases=total_purchases,
        total_revenue=total_revenue,
        points_earned=points_earned,
        points_redeemed=points_redeemed,
        points_value=round(c.loyalty_points * POINTS_REDEEM_KES, 2),
        min_redemption=MIN_REDEMPTION,
    )


# =============================================================================
#  EDIT CLIENT
# =============================================================================
@app.route('/clients/<int:client_id>/edit', methods=['POST'])
@login_required
def edit_client(client_id):
    if current_user.role.lower() not in ('admin', 'manager'):
        flash('Access Denied.', 'danger')
        return redirect(url_for('clients'))

    c = Client.query.get_or_404(client_id)

    c.name           = request.form.get('name', c.name).strip()
    c.phone          = request.form.get('phone', '').strip()
    c.email          = request.form.get('email', '').strip()
    c.credit_enabled = request.form.get('credit_enabled') == '1'

    if c.credit_enabled:
        try:
            c.credit_limit = float(request.form.get('credit_limit', c.credit_limit) or 0)
        except ValueError:
            pass

    db.session.commit()
    flash(f'Client "{c.name}" updated.', 'success')
    return redirect(url_for('client_detail', client_id=c.id))


# =============================================================================
#  RECORD CREDIT PAYMENT (client paying off their debt)
# =============================================================================
@app.route('/clients/<int:client_id>/pay-credit', methods=['POST'])
@login_required
def client_pay_credit(client_id):
    if current_user.role.lower() not in ('admin', 'manager'):
        flash('Access Denied.', 'danger')
        return redirect(url_for('client_detail', client_id=client_id))

    c = Client.query.get_or_404(client_id)

    try:
        amount = float(request.form.get('amount', 0) or 0)
    except ValueError:
        amount = 0.0

    if amount <= 0:
        flash('Payment amount must be greater than zero.', 'danger')
        return redirect(url_for('client_detail', client_id=client_id))

    # Clamp — can't pay more than what is owed
    amount = round(min(amount, c.credit_balance or 0), 2)
    if amount <= 0:
        flash('This client has no outstanding credit balance.', 'info')
        return redirect(url_for('client_detail', client_id=client_id))

    method    = request.form.get('method', 'Cash')
    reference = request.form.get('reference', '').strip()

    cp = ClientCreditPayment(
        client_id=client_id,
        amount=amount,
        method=method,
        reference=reference,
        recorded_by=current_user.id
    )
    db.session.add(cp)

    c.credit_balance = round((c.credit_balance or 0) - amount, 2)
    db.session.commit()

    flash(f'Credit payment of KES {amount:,.2f} recorded for {c.name}.', 'success')
    return redirect(url_for('client_detail', client_id=client_id))


# =============================================================================
#  MANUAL LOYALTY POINTS ADJUSTMENT (admin — gifts, corrections)
# =============================================================================
@app.route('/clients/<int:client_id>/adjust-points', methods=['POST'])
@login_required
def adjust_loyalty_points(client_id):
    if current_user.role.lower() not in ('admin', 'manager'):
        flash('Access Denied.', 'danger')
        return redirect(url_for('client_detail', client_id=client_id))

    c = Client.query.get_or_404(client_id)

    try:
        points = int(request.form.get('points', 0))
    except ValueError:
        points = 0

    reason = request.form.get('reason', 'Manual adjustment').strip()

    if points == 0:
        flash('Points adjustment cannot be zero.', 'warning')
        return redirect(url_for('client_detail', client_id=client_id))

    # Prevent negative balance
    if points < 0 and (c.loyalty_points or 0) + points < 0:
        flash(f'Cannot remove more points than {c.name} has ({c.loyalty_points}).', 'danger')
        return redirect(url_for('client_detail', client_id=client_id))

    txn = LoyaltyTransaction(
        client_id=client_id,
        points=points,
        reason=reason
    )
    db.session.add(txn)

    c.loyalty_points = (c.loyalty_points or 0) + points
    db.session.commit()

    action = 'added' if points > 0 else 'deducted'
    flash(f'{abs(points)} points {action} for {c.name}.', 'success')
    return redirect(url_for('client_detail', client_id=client_id))


# =============================================================================
#  REDEEM POINTS AT CHECKOUT  (called via AJAX from cart page)
# =============================================================================
@app.route('/api/loyalty/redeem', methods=['POST'])
@login_required
def redeem_loyalty_points():
    """
    Returns the KES discount value for a given redemption.
    The cart JS calls this to show the discount before final checkout.
    Actual deduction happens in the checkout route hook below.
    """
    client_id = request.json.get('client_id')
    points    = int(request.json.get('points', 0))

    if not client_id or points < MIN_REDEMPTION:
        return jsonify({'success': False,
                        'message': f'Minimum {MIN_REDEMPTION} points required to redeem.'}), 400

    c = Client.query.get(client_id)
    if not c:
        return jsonify({'success': False, 'message': 'Client not found.'}), 404

    if (c.loyalty_points or 0) < points:
        return jsonify({'success': False,
                        'message': f'Client only has {c.loyalty_points} points.'}), 400

    discount_value = round(points * POINTS_REDEEM_KES, 2)
    return jsonify({
        'success':        True,
        'points':         points,
        'discount_value': discount_value,
        'remaining':      (c.loyalty_points or 0) - points,
    })


@app.route('/admin/users', methods=['GET'])
@login_required
def manage_users():
    """
    Dashboard view that lists all staff profiles along with their
    granular permission settings and discount thresholds.
    """
    # Enforce administrative access check
    if not current_user.can_manage_users:
        flash("Access Denied: You do not possess user management privileges.", "danger")
        abort(403)
        
    all_users = User.query.order_by(User.id.asc()).all()
    return render_template('manage_users.html', users=all_users)


@app.route('/admin/edit_user/<int:user_id>', methods=['POST'])
@login_required
def edit_user_permissions(user_id):
    """
    Saves the custom permission toggles and profile updates submitted
    from the management dashboard panel.
    """
    # Enforce administrative access check
    if not current_user.can_manage_users:
        flash("Access Denied: You do not possess user management privileges.", "danger")
        abort(403)
        
    user = User.query.get_or_404(user_id)
    
    # Safety Check: Prevent the logged-in administrator from accidentally 
    # revoking their own administrative or user-management permissions.
    if user.id == current_user.id:
        # Check if the incoming request tries to change important values
        if 'perm_manage_users' not in request.form or not int(request.form.get('is_active', 1)):
            flash("Security Restriction: You cannot deactivate yourself or revoke your own user management permissions.", "warning")
            return redirect(url_for('manage_users'))

    # 1. Update Core Metadata info
    user.full_name = request.form.get('full_name', user.full_name)
    user.phone = request.form.get('phone', user.phone)
    user.id_number = request.form.get('id_number', user.id_number)
    user.salary = float(request.form.get('salary') or 0.0)
    user.is_active = request.form.get('is_active') == '1'
    
    # Update Role Template label
    user.role = request.form.get('role', user.role)

    # 2. Process Granular Permission Toggles safely via HTML checkbox states
    user.perm_process_sales   = 'perm_process_sales' in request.form
    user.perm_view_reports    = 'perm_view_reports' in request.form
    user.perm_give_discount   = 'perm_give_discount' in request.form
    user.perm_process_refund  = 'perm_process_refund' in request.form
    user.perm_manage_products = 'perm_manage_products' in request.form
    user.perm_manage_suppliers= 'perm_manage_suppliers' in request.form
    user.perm_manage_users    = 'perm_manage_users' in request.form
    user.perm_view_profit     = 'perm_view_profit' in request.form
    
    # 3. Save Custom Discount Caps
    user.discount_limit_percentage = float(request.form.get('discount_limit_percentage' or 0.0))
    
    try:
        db.session.commit()
        flash(f"Permissions and profile for '{user.username}' updated successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error updating user permissions: {str(e)}", "danger")
        
    return redirect(url_for('manage_users'))

# =============================================================================
#  CLIENT SEARCH (AJAX — used by cart page dropdown)
# =============================================================================
@app.route('/api/clients/search')
@login_required
def client_search():
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])

    results = Client.query.filter(
        Client.is_walk_in == False,
        db.or_(
            Client.name.ilike(f'%{q}%'),
            Client.phone.ilike(f'%{q}%')
        )
    ).limit(8).all()

    return jsonify([{
        'id':             c.id,
        'name':           c.name,
        'phone':          c.phone or '',
        'loyalty_points': c.loyalty_points or 0,
        'credit_balance': round(c.credit_balance or 0, 2),
        'credit_enabled': c.credit_enabled,
        'available_credit': c.available_credit,
    } for c in results])



# =============================================================================
# SUPPLIER ROUTES
# Replace lines 1983-2178 in app.py with this entire block.
# delete_supplier moves to after edit_supplier so the route order is logical.
# The profit_report route (lines 2128-2167) sits between edit and delete in
# the original — it is NOT supplier code, so it is left in place. Remove it
# from this block when pasting; keep it where it already is in app.py.
# =============================================================================


# ── 1. SUPPLIERS LIST + ADD ────────────────────────────────────────────────────
@app.route('/suppliers', methods=['GET', 'POST'])
@login_required
def suppliers():
    if request.method == 'POST':
        if current_user.role.lower() != 'admin':
            flash('Access Denied.', 'danger')
            return redirect(url_for('suppliers'))

        name           = request.form.get('name', '').strip()
        phone          = request.form.get('phone', '').strip()
        email          = request.form.get('email', '').strip()
        address        = request.form.get('address', '').strip()
        items_supplied = request.form.get('items_supplied', '').strip()

        if not name:
            flash('Supplier name is required.', 'danger')
            return redirect(url_for('suppliers'))

        # Prevent duplicate supplier names
        if Supplier.query.filter_by(name=name).first():
            flash(f'A supplier named "{name}" already exists.', 'warning')
            return redirect(url_for('suppliers'))

        new_supplier = Supplier(
            name=name,
            phone=phone,
            email=email,
            address=address,
            items_supplied=items_supplied
        )
        db.session.add(new_supplier)
        db.session.commit()

        # NOTE: initial_amount removed — it was creating invoices with no line
        # items, never updating stock or buying prices. Use supplier_purchase.

        flash(f'Supplier "{name}" added successfully.', 'success')
        return redirect(url_for('suppliers'))

    # ── GET: single aggregated query instead of N+1 loop ───────────────────
    totals = dict(
        db.session.query(
            SupplierInvoice.supplier_id,
            func.sum(SupplierInvoice.total_amount)
        ).group_by(SupplierInvoice.supplier_id).all()
    )

    paid_totals = dict(
        db.session.query(
            SupplierInvoice.supplier_id,
            func.sum(SupplierInvoice.amount_paid)
        ).group_by(SupplierInvoice.supplier_id).all()
    )

    all_suppliers = Supplier.query.order_by(Supplier.created_at.desc()).all()
    for s in all_suppliers:
        s.total_value  = round(totals.get(s.id) or 0.0, 2)
        s.total_paid   = round(paid_totals.get(s.id) or 0.0, 2)
        s.balance_owed = round(s.total_value - s.total_paid, 2)

    products = Product.query.order_by(Product.name).all()  # for purchase modal

    return render_template(
        'suppliers.html',
        suppliers=all_suppliers,
        products=products,
        is_admin=(current_user.role.lower() == 'admin')
    )


# ── 2. RECORD A PURCHASE (stock in from supplier) ─────────────────────────────
@app.route('/supplier_purchase', methods=['POST'])
@login_required
def supplier_purchase():
    if current_user.role.lower() != 'admin':
        flash('Access Denied.', 'danger')
        return redirect(url_for('suppliers'))

    supplier_id = request.form.get('supplier_id', type=int)
    items_json  = request.form.get('items_json')
    notes       = request.form.get('notes', '').strip()

    try:
        items = json.loads(items_json or '[]')
    except Exception:
        flash('Invalid items format.', 'danger')
        return redirect(url_for('suppliers'))

    if not supplier_id or not items:
        flash('Missing supplier or items.', 'danger')
        return redirect(url_for('suppliers'))

    if not Supplier.query.get(supplier_id):
        flash('Supplier not found.', 'danger')
        return redirect(url_for('suppliers'))

    try:
        inv = SupplierInvoice(supplier_id=supplier_id, total_amount=0.0, notes=notes)
        db.session.add(inv)
        db.session.flush()  # get inv.id before the loop

        total = 0.0

        for it in items:
            # ── Validate each line before touching the DB ───────────────
            try:
                product_id = int(it['product_id'])
                qty        = int(it['quantity'])
                expected_cost  = float(it['expected_cost'])
            except (KeyError, ValueError, TypeError):
                db.session.rollback()
                flash('Invalid quantity or cost in one of the items.', 'danger')
                return redirect(url_for('suppliers'))

            if qty <= 0 or expected_cost <= 0:
                db.session.rollback()
                flash('Quantity and unit cost must be greater than zero.', 'danger')
                return redirect(url_for('suppliers'))

            product = Product.query.get(product_id)
            if not product:
                db.session.rollback()
                flash(f'Product ID {product_id} not found.', 'danger')
                return redirect(url_for('suppliers'))

            line_total = round(qty * expected_cost, 2)

            sii = SupplierInvoiceItem(
                supplier_invoice_id=inv.id,
                product_id=product.id,
                quantity=qty,
                expected_cost=expected_cost,
                line_total=line_total
            )
            db.session.add(sii)

            # ── Weighted average cost update ────────────────────────────
            # Keeps buying_price (used in profit reports) accurate after
            # every restock. Formula:
            #   new_avg = (old_stock_value + new_delivery_value)
            #             / (old_qty + new_qty)
            old_qty   = product.quantity or 0
            old_price = product.buying_price or 0.0
            new_total_value = (old_qty * old_price) + (qty * expected_cost)
            new_total_qty   = old_qty + qty
            product.buying_price = round(new_total_value / new_total_qty, 4)

            # ── Update physical stock ───────────────────────────────────
            product.quantity = new_total_qty

            # ── Audit ledger ────────────────────────────────────────────
            movement = StockMovement(
                product_id=product.id,
                quantity_change=qty,
                reason='purchase',
                reference_type='supplier_invoice',
                reference_id=inv.id
            )
            db.session.add(movement)

            total += line_total

        inv.total_amount = round(total, 2)
        db.session.commit()

        flash(f'Purchase of KES {inv.total_amount:,.2f} recorded and stock updated.', 'success')

    except Exception as e:
        db.session.rollback()
        logger.error('supplier_purchase error: %s', e)
        flash('An error occurred while recording the purchase. Please try again.', 'danger')

    return redirect(url_for('suppliers'))


# ── 3. SUPPLIER DETAIL + PURCHASE HISTORY (NEW) ───────────────────────────────
@app.route('/supplier/<int:id>')
@login_required
def supplier_detail(id):
    if current_user.role.lower() != 'admin':
        flash('Access Denied.', 'danger')
        return redirect(url_for('suppliers'))

    supplier = Supplier.query.get_or_404(id)

    invoices = (
        SupplierInvoice.query
        .filter_by(supplier_id=id)
        .order_by(SupplierInvoice.created_at.desc())
        .all()
    )

    total_spent   = round(sum(inv.total_amount or 0 for inv in invoices), 2)
    total_paid    = round(sum(inv.amount_paid  or 0 for inv in invoices), 2)
    total_owed    = round(total_spent - total_paid, 2)
    invoice_count = len(invoices)

    return render_template(
        'supplier_detail.html',
        supplier=supplier,
        invoices=invoices,
        total_spent=total_spent,
        total_paid=total_paid,
        total_owed=total_owed,
        invoice_count=invoice_count,
        is_admin=(current_user.role.lower() == 'admin')
    )


# ── 4. MARK A SUPPLIER INVOICE AS PAID (NEW) ──────────────────────────────────
@app.route('/supplier_invoice/<int:invoice_id>/pay', methods=['POST'])
@login_required
def supplier_invoice_pay(invoice_id):
    if current_user.role.lower() != 'admin':
        flash('Access Denied.', 'danger')
        return redirect(url_for('suppliers'))

    inv = SupplierInvoice.query.get_or_404(invoice_id)

    try:
        amount = float(request.form.get('amount_paid', 0) or 0)
    except (ValueError, TypeError):
        flash('Invalid payment amount.', 'danger')
        return redirect(url_for('supplier_detail', id=inv.supplier_id))

    if amount <= 0:
        flash('Payment amount must be greater than zero.', 'danger')
        return redirect(url_for('supplier_detail', id=inv.supplier_id))

    # Clamp: can never pay more than what is owed
    inv.amount_paid = round(
        min((inv.amount_paid or 0) + amount, inv.total_amount), 2
    )
    inv.recompute_status()
    db.session.commit()

    flash(
        f'Payment of KES {amount:,.2f} recorded. Invoice is now {inv.payment_status}.',
        'success'
    )
    return redirect(url_for('supplier_detail', id=inv.supplier_id))


# ── 5. EDIT SUPPLIER ───────────────────────────────────────────────────────────
@app.route('/edit_supplier/<int:id>', methods=['POST'])
@login_required
def edit_supplier(id):
    if current_user.role.lower() != 'admin':          # ← was missing before
        flash('Access Denied.', 'danger')
        return redirect(url_for('suppliers'))

    supplier = Supplier.query.get_or_404(id)

    new_name = request.form.get('name', '').strip()
    if not new_name:
        flash('Supplier name cannot be empty.', 'danger')
        return redirect(url_for('suppliers'))

    # Prevent renaming to a name already taken by another supplier
    conflict = Supplier.query.filter(
        Supplier.name == new_name,
        Supplier.id != id
    ).first()
    if conflict:
        flash(f'Another supplier named "{new_name}" already exists.', 'warning')
        return redirect(url_for('suppliers'))

    supplier.name           = new_name
    supplier.phone          = request.form.get('phone', '').strip()
    supplier.email          = request.form.get('email', '').strip()
    supplier.address        = request.form.get('address', '').strip()
    supplier.items_supplied = request.form.get('items_supplied', '').strip()

    db.session.commit()
    flash(f'Supplier "{supplier.name}" updated successfully.', 'success')
    return redirect(url_for('suppliers'))


# ── 6. DELETE SUPPLIER ─────────────────────────────────────────────────────────
@app.route('/delete_supplier/<int:id>', methods=['POST'])   # ← was GET before
@login_required
def delete_supplier(id):
    if current_user.role.lower() != 'admin':               # ← was missing before
        flash('Access Denied.', 'danger')
        return redirect(url_for('suppliers'))

    supplier = Supplier.query.get_or_404(id)
    name = supplier.name

    # cascade="all, delete-orphan" on Supplier.invoices in models.py ensures
    # SupplierInvoice rows and their SupplierInvoiceItem children are removed
    # automatically — no orphaned purchase records left behind.
    db.session.delete(supplier)
    db.session.commit()

    flash(f'Supplier "{name}" and all related purchase records deleted.', 'info')
    return redirect(url_for('suppliers'))

@app.route('/profit')
@login_required
def profit_report():
    if current_user.role.lower() != 'admin':
        flash('Access Denied.', 'danger')
        return redirect(url_for('dashboard'))

    try:
        # Revenue = what customers actually paid (after discounts, VAT-inclusive)
        # FIXED: was Sale.total_price (pre-discount) — now Sale.total_amount (post-discount)
        stats = db.session.query(
            func.sum(Sale.total_amount).label('revenue'),
            func.sum(Sale.quantity_sold * Product.buying_price).label('total_cost')
        ).join(Product, Sale.product_id == Product.id).first()

        revenue        = float(stats.revenue    or 0.0)
        cost           = float(stats.total_cost or 0.0)

        # All operating expenses (rent, salaries, utilities, waste write-offs, etc.)
        total_expenses = float(
            db.session.query(func.sum(Expense.amount)).scalar() or 0.0
        )

        # Gross profit = revenue minus cost of goods sold only
        gross_profit = round(revenue - cost, 2)

        # Net profit = gross profit minus ALL operating expenses
        # This is the real bottom-line figure the business owner needs
        net_profit = round(gross_profit - total_expenses, 2)

        # Percentages for progress bars — guard against division by zero
        cost_pc         = round((cost          / revenue * 100), 1) if revenue > 0 else 0
        expenses_pc     = round((total_expenses / revenue * 100), 1) if revenue > 0 else 0
        gross_profit_pc = round((gross_profit  / revenue * 100), 1) if revenue > 0 else 0
        net_profit_pc   = round((net_profit    / revenue * 100), 1) if revenue > 0 else 0

    except Exception as e:
        logger.error("Profit report error: %s", e)
        revenue, cost, total_expenses          = 0.0, 0.0, 0.0
        gross_profit, net_profit               = 0.0, 0.0
        cost_pc, expenses_pc                   = 0, 0
        gross_profit_pc, net_profit_pc         = 0, 0

    recent_sales = Sale.query.order_by(Sale.timestamp.desc()).limit(10).all()

    return render_template(
        'profit.html',
        revenue         = round(revenue, 2),
        cost            = round(cost, 2),
        total_expenses  = round(total_expenses, 2),
        gross_profit    = gross_profit,
        net_profit      = net_profit,
        cost_pc         = cost_pc,
        expenses_pc     = expenses_pc,
        gross_profit_pc = gross_profit_pc,
        net_profit_pc   = net_profit_pc,
        recent_sales    = recent_sales,
    )


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




# Add these endpoints towards the bottom of your app.py file

@app.route('/checkout', methods=['POST'])
@login_required
def checkout():
    try:
        # --- 1. Form Data Extraction ---
        client_id      = request.form.get('client_id')
        payment_method = request.form.get('payment_method', 'Cash')

        # SAFETY NET: Handle empty strings for cash fields
        raw_amt    = request.form.get('payment_amount', '')
        raw_cash   = request.form.get('cash_received', '')
        raw_change = request.form.get('change_given', '')

        cash_received = float(raw_cash)   if (raw_cash   and raw_cash.strip())   else 0.0
        change_given  = float(raw_change) if (raw_change and raw_change.strip()) else 0.0

        # Get the arrays sent from the updated cart.html
        item_ids  = request.form.getlist('item_ids[]')
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

            unit_price      = float(product.selling_price or 0)
            base_line_total = round(item.quantity * unit_price, 2)

            current_discount_val = discounts[i] if (discounts and i < len(discounts)) else '0'
            item_discount = float(current_discount_val) if (current_discount_val and current_discount_val.strip()) else 0.0

            # CLAMP: discount can never exceed the line total
            item_discount = round(min(max(item_discount, 0), base_line_total), 2)

            # VAT-INCLUSIVE: prices already contain tax — do NOT add 16% on top
            final_line_total = round(base_line_total - item_discount, 2)

            # Extract the VAT already embedded — for KRA reporting only
            extracted_tax = round(final_line_total * 0.16 / 1.16, 2)

            sale = Sale(
                product_name=product.name,
                product_id=product.id,
                quantity_sold=item.quantity,
                unit_price=unit_price,
                total_price=base_line_total,
                discount=item_discount,
                total_amount=final_line_total,
                tax_amount=extracted_tax,
                receipt_id=receipt.id,
                client_id=client.id,
                tax_rate=0.16
            )
            db.session.add(sale)

            movement = StockMovement(
                product_id=product.id,
                quantity_change=-item.quantity,
                reason="sale",
                reference_type="receipt",
                reference_id=receipt.id
            )
            db.session.add(movement)

            product.quantity -= item.quantity
            total_checkout_amount += final_line_total

        # --- 6. Finalize Totals & Payments ---
        receipt.total_amount = round(total_checkout_amount, 2)

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

        # --- 7. Cleanup ---
        CartItem.query.filter_by(user_id=current_user.id).delete()

        # --- 8. Loyalty Points: earn on every sale ---
        if client and not client.is_walk_in and total_checkout_amount > 0:
            points_earned = int(total_checkout_amount * POINTS_PER_KES)
            if points_earned > 0:
                client.loyalty_points = (client.loyalty_points or 0) + points_earned
                client.total_spent    = round((client.total_spent or 0) + total_checkout_amount, 2)
                db.session.add(LoyaltyTransaction(
                    client_id    = client.id,
                    points       = points_earned,
                    reason       = f'Sale receipt #{receipt.id}',
                    reference_id = receipt.id
                ))

        # --- 9. Loyalty Points: redeem if requested ---
        redeem_points = int(request.form.get('redeem_points', 0) or 0)
        if redeem_points >= MIN_REDEMPTION and client and not client.is_walk_in:
            if (client.loyalty_points or 0) >= redeem_points:
                redemption_discount   = round(redeem_points * POINTS_REDEEM_KES, 2)
                client.loyalty_points -= redeem_points
                db.session.add(LoyaltyTransaction(
                    client_id    = client.id,
                    points       = -redeem_points,
                    reason       = f'Redemption on receipt #{receipt.id}',
                    reference_id = receipt.id
                ))
                receipt.total_amount = round(max(0, receipt.total_amount - redemption_discount), 2)
                receipt.discount     = round((receipt.discount or 0) + redemption_discount, 2)

        # --- 10. Credit Sales: charge to client account ---
        if payment_method == 'Credit' and client and not client.is_walk_in:
            if client.credit_enabled:
                client.credit_balance = round(
                    (client.credit_balance or 0) + receipt.total_amount, 2
                )
            else:
                db.session.rollback()
                flash('Credit sales are not enabled for this client.', 'danger')
                return redirect(url_for('cart'))

        # --- 11. Commit ---
        db.session.commit()

        flash(f"Sale successful! Receipt #{receipt.id} generated.", "success")
        return redirect(url_for('invoice_view',
                                receipt_id=receipt.id,
                                cash_received=cash_received,
                                change_given=change_given))

    except Exception as e:
        db.session.rollback()
        logger.error("Critical checkout error: %s", e)
        logger.error("Checkout traceback:", exc_info=True)
        flash(f"Checkout Error: {str(e)}", "danger")
        return redirect(url_for('cart'))

@app.route('/procurement/po')
@login_required
def list_purchase_orders():
    if current_user.role not in ['admin', 'manager', 'stock_manager']:
        abort(403)
    orders = PurchaseOrder.query.order_by(PurchaseOrder.created_at.desc()).all()
    suppliers = Supplier.query.order_by(Supplier.name.asc()).all()
    products = Product.query.order_by(Product.name.asc()).all()

    # Serialize products to plain dicts for the JS item builder
    products_json = [
        {
            'id': p.id,
            'name': p.name,
            'buying_price': float(p.buying_price or 0)
        }
        for p in products
    ]

    return render_template('procurement/po_list.html', orders=orders, suppliers=suppliers, products=products, products_json=products_json)

@app.route('/procurement/po/create', methods=['POST'])
@login_required
def create_purchase_order():
    if current_user.role not in ['admin', 'manager', 'stock_manager']:
        return jsonify({'error': 'Unauthorized'}), 403
        
    supplier_id = request.form.get('supplier_id')
    notes = request.form.get('notes', '')
    items_raw = request.form.get('items') # JSON serialized string array from frontend interaction matrix
    
    if not supplier_id or not items_raw:
        flash("Missing mandatory tracking variables.", "danger")
        return redirect(url_for('list_purchase_orders'))
        
    try:
        items_data = json.loads(items_raw)
    except Exception:
        flash("Malformed procurement matrix layout data.", "danger")
        return redirect(url_for('list_purchase_orders'))

    # Auto-generate serial sequence: LPO-YYYY-COUNTER
    year = datetime.utcnow().year
    counter = PurchaseOrder.query.filter(PurchaseOrder.po_number.like(f"LPO-{year}-%")).count() + 1
    po_number = f"LPO-{year}-{counter:04d}"

    po = PurchaseOrder(
        po_number=po_number,
        supplier_id=supplier_id,
        status='Sent', # Instantly mark as active LPO record ready for distribution
        notes=notes
    )
    db.session.add(po)

    total_po_value = 0.0
    for item in items_data:
        p_id = int(item['product_id'])
        qty  = int(item['quantity'])
        cost = float(item['cost'])
        
        total_po_value += (qty * cost)
        po_item = PurchaseOrderItem(
            purchase_order=po,
            product_id=p_id,
            quantity_ordered=qty,
            expected_cost=cost
        )
        db.session.add(po_item)

    po.total_amount = total_po_value
    db.session.commit()
    
    flash(f"Purchase order {po_number} generated successfully.", "success")
    return redirect(url_for('list_purchase_orders'))

@app.route('/procurement/po/receive/<int:po_id>', methods=['POST'])
@login_required
def receive_purchase_order_delivery(po_id):
    """Processes incoming physical items and translates items directly to stock inventory tables."""
    if current_user.role not in ['admin', 'manager', 'stock_manager']:
        return jsonify({'error': 'Unauthorized'}), 403

    po = PurchaseOrder.query.get_or_404(po_id)
    if po.status in ['Fully Received', 'Cancelled']:
        return jsonify({'error': 'This order status cannot receive stock modifications.'}), 400

    # incoming format: {"items": [{"item_id": 1, "qty_received": 10}, ...], "invoice_no": "INV-99"}
    payload = request.get_json() or {}
    items_received = payload.get('items', [])
    invoice_no = payload.get('invoice_no', f"RCV-{po.po_number}")

    if not items_received:
        return jsonify({'error': 'No inventory item records selected for intake.'}), 400

    # Instantiate your existing SupplierInvoice to log actual financial liability
    from models import SupplierInvoice, SupplierInvoiceItem
    
    invoice = SupplierInvoice(
        supplier_id=po.supplier_id,
        invoice_number=invoice_no,
        total_amount=0.0,
        amount_paid=0.0,
        payment_status='Unpaid',
        notes=f"Automated ingestion pipeline receipt from order: {po.po_number}."
    )
    db.session.add(invoice)
    db.session.flush() # Gain access to invoice.id

    invoice_total = 0.0
    any_item_received = False
    
    for entry in items_received:
        item_id = int(entry['item_id'])
        qty_in  = int(entry['qty_received'])
        
        po_item = PurchaseOrderItem.query.filter_by(id=item_id, purchase_order_id=po.id).first()
        if not po_item or qty_in <= 0:
            continue
            
        # Safeguard logic constraints map
        max_allowable = po_item.quantity_ordered - po_item.quantity_received
        if qty_in > max_allowable:
            return jsonify({'error': f"Intake count exceeds original requested baseline quantity allocation."}), 400

        # Update order item metrics
        po_item.quantity_received += qty_in
        any_item_received = True

        # Sync master Product metrics instantly
        product = Product.query.get(po_item.product_id)
        if product:
            product.quantity += qty_in
            product.buying_price = po_item.expected_cost # Adjust system valuation benchmark prices

        # Log entry onto your legacy SupplierInvoiceItem tables structure
        inv_item = SupplierInvoiceItem(
            supplier_invoice_id=invoice.id,
            product_id=po_item.product_id,
            quantity=qty_in,
            buying_price=po_item.expected_cost
        )
        db.session.add(inv_item)
        invoice_total += (qty_in * po_item.expected_cost)

    if not any_item_received:
        db.session.rollback()
        return jsonify({'error': 'No valid updates processed.'}), 400

    invoice.total_amount = invoice_total
    
    # Calculate order state transition checks
    fully_complete = all(item.quantity_received >= item.quantity_ordered for item in po.items)
    po.status = 'Fully Received' if fully_complete else 'Partially Received'
    
    db.session.commit()
    return jsonify({'success': True, 'current_status': po.status})

@app.route('/procurement/po/print/<int:po_id>')
@login_required
def print_purchase_order(po_id):
    if current_user.role not in ['admin', 'manager', 'stock_manager']:
        abort(403)
    po = PurchaseOrder.query.get_or_404(po_id)
    # Fetch supplier explicit relationship records separately for the print view
    from models import Supplier
    supplier = Supplier.query.get(po.supplier_id)
    return render_template('procurement/po_print_layout.html', po=po, supplier=supplier)            

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

    sales    = Sale.query.filter_by(receipt_id=receipt.id).all()
    payments = Payment.query.filter_by(receipt_id=receipt.id).order_by(Payment.id.desc()).all()
    payment  = payments[0] if payments else None

    # Compute summary figures the same way receipt() and download_invoice() do
    # subtotal = sum of each line's final charged amount (after per-line discount)
    subtotal         = round(sum(s.total_amount or 0 for s in sales), 2)
    total_discounts  = round(sum(s.discount or 0  for s in sales), 2)
    # Extracted VAT already inside the prices — informational only, never additive
    total_tax        = round(subtotal * 0.16 / 1.16, 2)

    # Cash figures: prefer DB columns stored at checkout, fall back to URL params
    # (URL params are passed by the checkout redirect for backward compatibility)
    cash_received = float(
        receipt.cash_received
        if (hasattr(receipt, 'cash_received') and receipt.cash_received)
        else request.args.get('cash_received', 0)
    )
    change_given = float(
        receipt.change_given
        if (hasattr(receipt, 'change_given') and receipt.change_given)
        else request.args.get('change_given', 0)
    )

    return render_template(
        'receipt.html',
        receipt=receipt,
        sales=sales,
        payments=payments,
        payment=payment,
        subtotal=subtotal,
        total_discounts=total_discounts,
        total_tax=total_tax,
        cash_received=cash_received,
        change_given=change_given,
    )

@app.route('/api/fast-moving')
@login_required
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
@login_required
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

