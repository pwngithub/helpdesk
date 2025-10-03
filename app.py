# app.py
import os
import re
import io
import json
import hmac
import base64
import hashlib
import urllib.request
from datetime import datetime
from typing import Dict, List

import pandas as pd
import streamlit as st
import extra_streamlit_components as stx
from dotenv import load_dotenv
from sqlalchemy.orm import Session, joinedload

from db import engine, get_db
from schema import Base, Ticket, TicketEvent, Customer
from utils import compute_sla_due, fmt_dt
from constants import STATUS_ORDER, PRIORITY_ORDER, STATUS_COLOR, PRIORITY_COLOR

# ---------------- Bootstrap ----------------
load_dotenv()
TZ = os.getenv("TZ", "America/New_York")
Base.metadata.create_all(bind=engine)
st.set_page_config(page_title="Pioneer Ticketing", page_icon="🎫", layout="wide")

# ---------------- Branding / Styles ----------------
PIONEER_LOGO = (
    "https://images.squarespace-cdn.com/content/v1/651eb4433b13e72c1034f375/"
    "369c5df0-5363-4827-b041-1add0367f447/PBB+long+logo.png?format=1500w"
)

def load_css(file_name):
    with open(file_name) as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

load_css("style.css")
st.markdown(
    f"""
    <div class="pioneer-header">
        <img src="{PIONEER_LOGO}" alt="Pioneer Broadband" style="height:42px;">
        <h2>ISP Ticketing</h2>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------- Users & Groups ----------------
USER_FILE = "users.json"
def load_users():
    if os.path.exists(USER_FILE):
        with open(USER_FILE, "r") as f:
            return json.load(f)
    # This default is for first-time setup only. You should create users.json
    return {"Admin": {"password": "CHANGE_ME", "group": "Admin"}}

USERS = load_users()
ASSIGNEES = ["Unassigned"] + list(USERS.keys())

def hash_password(password: str) -> str:
    """Hashes a password with a salt."""
    salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return base64.b64encode(salt + pwd_hash).decode('ascii').strip()

def verify_password(stored_password: str, provided_password: str) -> bool:
    """Verifies a stored password against one provided by user"""
    try:
        decoded_password = base64.b64decode(stored_password)
        salt = decoded_password[:16]
        stored_hash = decoded_password[16:]
        pwd_hash = hashlib.pbkdf2_hmac('sha256', provided_password.encode('utf-8'), salt, 100000)
        return hmac.compare_digest(pwd_hash, stored_hash)
    except Exception:
        return False

# ---------------- Auth cookie ----------------
cookie_manager = stx.CookieManager()
# ... (The rest of the cookie functions remain exactly the same)
def _secret_key() -> bytes:
    try:
        sk = st.secrets["auth_secret"]
    except Exception:
        sk = os.getenv("AUTH_SECRET", "dev-insecure-secret")
    return sk.encode("utf-8")
def _sign_token(user: str, role: str, exp_ts: int) -> str:
    msg = f"{user}|{role}|{exp_ts}"
    sig = hmac.new(_secret_key(), msg.encode(), hashlib.sha256).hexdigest()
    b64 = base64.urlsafe_b64encode(msg.encode()).decode()
    return f"{b64}.{sig}"
def _verify_token(token: str):
    try:
        b64, sig = token.split(".", 1)
        msg = base64.urlsafe_b64decode(b64.encode()).decode()
        expected = hmac.new(_secret_key(), msg.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        user, role, exp_ts = msg.split("|")
        if int(exp_ts) < int(datetime.utcnow().timestamp()):
            return None
        return user, role
    except Exception:
        return None
def _write_auth_cookie(user: str, role: str, days: int = 7):
    from datetime import timedelta
    exp = datetime.utcnow() + timedelta(days=days)
    token = _sign_token(user, role, int(exp.timestamp()))
    cookie_manager.set("pioneer_auth", token, expires_at=exp, path="/", same_site="Lax", secure=False)
def _clear_auth_cookie():
    from datetime import timedelta
    try:
        cookie_manager.delete("pioneer_auth", path="/")
    except Exception:
        pass
    past = datetime.utcnow() - timedelta(days=1)
    cookie_manager.set("pioneer_auth", "", expires_at=past, path="/", same_site="Lax", secure=False)
def _read_auth_cookie():
    token = cookie_manager.get("pioneer_auth")
    if not token: return None
    return _verify_token(token)

# ---------------- Helpers ----------------
def badge(text, color): return f'<span class="badge {color}">{text}</span>'
# ... (sla_countdown, dataframe_with_badges, render_df_html helpers are the same)
def sla_countdown(now, due):
    if not due: return "-", "gray"
    hours = (due - now).total_seconds() / 3600
    if hours < 0: return f"{abs(int(hours))}h overdue", "red"
    if hours <= 4: return f"{int(hours)}h left", "orange"
    days = int(hours // 24)
    return (f"{days}d left", "green") if days >= 1 else (f"{int(hours)}h left", "green")
def dataframe_with_badges(rows):
    now = datetime.utcnow(); data = []
    for t in rows:
        sla_txt, sla_class = sla_countdown(now, t.sla_due)
        latest_note = t.description or "-"
        if getattr(t, "events", None):
            notes = [e for e in t.events if e.note]
            if notes: latest_note = sorted(notes, key=lambda e: e.created_at)[-1].note
        data.append({
            "Key": f'<a href="?ticket={t.ticket_key}" target="_self">{t.ticket_key}</a>',
            "Created": fmt_dt(t.created_at, TZ),
            "Customer": t.customer_name,
            "Acct #": t.account_number,
            "Phone": t.phone,
            "Status": badge(t.status, STATUS_COLOR.get(t.status, "gray")),
            "Priority": badge(t.priority, PRIORITY_COLOR.get(t.priority, "gray")),
            "Assigned": t.assigned_to or "-",
            "SLA": sla_txt,
            "Reason": t.call_reason,
            "Service": t.service_type,
            "Latest Note": latest_note,
        })
    return pd.DataFrame(data)
def render_df_html(df): st.write(df.to_html(escape=False, index=False), unsafe_allow_html=True)
def filter_by_role(query, role, user):
    if role == "Admin": return query
    return query.filter(Ticket.assigned_to == user)

# ... (Google Sheets helper functions are the same)
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
        except Exception: pass
    raise ValueError("Failed to read sheet.")
def normalize_customer_df(df: pd.DataFrame) -> pd.DataFrame:
    cols = {c: str(c).strip().lower() for c in df.columns}
    df = df.rename(columns=cols)
    rename_map = {"customer name":"name","account":"account_number","account number":"account_number"}
    df = df.rename(columns=rename_map)
    keep = ["account_number","name","phone","email","address","service_type","notes"]
    for k in keep:
        if k not in df.columns: df[k] = ""
    return df[keep]
def upsert_customers(db, df):
    ins=upd=0
    for _, r in df.iterrows():
        acct = (r.get("account_number") or "").strip()
        if not acct: continue
        c = db.query(Customer).filter(Customer.account_number==acct).first()
        if c:
            for f in ["name","phone","email","address","service_type","notes"]:
                val = (r.get(f) or "").strip()
                if val: setattr(c, f, val)
            upd+=1
        else:
            db.add(Customer(account_number=acct, name=r.get("name") or "", phone=r.get("phone") or ""))
            ins+=1
    db.commit()
    return ins,upd

# ---------------- Pages ----------------
def login():
    st.title("🔐 Pioneer Ticketing Login")
    user = st.selectbox("Employee", list(USERS.keys()))
    pw = st.text_input("Password", type="password")
    if st.button("Login"):
        if user in USERS and verify_password(USERS[user]["password"], pw):
            st.session_state["user"] = user
            st.session_state["role"] = USERS[user]["group"]
            _write_auth_cookie(user, USERS[user]["group"])
            st.session_state.pop("force_login", None)
            st.rerun()
        else:
            st.error("Invalid username or password")

def page_dashboard(db, user, role):
    # Eager-load events to prevent N+1 queries
    q = filter_by_role(db.query(Ticket).options(joinedload(Ticket.events)), role, user)
    rows = q.order_by(Ticket.created_at.desc()).all()
    render_df_html(dataframe_with_badges(rows))

def page_new_ticket(db, user):
    st.subheader("Create New Ticket")
    # ... (rest of new ticket page is the same)
    acct = st.text_input("Account Number (search)", key="lookup_acct")
    name = st.text_input("Customer Name (search)", key="lookup_name")
    matches = []
    if acct.strip():
        matches = db.query(Customer).filter(Customer.account_number.ilike(f"%{acct}%")).all()
    elif name.strip():
        matches = db.query(Customer).filter(Customer.name.ilike(f"%{name}%")).limit(20).all()
    if matches:
        labels = [f"{c.name} — {c.account_number}" for c in matches]
        sel = st.selectbox("Select customer", [""]+labels)
        if sel:
            c = matches[labels.index(sel)-1]
            st.session_state["new_acct"] = c.account_number
            st.session_state["new_name"] = c.name
            st.session_state["new_phone"] = c.phone
    # fields
    customer_name = st.text_input("Customer Name", key="new_name")
    account_number = st.text_input("Account Number", key="new_acct")
    phone = st.text_input("Phone", key="new_phone")
    service_type = st.selectbox("Service Type", ["Fiber","DSL","Wireless","TV","Voice","Other"])
    call_reason = st.selectbox("Reason", ["outage","repair","billing","upgrade","cancel","other"])
    priority = st.selectbox("Priority", PRIORITY_ORDER, 1)
    description = st.text_area("Description", height=120)
    assigned_to = st.selectbox("Assign To", ASSIGNEES)
    if st.button("Create Ticket"):
        created_at = datetime.utcnow()
        t = Ticket(ticket_key=f"TCK-{int(created_at.timestamp())}", created_at=created_at,
                   customer_name=customer_name, account_number=account_number, phone=phone,
                   service_type=service_type, call_reason=call_reason,
                   description=description, status="Open", priority=priority,
                   assigned_to=assigned_to, sla_due=compute_sla_due(priority, created_at))
        db.add(t); db.commit(); st.success(f"✅ Created {t.ticket_key}")

def page_customers_admin():
    st.subheader("👥 Customers (Admin Only)")
    sheet_url = st.text_input("Google Sheet URL", os.getenv("GOOGLE_SHEET_URL", ""))
    if st.button("Import"):
        try:
            df = normalize_customer_df(fetch_customers_from_sheet(sheet_url))
            with next(get_db()) as db: ins,upd = upsert_customers(db, df)
            st.success(f"Imported: {ins} new, {upd} updated")
        except Exception as e: st.error(str(e))

def page_ticket_detail(db: Session, ticket_key: str, actor: str):
    """Displays and manages a single ticket."""
    ticket = db.query(Ticket).options(joinedload(Ticket.events)).filter(Ticket.ticket_key == ticket_key).first()

    if not ticket:
        st.error(f"Ticket {ticket_key} not found.")
        if st.button("← Back to Dashboard"):
            st.query_params.clear()
            st.rerun()
        return

    st.title(f"Ticket: {ticket.ticket_key}")
    st.markdown(f"**Customer:** {ticket.customer_name} (`{ticket.account_number}`)")
    st.link_button("← Back to Dashboard", "/")

    # ----- UPDATE FORMS -----
    col1, col2, col3 = st.columns(3)
    with col1:
        new_status = st.selectbox("Status", options=STATUS_ORDER, index=STATUS_ORDER.index(ticket.status))
        if new_status != ticket.status:
            ticket.status = new_status
            db.add(TicketEvent(ticket_id=ticket.id, actor=actor, action=f"Status changed to {new_status}"))
            db.commit()
            st.rerun()
    with col2:
        new_priority = st.selectbox("Priority", options=PRIORITY_ORDER, index=PRIORITY_ORDER.index(ticket.priority))
        if new_priority != ticket.priority:
            ticket.priority = new_priority
            ticket.sla_due = compute_sla_due(new_priority, ticket.created_at)
            db.add(TicketEvent(ticket_id=ticket.id, actor=actor, action=f"Priority changed to {new_priority}"))
            db.commit()
            st.rerun()
    with col3:
        current_idx = ASSIGNEES.index(ticket.assigned_to) if ticket.assigned_to in ASSIGNEES else 0
        new_assignee = st.selectbox("Assigned To", options=ASSIGNEES, index=current_idx)
        if new_assignee != ticket.assigned_to:
            ticket.assigned_to = new_assignee
            db.add(TicketEvent(ticket_id=ticket.id, actor=actor, action=f"Assigned to {new_assignee}"))
            db.commit()
            st.rerun()

    # ----- ADD A NOTE -----
    with st.form("add_note_form"):
        note_text = st.text_area("Add a new note or event log:")
        submitted = st.form_submit_button("Add Note")
        if submitted and note_text:
            db.add(TicketEvent(ticket_id=ticket.id, actor=actor, action="Note added", note=note_text))
            db.commit()
            st.success("Note added!")
            st.rerun()

    # ----- EVENT HISTORY -----
    st.subheader("History & Events")
    events = sorted(ticket.events, key=lambda e: e.created_at, reverse=True)
    for event in events:
        with st.container(border=True):
            st.markdown(f"**{event.actor}** · _{fmt_dt(event.created_at)}_")
            st.caption(f"Action: {event.action}")
            if event.note:
                st.markdown(event.note)

# ---------------- App ----------------
force_login = st.session_state.get("force_login", False)
cookie_auth = None if force_login else _read_auth_cookie()
if cookie_auth and "user" not in st.session_state:
    st.session_state["user"], st.session_state["role"] = cookie_auth
USER = st.session_state.get("user"); ROLE = st.session_state.get("role")

query_params = st.query_params.to_dict()
ticket_key_to_view = query_params.get("ticket")

if not USER or not ROLE:
    login()
elif ticket_key_to_view:
    with next(get_db()) as db:
        page_ticket_detail(db, ticket_key_to_view, USER)
else:
    st.info(f"👋 Logged in as {USER} ({ROLE})")
    if st.button("Logout"):
        _clear_auth_cookie()
        for k in ("user", "role"): st.session_state.pop(k, None)
        st.session_state["force_login"] = True
        st.rerun()

    admin_tabs = ["Dashboard", "New Ticket", "Customers"]
    user_tabs = ["Dashboard", "New Ticket"]
    tabs_to_show = admin_tabs if ROLE == "Admin" else user_tabs
    
    tabs = st.tabs(tabs_to_show)
    with tabs[0]:
        with next(get_db()) as db: page_dashboard(db, USER, ROLE)
    with tabs[1]:
        with next(get_db()) as db: page_new_ticket(db, USER)
    if ROLE == "Admin":
        with tabs[2]: page_customers_admin()
