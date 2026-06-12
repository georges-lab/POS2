"""
bizTOOL Emergency Password Reset Tool
======================================
Compile separately with:
    pyinstaller --onefile --windowed --name bizTOOL_Reset biztool_reset.py

Add to Inno Setup [Files]:
    Source: "dist\bizTOOL_Reset.exe"; DestDir: "{app}"; Flags: ignoreversion

Add to Inno Setup [Icons] (Start Menu only — not desktop):
    Name: "{group}\Emergency Password Reset"; Filename: "{app}\bizTOOL_Reset.exe"

DB Location (mirrors app.py get_user_data_path logic exactly):
    Frozen (.exe) → %LOCALAPPDATA%\bizTOOL\inventory.db
    Dev  (.py)    → ./instance/inventory.db
"""

import os
import sys
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox
from werkzeug.security import generate_password_hash


# ── Mirror app.py's exact DB path logic ──────────────────────────────────────

def get_db_path():
    """
    Mirrors get_user_data_path() + db_file from app.py exactly.

    Frozen (.exe):  %LOCALAPPDATA%\\bizTOOL\\inventory.db
    Dev   (.py):    ./instance/inventory.db
    """
    if getattr(sys, 'frozen', False):
        base = os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            "bizTOOL"
        )
    else:
        base = os.path.join(os.path.abspath(os.path.dirname(__file__)), "instance")

    return os.path.join(base, "inventory.db")


# ── Core reset logic ──────────────────────────────────────────────────────────

def get_all_users(db_path):
    """Return list of (id, username, role) from the users table."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, role FROM user ORDER BY role, username")
    users = cursor.fetchall()
    conn.close()
    return users


def reset_user_password(db_path, username, new_password):
    """
    Hash new_password with Werkzeug (same as app.py) and update the DB.
    Returns (success: bool, message: str)
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM user WHERE username = ?", (username,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return False, f"User '{username}' not found in the database."

    hashed = generate_password_hash(new_password)
    cursor.execute(
        "UPDATE user SET password_hash = ? WHERE username = ?",
        (hashed, username)
    )
    conn.commit()
    conn.close()
    return True, f"Password for '{username}' has been reset successfully."


# ── GUI ───────────────────────────────────────────────────────────────────────

class ResetApp:
    def __init__(self, root):
        self.root = root
        self.root.title("bizTOOL — Emergency Password Reset")
        self.root.geometry("420x370")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1a2e")

        self.db_path = get_db_path()
        self._build_ui()
        self._check_db()

    def _build_ui(self):
        # ── Header ──
        header = tk.Frame(self.root, bg="#16213e", pady=14)
        header.pack(fill="x")

        tk.Label(
            header,
            text="🔐  bizTOOL Password Reset",
            font=("Segoe UI", 14, "bold"),
            fg="#e94560",
            bg="#16213e"
        ).pack()

        tk.Label(
            header,
            text="For emergency use only — when all users are locked out",
            font=("Segoe UI", 8),
            fg="#aaaaaa",
            bg="#16213e"
        ).pack()

        # ── DB Status ──
        self.status_frame = tk.Frame(self.root, bg="#1a1a2e", pady=6)
        self.status_frame.pack(fill="x", padx=20)

        self.lbl_db = tk.Label(
            self.status_frame,
            text="",
            font=("Segoe UI", 8),
            fg="#aaaaaa",
            bg="#1a1a2e",
            wraplength=380,
            justify="left"
        )
        self.lbl_db.pack(anchor="w")

        # ── Form ──
        form = tk.Frame(self.root, bg="#1a1a2e", pady=10)
        form.pack(padx=24, fill="x")

        def label(text):
            return tk.Label(form, text=text, font=("Segoe UI", 10),
                            fg="#cccccc", bg="#1a1a2e", anchor="w")

        def entry(**kwargs):
            e = tk.Entry(form, font=("Segoe UI", 10), bg="#0f3460",
                         fg="white", insertbackground="white",
                         relief="flat", bd=6, **kwargs)
            return e

        # Username dropdown
        label("Select User:").pack(fill="x", pady=(6, 2))
        self.username_var = tk.StringVar()
        self.user_dropdown = ttk.Combobox(
            form,
            textvariable=self.username_var,
            font=("Segoe UI", 10),
            state="readonly",
            width=35
        )
        self.user_dropdown.pack(fill="x", pady=(0, 8))

        # New password
        label("New Password:").pack(fill="x", pady=(4, 2))
        self.entry_pass = entry(show="*")
        self.entry_pass.pack(fill="x", ipady=4, pady=(0, 8))

        # Confirm password
        label("Confirm Password:").pack(fill="x", pady=(4, 2))
        self.entry_confirm = entry(show="*")
        self.entry_confirm.pack(fill="x", ipady=4, pady=(0, 14))

        # Reset button
        self.btn_reset = tk.Button(
            form,
            text="Reset Password",
            font=("Segoe UI", 11, "bold"),
            bg="#e94560",
            fg="white",
            activebackground="#c73652",
            activeforeground="white",
            relief="flat",
            bd=0,
            pady=8,
            cursor="hand2",
            command=self._do_reset
        )
        self.btn_reset.pack(fill="x")

    def _check_db(self):
        """Verify DB exists and load users into the dropdown."""
        if not os.path.exists(self.db_path):
            self.lbl_db.config(
                text=f"⚠  Database not found:\n{self.db_path}",
                fg="#e94560"
            )
            self.btn_reset.config(state="disabled")
            return

        self.lbl_db.config(
            text=f"✓  Database: {self.db_path}",
            fg="#4caf50"
        )

        try:
            users = get_all_users(self.db_path)
            if not users:
                self.lbl_db.config(
                    text="⚠  No users found in database.",
                    fg="#e94560"
                )
                self.btn_reset.config(state="disabled")
                return

            # Format: "admin  [admin]", "cashier  [employee]"
            self.user_map = {}
            display_names = []
            for uid, uname, role in users:
                display = f"{uname}  [{role or 'unknown'}]"
                display_names.append(display)
                self.user_map[display] = uname

            self.user_dropdown["values"] = display_names
            self.user_dropdown.current(0)

        except Exception as e:
            self.lbl_db.config(
                text=f"⚠  Error reading users: {e}",
                fg="#e94560"
            )
            self.btn_reset.config(state="disabled")

    def _do_reset(self):
        selected_display = self.username_var.get().strip()
        new_pass    = self.entry_pass.get()
        confirm     = self.entry_confirm.get()

        # Validation
        if not selected_display:
            messagebox.showerror("Missing Field", "Please select a user.")
            return

        username = self.user_map.get(selected_display, selected_display)

        if not new_pass:
            messagebox.showerror("Missing Field", "Please enter a new password.")
            return

        if len(new_pass) < 6:
            messagebox.showerror("Too Short", "Password must be at least 6 characters.")
            return

        if new_pass != confirm:
            messagebox.showerror("Mismatch", "Passwords do not match.")
            return

        # Confirm action
        confirm_action = messagebox.askyesno(
            "Confirm Reset",
            f"Reset password for user '{username}'?\n\nThis cannot be undone."
        )
        if not confirm_action:
            return

        # Execute
        success, message = reset_user_password(self.db_path, username, new_pass)

        if success:
            messagebox.showinfo("Success ✓", message + "\n\nYou can now log in to bizTOOL.")
            self.entry_pass.delete(0, tk.END)
            self.entry_confirm.delete(0, tk.END)
        else:
            messagebox.showerror("Failed", message)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    app = ResetApp(root)
    root.mainloop()