from app import app, db, User

def seed_users():
    # Admin user
    admin = User.query.filter_by(username="admin").first()
    if not admin:
        admin = User(username="admin", role="admin")
        admin.set_password("admin123")
        db.session.add(admin)
        print("✅ Admin user created")
    else:
        # Force correct role if wrong
        if admin.role != "admin":
            admin.role = "admin"
            print("🔧 Fixed admin role")
        # Reset password if needed
        admin.set_password("admin123")

    # Employee user
    employee = User.query.filter_by(username="employee").first()
    if not employee:
        employee = User(username="employee", role="employee")
        employee.set_password("employee123")
        db.session.add(employee)
        print("✅ Employee user created")
    else:
        if employee.role != "employee":
            employee.role = "employee"
            print("🔧 Fixed employee role")
        employee.set_password("employee123")

    db.session.commit()
    print("🎉 Seeding complete!")

if __name__ == "__main__":
    with app.app_context():
        seed_users()
