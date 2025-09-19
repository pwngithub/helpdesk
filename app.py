# app.py
import os
import re
import io
import json
import hmac
import base64
import hashlib
import urllib.request
from datetime import datetime, timedelta
from typing import List, Tuple, Dict, Optional

import pandas as pd
import streamlit as st
import extra_streamlit_components as stx
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from db import engine, get_db
from schema import Base, Ticket, TicketEvent, Customer
from utils import compute_sla_due, fmt_dt

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

CUSTOM_CSS = """
<style>
section.main { background: #F5F5F5 !important; }
.pioneer-header { display:flex; align-items:center; gap:14px; background:#002856;
  padding:10px 14px; border-radius:10px; margin:10px 0 16px 0; }
.pioneer-header h2 { color:white; margin:0; font-size:22px; }
.stButton>button { background:#3BAFDA; color:white; border:none; border-radius:10px; font-weight:600; padding:8px 14px; }
.stButton>button:hover { background:#002856; }
[data-testid="stMetricValue"] { color:#7AC143; font-weight:700; }
.badge { display:inline-block; padding:3px 8px; border-radius:999px; font-size:12px; font-weight:700; color:white; }
.badge.gray{ background:#6b7280; }
.badge.blue{ background:#3BAFDA; }
.badge.orange{ background:#f59e0b; }
.badge.red{ background:#ef4444; }
.badge.green{ background:#10b981; }
.badge.purple{ background:#8b5cf6; }
.badge.yellow{ background:#fbbf24; color:#111827; }
.stDataFrame { border:1px solid #e5e7eb; border-radius:10px; background:white; }
.small-note { color:#6b7280; font-size:12px; }
.overdue { color:#ef4444; font-weight:700; }
.almost { color:#f59e0b; font-weight:700; }
.ok { color:#10b981; font-weight:700; }
table { table-layout: auto; width: 100%; }
td { white-space: normal !important; word-wrap: break-word !important; max-width: 520px; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
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
DEFAULT_USERS: Dict[str, Dict[str, str]] = {
    "Admin": {"password": "admin123", "group": "Admin"},
    "Chuck": {"password": "pass123", "group": "Support"},
    "Aidan": {"password": "pass123", "group": "Support"},
    "Billy": {"password": "pass123", "group": "Support"},
    "Gabby": {"password": "pass123", "group": "Billing/Sales"},
    "Gillian": {"password": "pass123", "group": "Billing/Sales"},
    "Megan": {"password": "pass123", "group": "Billing/Sales"},
}
def load_users():
    if os.path.exists(USER_FILE):
        with open(USER_FILE, "r") as f:
            return json.load(f)
    return DEFAULT_USERS
def save_users(users):
    with open(USER_FILE, "w") as f:
        json.dump(users, f, indent=2)
def load_groups(users):
    groups: Dict[str, List[str]] = {}
    for name, info in users.items():
        grp = info.get("group", "Support")
        groups.setdefault(grp, []).append(name)
    groups["Admin"] = list(users.keys())
    return groups

# ---------------- Auth cookie ----------------
cookie_manager = stx.CookieManager()
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
    exp = datetime.utcnow() + timedelta(days=days)
    token = _sign_token(user, role, int(exp.timestamp()))
    cookie_manager.set("pioneer_auth", token, expires_at=exp, path="/", same_site="Lax", secure=False)
def _clear_auth_cookie():
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

# ---------------- Constants ----------------
STATUS_ORDER = ["Open", "In Progress", "Escalated", "On Hold", "Resolved", "Closed"]
PRIORITY_ORDER = ["Low", "Medium", "High", "Critical"]
STATUS_COLOR = {"Open":"blue","In Progress":"purple","Escalated":"red","On Hold":"orange","Resolved":"green","Closed":"gray"}
PRIORITY_COLOR = {"Low":"gray","Medium":"blue","High":"orange","Critical":"red"}
ASSIGNEES = ["All", "Billing", "Support", "Sales", "BJ", "Megan", "Billy", "Gillian", "Gabby", "Chuck", "Aidan"]

# ---------------- Helpers ----------------
def badge(text, color): return f'<span class="badge {color}">{text}</span>'
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
    users = load_users()
    user = st.selectbox("Employee", list(users.keys()))
    pw = st.text_input("Password", type="password")
    if st.button("Login"):
        if user in users and pw == users[user]["password"]:
            st.session_state["user"] = user
            st.session_state["role"] = users[user]["group"]
            _write_auth_cookie(user, users[user]["group"])
            st.session_state.pop("force_login", None)
            st.rerun()
        else: st.error("Invalid login")

def page_dashboard(db, user, role):
    q = filter_by_role(db.query(Ticket), role, user)
    rows = q.order_by(Ticket.created_at.desc()).all()
    render_df_html(dataframe_with_badges(rows))

def page_new_ticket(db, user):
    st.subheader("Create New Ticket")
    # live lookup
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
    assigned_to = st.selectbox("Assign To", [a for a in ASSIGNEES[1:]])
    if st.button("Create Ticket"):
        created_at = datetime.utcnow()
        t = Ticket(ticket_key=f"TCK-{int(created_at.timestamp())}", created_at=created_at,
                   customer_name=customer_name, account_number=account_number, phone=phone,
                   service_type=service_type, call_reason=call_reason,
                   description=description, status="Open", priority=priority,
                   assigned_to=assigned_to, sla_due=compute_sla_due(priority, created_at))
        db.add(t); db.commit(); st.success(f"✅ Created {t.ticket_key}")

def page_customers_admin(url=""):
    st.subheader("👥 Customers (Admin Only)")
    sheet_url = st.text_input("Google Sheet URL", url)
    if st.button("Import"):
        try:
            df = normalize_customer_df(fetch_customers_from_sheet(sheet_url))
            with next(get_db()) as db: ins,upd = upsert_customers(db, df)
            st.success(f"Imported: {ins} new, {upd} updated")
        except Exception as e: st.error(str(e))

# ---------------- App ----------------
force_login = st.session_state.get("force_login", False)
cookie_auth = None if force_login else _read_auth_cookie()
if cookie_auth and "user" not in st.session_state:
    st.session_state["user"], st.session_state["role"] = cookie_auth
USER = st.session_state.get("user"); ROLE = st.session_state.get("role")

if not USER or not ROLE:
    login()
else:
    st.info(f"👋 Logged in as {USER} ({ROLE})")
    if st.button("Logout"):
        _clear_auth_cookie()
        for k in ("user","role"): st.session_state.pop(k, None)
        st.session_state["force_login"]=True
        st.rerun()
    if ROLE=="Admin":
        tabs=st.tabs(["Dashboard","New Ticket","Customers"])
    else:
        tabs=st.tabs(["Dashboard","New Ticket"])
    with tabs[0]:
        with next(get_db()) as db: page_dashboard(db, USER, ROLE)
    with tabs[1]:
        with next(get_db()) as db: page_new_ticket(db, USER)
    if ROLE=="Admin":
        with tabs[2]: page_customers_admin("https://docs.google.com/spreadsheets/d/1ywqLJIzydhifdUjX9Zo03B536LEUhH483hRAazT3zV8/edit?usp=sharing")
