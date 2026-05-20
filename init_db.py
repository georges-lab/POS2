# init_db.py
from app import app
from extensions import db
from models import *


def init_db(app):
  with app.app_context():
    # Create all tables
    db.create_all()
    print("✅ Database initialized and all tables created!")

    # --- Default Walk-in client ---
    walk_in = Client.query.filter_by(is_walk_in=True).first()
    if not walk_in:
        walk_in = Client(name="Walk-in Customer", is_walk_in=True)
        db.session.add(walk_in)
        db.session.commit()
        print("✅ Walk-in client created")

    # --- Default admin user ---
    admin = User.query.filter_by(username="admin").first()
    if not admin:
        admin = User(username="admin", role="admin")
        admin.set_password("admin123")  # default password
        db.session.add(admin)
        db.session.commit()
        print("✅ Default admin user created: username='admin', password='admin123'")

    # --- Default employee user ---
    employee = User.query.filter_by(username="employee").first()
    if not employee:
        employee = User(username="employee", role="employee")
        employee.set_password("emp123")  # default password
        db.session.add(employee)
        db.session.commit()
        print("✅ Default employee user created: username='employee', password='emp123'")
