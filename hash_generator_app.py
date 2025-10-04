import streamlit as st
import os
import base64
import hashlib

def hash_password(password: str) -> str:
    """Hashes a password with a salt using a strong algorithm."""
    salt = os.urandom(16)
    # Use PBKDF2 with 100,000 iterations for security
    # FIX: Removed the comma from 100,000
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    # Store salt and hash together, encoded in base64
    return base64.b64encode(salt + pwd_hash).decode('ascii').strip()

# --- Streamlit App UI ---
st.set_page_config(page_title="Password Hash Generator", layout="centered")

st.title("🔐 Secure Password Hash Generator")

st.info(
    "Use this tool to create a secure password hash. "
    "Copy the generated hash and paste it into the `users.json` file of your main application."
)

with st.form("hash_generator_form"):
    password_input = st.text_input(
        "Enter the new password to hash",
        type="password"
    )

    submitted = st.form_submit_button("Generate Hash")

    if submitted and password_input:
        generated_hash = hash_password(password_input)
        st.success("Hash generated successfully!")
        st.write("Copy the text below:")
        st.code(generated_hash, language="text")
    elif submitted and not password_input:
        st.warning("Please enter a password.")
