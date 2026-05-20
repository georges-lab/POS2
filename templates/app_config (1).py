import os
import sys
import io
import json
import csv
import time
import shutil
import threading
import requests
from datetime import datetime, timedelta, date
from io import BytesIO

# UI and Desktop Window Management
import webview
import tkinter as tk
from tkinter import messagebox

# Flask, Security, and Database Extensions
from flask import (Flask, render_template, redirect, url_for, request,
                   flash, session, abort, jsonify, make_response, send_file)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, login_user, login_required,
                         logout_user, current_user, UserMixin)
from flask_migrate import Migrate
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet
from sqlalchemy import func

# PDF Generation
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


# ─────────────────────────────────────────
# 1. CORE PATH HELPERS
# ─────────────────────────────────────────

def resource_path(relative_path):
    """
    Resolves path to a BUNDLED read-only resource (templates, static, etc.)

    PyInstaller extracts bundled files into a temp folder at sys._MEIPASS.
    Inno Setup installs the .exe to Program Files — assets sit alongside it.
    This function finds them correctly in both scenarios.

    ▸ Running as .exe  → sys._MEIPASS  (PyInstaller temp extract dir)
    ▸ Running as .py   → current working directory
    """
    try:
        base_path = sys._MEIPASS          # PyInstaller frozen bundle
    except AttributeError:
        base_path = os.path.abspath(".")  # Normal Python / dev run
    return os.path.join(base_path, relative_path)


def get_user_data_path():
    """
    Returns a WRITABLE, PERSISTENT folder for the database and uploads.

    WHY NOT next to the .exe?
    ─────────────────────────
    ▸ Program Files (Inno Setup default install dir) is READ-ONLY on
      Windows without UAC elevation — writing the DB there will fail.
    ▸ sys._MEIPASS is DELETED when the .exe closes — the DB would be
      wiped on every exit.

    AppData\\Roaming\\bizTOOL is the correct location because it is:
      - Always writable with no UAC prompt
      - Persistent across reboots and .exe updates
      - NOT touched by Inno Setup upgrades/reinstalls
      - Per-user (each Windows account has its own data)

    ▸ .exe / installed → C:\\Users\\<name>\\AppData\\Roaming\\bizTOOL\\
    ▸ dev (plain .py)  → <project folder>\\instance\\
    """
    if getattr(sys, 'frozen', False):
        base = os.path.join(os.environ["APPDATA"], "bizTOOL")
    else:
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
    Calls root.quit() + root.destroy() to release the thread so
    pywebview is not blocked after the dialog closes.
    """
    root = tk.Tk()
    root.withdraw()
    messagebox.showwarning(title, message)
    root.quit()
    root.destroy()


# ─────────────────────────────────────────
# 2. BASE DIRECTORY  (single definition)
# ─────────────────────────────────────────
# Used ONLY to locate the bundled seed database on first run.
# Never write anything here — Program Files is read-only.
#
# ▸ .exe  → folder the .exe lives in  (e.g. Program Files\bizTOOL)
# ▸ dev   → folder containing this .py file
if getattr(sys, 'frozen', False):
    basedir = os.path.dirname(sys.executable)
else:
    basedir = os.path.abspath(os.path.dirname(__file__))

# Ensure instance folder exists in dev mode
instance_path = os.path.join(basedir, 'instance')
os.makedirs(instance_path, exist_ok=True)


# ─────────────────────────────────────────
# 3. FLASK APP INITIALISATION
# ─────────────────────────────────────────
# template_folder and static_folder are set explicitly so Flask
# finds them whether running as plain .py, frozen .exe, or after
# Inno Setup installs the app to Program Files.
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
# 4. DATABASE PATH  (single source of truth)
# ─────────────────────────────────────────
# All DB reads and writes go through get_user_data_path() → AppData.
# This is the only place the database URI is set.
user_data_path = get_user_data_path()
db_file        = os.path.join(user_data_path, "inventory.db")

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_file


# ─────────────────────────────────────────
# 5. FIRST-RUN SEED DATABASE COPY
# ─────────────────────────────────────────
# If this is the very first launch (no DB in AppData yet) and a seed
# database was bundled with the installer, copy it across so the app
# starts with its default data (admin user, walk-in client, etc.)
#
# INNO SETUP .ISS — include this in your [Files] section:
#   Source: "dist\instance\inventory.db";
#   DestDir: "{app}\instance";
#   Flags: onlyifdestfilenotexists
#
# The Inno flag prevents overwriting on upgrades.
# The Python check below prevents overwriting existing user data.
default_db = os.path.join(basedir, "instance", "inventory.db")

if not os.path.exists(db_file) and os.path.exists(default_db):
    shutil.copy(default_db, db_file)


# ─────────────────────────────────────────
# 6. UPLOAD FOLDER
# ─────────────────────────────────────────
# Stored in AppData alongside the DB so Inno Setup upgrades never
# wipe user-uploaded files.
app.config['UPLOAD_FOLDER'] = os.path.join(user_data_path, 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


# ─────────────────────────────────────────
# 7. EXTENSIONS & MODELS
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
# 8. DATABASE INITIALISATION
# ─────────────────────────────────────────
def create_tables_and_users():
    """
    Creates all tables and seeds the default admin user + walk-in client.

    FIX — role stored as lowercase 'admin':
    The previous build stored role='Admin' (capital A). Every route
    guard compared against 'admin' (lowercase) so the check always
    failed inside the .exe, producing "Access Denied" on profit report
    and any other admin-only page. Lowercase fixes this permanently.
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
# 9. MIGRATE EXISTING DB: normalise roles
# ─────────────────────────────────────────
def normalise_roles():
    """
    Silently converts any 'Admin' (capital A) → 'admin' in the database.

    WHY THIS MATTERS FOR INNO SETUP:
    Inno Setup upgrades leave AppData untouched, so a user who installed
    the old build still has role='Admin' in their database. Without this
    function they would get "Access Denied" forever even after upgrading.
    This runs on every startup and fixes it transparently — no data loss,
    no user action, no manual SQL required.
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
# 10. FLASK-LOGIN SETUP
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
    Makes variables available in every template automatically.
    current_user is imported inside the function (not at module level)
    to avoid circular import crashes at PyInstaller bundle time.
    """
    from flask_login import current_user
    return {
        'current_year': datetime.now().year,
        # Use is_admin in templates instead of current_user.role == 'admin'
        # to avoid case-sensitivity bugs across the whole app.
        'is_admin': (
            current_user.is_authenticated and
            current_user.role.lower() == 'admin'
        ),
    }


# ─────────────────────────────────────────
# 11. STARTUP CALL ORDER
# ─────────────────────────────────────────
# Call these TWO functions at the bottom of this file (or in main.py)
# before starting Flask / pywebview:
#
#   create_tables_and_users()   # 1. create schema + seed admin user
#   normalise_roles()           # 2. fix any 'Admin' → 'admin' in DB
#
# Then launch as normal:
#   app.run(debug=True)                        ← dev / testing
#   webview.start(...)                         ← production pywebview
# ─────────────────────────────────────────


# ─────────────────────────────────────────
# ROUTE GUARD PATTERN (use in every route)
# ─────────────────────────────────────────
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
# INNO SETUP — COMPLETE .ISS TEMPLATE
# ─────────────────────────────────────────
#
# [Setup]
# AppName=bizTOOL POS
# AppVersion=1.0
# DefaultDirName={autopf}\bizTOOL
# DefaultGroupName=bizTOOL POS
# OutputDir=installer_output
# OutputBaseFilename=bizTOOL_Setup
# PrivilegesRequired=lowest              ; no UAC prompt needed
#
# [Files]
# Source: "dist\biztool.exe";            DestDir: "{app}";          Flags: ignoreversion
# Source: "dist\instance\inventory.db";  DestDir: "{app}\instance"; Flags: onlyifdestfilenotexists
# Source: "dist\templates\*";            DestDir: "{app}\templates"; Flags: ignoreversion recursesubdirs
# Source: "dist\static\*";               DestDir: "{app}\static";    Flags: ignoreversion recursesubdirs
#
# [Icons]
# Name: "{group}\bizTOOL POS";      Filename: "{app}\biztool.exe"
# Name: "{commondesktop}\bizTOOL POS"; Filename: "{app}\biztool.exe"
#
# [Run]
# Filename: "{app}\biztool.exe"; Description: "Launch bizTOOL POS"; Flags: nowait postinstall skipifsilent
# ─────────────────────────────────────────

# ... Rest of @app.route functions follow below this block ...
