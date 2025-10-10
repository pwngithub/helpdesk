import os
import io
import re
import urllib.request
import requests
from datetime import datetime
import pandas as pd
import streamlit as st
from sqlalchemy.orm import joinedload
from dotenv import load_dotenv

from db import engine, get_db
from schema import Base, Ticket, TicketEvent, Customer
from utils import compute_sla_due, fmt_dt
from constants import STATUS_ORDER, PRIORITY_ORDER, STATUS_COLOR, PRIORITY_COLOR

# ---------------------- INITIAL SETUP ----------------------
Base.metadata.create_all(bind=engine)
st.set_page_config(page_title="Pioneer Helpdesk", page_icon="🎫", layout="wide")
load_dotenv()  # Load GOOGLE_API_KEY from .env if present

# Pioneer Branding
PIONEER_LOGO = (
    "https://images.squarespace-cdn.com/content/v1/651eb4433b13e72c1034f375/"
    "369c5df0-5363-4827-b041-1add0367f447/PBB+long+logo.png?format=1500w"
)
st.markdown(
    f"<div style='display:flex;align-items:center;gap:10px;'>"
    f"<img src='{PIONEER_LOGO}' height='40'>"
    f"<h2 style='margin:0;'>Pioneer Helpdesk</h2></div><hr>",
    unsafe_allow_html=True,
)

# ---------------------- GOOGLE SHEETS API IMPORT ----------------------
def fetch_customers_from_sheet_api_key() -> pd.DataFrame:
    """Fetch customers securely from Google Sheets using API key."""
    SHEET_ID = "1ywqLJIzydhifdUjX9Zo03B536LEUhH483hRAazT3zV8"
    RANGE_NAME = "Customers!A:D"  # Adjust tab and range
    api_key = os.getenv("GOOGLE_API_KEY", st.secrets.get("GOOGLE_API_KEY", ""))

    if not api_key:
        raise ValueError("Google API key not found. Please set GOOGLE_API_KEY in .env or Streamlit secrets.")

    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{RANGE_NAME}?key={api_key}"
    response = requests.get(url)

    if response.status_code != 200:
        raise ValueError(f"Google Sheets API error: {response.status_code} - {response.text}")

    data = response.json()
    values = data.get("values", [])
    if not values:
        raise ValueError("No data returned from sheet")

    headers = [h.strip().lower() for h in values[0]]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)

    # Normalize columns
    rename_map = {
        "account": "account_number",
        "account #": "account_number",
        "account number": "account_number",
        "name": "name",
        "contact method": "phone",
        "phone": "phone",
    }
    df = df.rename(columns=rename_map)

    # Ensure expected columns
    for key in ["account_number", "name", "phone"]:
        if key not in df.columns:
            df[key] = ""

    return df[["account_number", "name", "phone"]]

def upsert_customers(db, df):
    """Upsert customers safely from dataframe."""
    ins = upd = 0
    for _, r in df.iterrows():
        acct = str(r.get("account_number") or "").strip()
        name = str(r.get("name") or "").strip()
        phone = str(r.get("phone") or "").strip()
        if not acct:
            continue
        c = db.query(Customer).filter(Customer.account_number == acct).first()
        if c:
            if name:
                c.name = name
            if phone:
                c.phone = phone
            upd += 1
        else:
            db.add(Customer(account_number=acct, name=name, phone=phone))
            ins += 1
    db.commit()
    return ins, upd

def sync_customers():
    """Sync customers from Google Sheets API using key."""
    with st.spinner("🔄 Syncing customer data from Google Sheets..."):
        try:
            df_raw = fetch_customers_from_sheet_api_key()
            if not df_raw.empty:
                with next(get_db()) as db:
                    ins, upd = upsert_customers(db, df_raw)
                st.success(f"✅ Sync complete — {ins} new, {upd} updated customers.")
            else:
                st.warning("⚠️ Sheet was empty or missing expected columns.")
        except Exception as e:
            st.warning(f"⚠️ Could not sync customers: {e}")

# ---------------------- PAGE: DASHBOARD ----------------------
def page_dashboard(db):
    rows = db.query(Ticket).options(joinedload(Ticket.events)).order_by(Ticket.created_at.desc()).all()
    if not rows:
        st.info("No tickets yet.")
        return
    data = []
    for t in rows:
        data.append({
            "Key": f"<a href='?ticket={t.ticket_key}' target='_self'>{t.ticket_key}</a>",
            "Created": fmt_dt(t.created_at),
            "Customer": t.customer_name,
            "Acct #": t.account_number,
            "Phone": t.phone,
            "Status": t.status,
            "Priority": t.priority,
            "Assigned": t.assigned_to or "-",
            "Reason": t.call_reason,
            "Service": t.service_type,
            "Description": t.description or "-"
        })
    st.write(pd.DataFrame(data).to_html(escape=False, index=False), unsafe_allow_html=True)

# ---------------------- PAGE: NEW TICKET ----------------------
def page_new_ticket(db):
    st.subheader("➕ Create New Ticket")

    acct = st.text_input("Account Number (search)")
    name = st.text_input("Customer Name (search)")
    matches = []

    search_term = (acct or name).strip()
    if search_term:
        if acct:
            matches = db.query(Customer).filter(Customer.account_number.ilike(f"%{acct}%")).limit(20).all()
        elif name:
            matches = db.query(Customer).filter(Customer.name.ilike(f"%{name}%")).limit(20).all()

    if matches:
        st.write("### 🔍 Matching Customers")
        for c in matches:
            if st.button(f"{c.name} — {c.account_number} — {c.phone}", key=f"select_{c.id}"):
                st.session_state["new_acct"] = c.account_number
                st.session_state["new_name"] = c.name
                st.session_state["new_phone"] = c.phone
                st.success(f"Loaded: {c.name} ({c.account_number})")

    customer_name = st.text_input("Customer Name", key="new_name")
    account_number = st.text_input("Account Number", key="new_acct")
    phone = st.text_input("Phone", key="new_phone")
    service_type = st.selectbox("Service Type", ["Fiber", "DSL", "Wireless", "TV", "Voice", "Other"])
    call_reason = st.selectbox("Reason", ["outage", "repair", "billing", "upgrade", "cancel", "other"])
    priority = st.selectbox("Priority", PRIORITY_ORDER, 1)
    description = st.text_area("Description", height=120)
    assigned_to = st.selectbox("Assign To", ["Unassigned", "Billing", "Support", "Sales",
                                             "BJ", "Megan", "Billy", "Gillian", "Gabby", "Chuck", "Aidan"])

    if st.button("Create Ticket"):
        created_at = datetime.utcnow()
        t = Ticket(
            ticket_key=f"TCK-{int(created_at.timestamp())}",
            created_at=created_at,
            customer_name=customer_name,
            account_number=account_number,
            phone=phone,
            service_type=service_type,
            call_reason=call_reason,
            description=description,
            status="Open",
            priority=priority,
            assigned_to=assigned_to,
            sla_due=compute_sla_due(priority, created_at),
        )
        db.add(t)
        db.commit()
        st.success(f"✅ Created {t.ticket_key}")
        st.info("🔄 Refreshing customers from Google Sheets...")
        sync_customers()
        st.session_state["redirect_to_dashboard"] = True
        st.stop()

# ---------------------- PAGE: CUSTOMERS ----------------------
def page_customers(db):
    st.subheader("👥 Customers")
    rows = db.query(Customer).order_by(Customer.name).all()
    if not rows:
        st.info("No customers found.")
        return
    data = [{"Account #": c.account_number, "Name": c.name, "Phone": c.phone} for c in rows]
    st.write(pd.DataFrame(data))

# ---------------------- PAGE: TICKET DETAIL ----------------------
def page_ticket_detail(db, ticket_key):
    t = db.query(Ticket).filter(Ticket.ticket_key == ticket_key).first()
    if not t:
        st.error("Ticket not found.")
        if st.button("← Back to Dashboard"):
            st.query_params.clear()
            st.rerun()
        return
    st.title(f"{t.ticket_key} — {t.customer_name}")
    st.write(f"**Account:** {t.account_number}  \n**Phone:** {t.phone}")
    st.write(f"**Status:** {t.status}  \n**Priority:** {t.priority}")
    st.write("---")
    st.write(t.description or "")

# ---------------------- MAIN APP ----------------------
st.sidebar.title("📋 Navigation")
page = st.sidebar.radio("Go to", ["Dashboard", "New Ticket", "Customers"])

sync_customers()

ticket_key = st.query_params.get("ticket")
if ticket_key:
    with next(get_db()) as db:
        page_ticket_detail(db, ticket_key)
else:
    with next(get_db()) as db:
        if page == "Dashboard":
            page_dashboard(db)
        elif page == "New Ticket":
            page_new_ticket(db)
        elif page == "Customers":
            page_customers(db)
