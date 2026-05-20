import json
from datetime import datetime, timedelta
from cryptography.fernet import Fernet

# Generate key only once, then save and reuse it
def load_or_create_key():
    try:
        with open("license.key", "rb") as key_file:
            return key_file.read()
    except FileNotFoundError:
        key = Fernet.generate_key()
        with open("license.key", "wb") as key_file:
            key_file.write(key)
        return key

# Generate license
def generate_license():
    key = load_or_create_key()
    fernet = Fernet(key)

    # Set license info
    license_info = {
        "status": "active",
        "expiry": (datetime.utcnow() + timedelta(days=2000)).strftime("%Y-%m-%d")
    }

    # Encrypt license data
    encrypted_data = fernet.encrypt(json.dumps(license_info).encode())

    # Save encrypted license
    with open("license.dat", "wb") as lic_file:
        lic_file.write(encrypted_data)

    print(" License generated successfully.")

if __name__ == "__main__":
    generate_license()
