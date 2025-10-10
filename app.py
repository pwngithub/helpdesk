import os
import io
import re
import urllib.request
from datetime import datetime
import pandas as pd
import streamlit as st
from sqlalchemy.orm import joinedload

from db import engine, get_db
from schema import Base, Ticket, TicketEvent, Customer
from utils import compute_sla_due, fmt_dt
from constants import STATUS_ORDER, PRIORITY_ORDER, STATUS_COLOR, PRIORITY_COLOR

Base.metadata.create_all(bind=engine)
st.set_page_config(page_title="Pioneer Ticketing", page_icon="🎫", layout="wide")

PIONEER_LOGO = "https://images.squarespace-cdn.com/content/v1/651eb4433b13e72c1034f375/369c5df0-5363-4827-b041-1add0367f447/PBB+long+logo.png?format=1500w"

st.markdown(
    f"<div style='display:flex;align-items:center;gap:10px'><img src='{PIONEER_LOGO}' height='40'><h2>Pioneer Ticketing</h2></div>",
    unsafe_allow_html=True,
)

# ---------------- Google Sheets helpers ----------------
def build_candidate_csv_urls(sheet_url: str) -> list[str]:
    urls = []
    m = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', sheet_url)
    if not m: return urls
    sid = m.group(1)
    urls.append(f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv")
    return urls

def fetch_customers_from_sheet(sheet_url: str) -> pd.DataFrame:
    for u in build_candidate_csv_urls(sheet_url):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req) as r:
                return pd.read_csv(io.StringIO(r.read().decode("utf-8")))
        except Exception:
            pass
    raise ValueError("Failed to read sheet.")

def normalize_customer_df(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip().lower() for c in df.columns]
    acct_col = next((c for c in df.columns if "account" in c or "acct" in c), None)
    name_col = next((c for c in df.columns if "name" in c), None)
    phone_col = next((c for c in df.columns if "contact" in c or "phone" in c or "tel" in c), None)
    mapping = {}
    if acct_col: mapping[acct_col] = "account_number"
    if name_col: mapping[name_col] = "name"
    if phone_col: mapping[phone_col] = "phone"
    df = df.rename(columns=mapping)
    for key in ["account_number", "name", "phone"]:
        if key not in df.columns:
            df[key] = ""
    return df[["account_number", "name", "phone"]]

def upsert_customers(db, df):
    """Upsert customers safely even if data types vary (string, float, NaN)."""
    ins = upd = 0

    for _, r in df.iterrows():
        # Convert all fields to strings and strip safely
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


# ---------------- Pages ----------------
def page_dashboard(db):
    rows = db.query(Ticket).options(joinedload(Ticket.events)).order_by(Ticket.created_at.desc()).all()
    data = []
    now = datetime.utcnow()
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
            "Description": t.description or "-",
        })
    if data:
        st.write(pd.DataFrame(data).to_html(escape=False, index=False), unsafe_allow_html=True)
    else:
        st.info("No tickets yet.")

def page_new_ticket(db):
    st.subheader("➕ New Ticket")
    acct = st.text_input("Account Number (search)")
    name = st.text_input("Customer Name (search)")

    matches = []
    if acct.strip():
        matches = db.query(Customer).filter(Customer.account_number.ilike(f"%{acct}%")).all()
    elif name.strip():
        matches = db.query(Customer).filter(Customer.name.ilike(f"%{name}%")).limit(20).all()

    if matches:
        labels = [f"{c.name} — {c.account_number} — {c.phone}" for c in matches]
        sel = st.selectbox("Select a customer", [""] + labels)
        if sel:
            c = matches[labels.index(sel) - 1]
            st.session_state["new_acct"] = c.account_number
            st.session_state["new_name"] = c.name
            st.session_state["new_phone"] = c.phone
            st.success(f"Loaded: {c.name} ({c.account_number})")

    customer_name = st.text_input("Customer Name", key="new_name")
    account_number = st.text_input("Account Number", key="new_acct")
    phone = st.text_input("Phone", key="new_phone")
    service_type = st.selectbox("Service Type", ["Fiber","DSL","Wireless","TV","Voice","Other"])
    call_reason = st.selectbox("Reason", ["outage","repair","billing","upgrade","cancel","other"])
    priority = st.selectbox("Priority", PRIORITY_ORDER, 1)
    description = st.text_area("Description", height=120)
    assigned_to = st.selectbox("Assign To", ["Unassigned","Billing","Support","Sales","BJ","Megan","Billy","Gillian","Gabby","Chuck","Aidan"])

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
st.info("Returning to dashboard...")
st.session_state["new_acct"] = ""
st.session_state["new_name"] = ""
st.session_state["new_phone"] = ""
st.query_params.clear()
st.success(f"✅ Created {t.ticket_key}")

        # --- Return to dashboard safely ---
        import time
        time.sleep(1.5)  # show success briefly
        st.query_params.clear()
        st.rerun()


def page_customers_admin():
    st.subheader("👥 Customers")
    st.caption("Preview and Import customer data from Google Sheets.")

    sheet_url = st.text_input(
        "Google Sheet URL",
        "https://docs.google.com/spreadsheets/d/1ywqLJIzydhifdUjX9Zo03B536LEUhH483hRAazT3zV8/edit?usp=sharing"
    )
    c1, c2 = st.columns(2)
    with c1:
        preview = st.button("🔎 Preview")
    with c2:
        import_btn = st.button("⬇️ Import / Upsert")

    if preview or import_btn:
        try:
            df_raw = fetch_customers_from_sheet(sheet_url)
            df_norm = normalize_customer_df(df_raw)
            if df_norm.empty:
                st.warning("No customer data found in sheet.")
            else:
                st.dataframe(df_norm.head(20))
                if import_btn:
                    with next(get_db()) as db:
                        ins, upd = upsert_customers(db, df_norm)
                    st.success(f"✅ Import complete — {ins} new, {upd} updated customers.")
        except Exception as e:
            st.error(f"❌ Error reading sheet: {e}")

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

ticket_key = st.query_params.get("ticket")
if ticket_key:
    with next(get_db()) as db:
        page_ticket_detail(db, ticket_key)
else:
    st.info("👋 Logged in automatically as Admin")
    tabs = st.tabs(["Dashboard", "New Ticket", "Customers"])
    with tabs[0]:
        with next(get_db()) as db:
            page_dashboard(db)
    with tabs[1]:
        with next(get_db()) as db:
            page_new_ticket(db)
    with tabs[2]:
        page_customers_admin()
