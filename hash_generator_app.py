import streamlit as st
import os
import base64
import hashlib

def hash_password(password: str) -> str:
    """Hashes a password with a salt using a strong algorithm."""
    salt = os.urandom(16)
    # Use PBKDF2 with 100,000 iterations for security
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100,000)
    # Store salt and hash together, encoded in base64
    return base64.b64encode(salt + pwd_hash).decode('ascii').strip()

# --- Streamlit App UI ---
st.set_page_config(page_title="Password Hash Generator", layout="centered")

st.title("🔐 Secure Password Hash Generator")

st.write(
    "This tool creates a secure password hash that you can copy and paste into "
    "your `users.json` file. The password you enter is never stored or saved."
)

with st.form("hash_generator_form"):
    st.image(
        "https://images.squarespace-cdn.com/content/v1/651eb4433b13e72c1034f375/"
        "369c5df0-5363-4827-b041-1add0367f447/PBB+long+logo.png?format=1500w",
        width=300
    )

    password_input = st.text_input(
        "Enter the password to hash",
        type="password",
        help="Type the new password here."
    )

    submitted = st.form_submit_button("Generate Hash")

    if submitted and password_input:
        # Generate the hash from the user's input
        generated_hash = hash_password(password_input)

        st.success("Hash generated successfully!")
        st.write("Copy the text below:")
        # Display the hash in a code block for easy copying
        st.code(generated_hash, language="text")
        st.info(
            "Paste this hash into the 'password' field for the correct user in your `users.json` file.",
            icon="📋"
        )
    elif submitted and not password_input:
        st.warning("Please enter a password before generating the hash.")
