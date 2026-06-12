# reset_admin.py  (bundled separately, NOT via PyInstaller)
import sys
import os

# Point to the same SQLite DB your app uses
db_path = os.path.join(os.environ['APPDATA'], 'bizTOOL', 'biztool.db')

from werkzeug.security import generate_password_hash
import sqlite3

def reset_admin(username, new_password):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    hashed = generate_password_hash(new_password)
    cursor.execute(
        "UPDATE users SET password_hash = ? WHERE username = ? AND is_admin = 1",
        (hashed, username)
    )

    if cursor.rowcount == 0:
        print(f"[!] No admin user '{username}' found.")
    else:
        conn.commit()
        print(f"[✓] Password for '{username}' has been reset.")

    conn.close()

if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("Usage: python reset_admin.py <username> <new_password>")
        sys.exit(1)

    reset_admin(sys.argv[1], sys.argv[2])