import os
import base64
import hashlib

def hash_password(password: str) -> str:
    """Hashes a password with a salt."""
    salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return base64.b64encode(salt + pwd_hash).decode('ascii').strip()
