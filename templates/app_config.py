# ─────────────────────────────────────────
# 2. CORE HELPERS
# ─────────────────────────────────────────

import os
import sys
import shutil
import tkinter as tk
from tkinter import messagebox
from datetime import datetime
from werkzeug.security import generate_password_hash
from flask import Flask
from flask_login import LoginManager


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
    """
    Returns a WRITABLE, PERSISTENT folder for the database and uploads.

    INNO SETUP + PYINSTALLER NOTES:
    ─────────────────────────────────
    ▸ Program Files (where Inno Setup installs the .exe) is READ-ONLY
      on Windows — the database CANNOT live there.
    ▸ sys._MEIPASS is DELETED when the .exe closes — never store the
      database there either.
    ▸ AppData\\Roaming\\bizTOOL is:
        - Always writable (no UAC issues)
        - Persists across app restarts and updates
        - Survives Inno Setup upgrades/reinstalls (data is NOT wiped)
        - Per-user (each Windows user has their own copy)

    Final paths:
    ▸ .exe  → C:\\Users\\<name>\\AppData\\Roaming\\bizTOOL\\
    ▸ dev   → ./instance/
    """
    if getattr(sys, 'frozen', False):
        # Installed via Inno Setup or run as standalone .exe
        base = os.path.join(os.environ["APPDATA"], "bizTOOL")
    else:
        # Development — keep data local to the project
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
# DefaultDirName={autopf}\bizTOOL        ; installs to Program Files
# DefaultGroupName=bizTOOL POS
# OutputDir=installer_output
# OutputBaseFilename=bizTOOL_Setup
# PrivilegesRequired=lowest              ; no admin rights needed to install
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
# Name: "{group}\bizTOOL POS"; Filename: "{app}\biztool.exe"
# Name: "{commondesktop}\bizTOOL POS"; Filename: "{app}\biztool.exe"
#
# [Run]
# Filename: "{app}\biztool.exe"; Description: "Launch bizTOOL POS"; Flags: nowait postinstall skipifsilent
# ─────────────────────────────────────────
