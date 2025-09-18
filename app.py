import os
import json
from datetime import datetime, timedelta
from typing import List, Tuple

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from db import engine, get_db
from schema import Base, Ticket, TicketEvent
from utils import compute_sla_due, fmt_dt

# ---------- Bootstrap ----------
load_dotenv()
TZ = os.getenv("TZ", "America/New_York")
Base.metadata.create_all(bind=engine)

st.set_page_config(page_title="Pioneer Ticketing", page_icon="🎫", layout="wide")

# ---------- Branding / Styles ----------
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
td { white-space: normal !important; word-wrap: break-word !important; max-width: 400px; }
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

# ---------- Users & Groups ----------
USER_FILE = "users.json"

DEFAULT_USERS = {
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

USERS = load_users()

GROUP_MEMBERS = {
    "Support": ["Chuck", "Aidan", "Billy"],
    "Billing/Sales": ["Gabby", "Gillian", "Megan"],
    "Admin": list(USERS.keys()),  # Admin sees all
}

STATUS_ORDER = ["Open", "In Progress", "Escalated", "On Hold", "Resolved", "Closed"]
PRIORITY_ORDER = ["Low", "Medium", "High", "Critical"]

STATUS_COLOR = {
    "Open": "blue",
    "In Progress": "purple",
    "Escalated": "red",
    "On Hold": "orange",
    "Resolved": "green",
    "Closed": "gray",
}
PRIORITY_COLOR = {"Low": "gray", "Medium": "blue", "High": "orange", "Critical": "red"}

ASSIGNEES = [
    "All", "Billing", "Support", "Sales", "BJ", "Megan",
    "Billy", "Gillian", "Gabby", "Chuck", "Aidan"
]

# ---------- Login ----------
def login():
    st.title("🔐 Pioneer Ticketing Login")
    user = st.selectbox("Employee", list(USERS.keys()))
    pw = st.text_input("Password", type="password")
    if st.button("Login"):
        if user in USERS and pw == USERS[user]["password"]:
            st.session_state["user"] = user
            st.session_state["role"] = USERS[user]["group"]
            st.success(f"Welcome {user} ({USERS[user]['group']})")
            st.rerun()
        else:
            st.error("Invalid login")

# ---------- Helpers ----------
def badge(text: str, color: str) -> str:
    return f'<span class="badge {color}">{text}</span>'

def sla_countdown(now: datetime, due: datetime | None) -> Tuple[str, str]:
    if not due:
        return "-", "gray"
    delta = due - now
    hours = delta.total_seconds() / 3600
    if hours < 0:
        return f"{abs(int(hours))}h overdue", "red"
    if hours <= 4:
        return f"{int(hours)}h left", "orange"
    days = int(hours // 24)
    if days >= 1:
        return f"{days}d left", "green"
    return f"{int(hours)}h left", "green"

def dataframe_with_badges(rows: List[Ticket]) -> pd.DataFrame:
    now = datetime.utcnow()
    data = []
    for t in rows:
        sla_txt, sla_class = sla_countdown(now, t.sla_due)
        latest_note = t.description or "-"
        if t.events:
            note_events = [e for e in t.events if e.note]
            if note_events:
                latest_note = sorted(note_events, key=lambda e: e.created_at)[-1].note
        data.append(
            {
                "Key": f'<a href="?ticket={t.ticket_key}">{t.ticket_key}</a>',
                "Created": fmt_dt(t.created_at, TZ),
                "Customer": t.customer_name,
                "Acct #": t.account_number,
                "Phone": t.phone,
                "Status": badge(t.status, STATUS_COLOR.get(t.status, "gray")),
                "Priority": badge(t.priority, PRIORITY_COLOR.get(t.priority, "gray")),
                "Assigned": t.assigned_to or "-",
                "SLA": f'<span class="{ "overdue" if sla_class=="red" else ("almost" if sla_class=="orange" else "ok") }">{sla_txt}</span>',
                "Reason": t.call_reason,
                "Service": t.service_type,
                "Latest Note": latest_note,
            }
        )
    return pd.DataFrame(data)

def render_df_html(df: pd.DataFrame):
    st.write(df.to_html(escape=False, index=False), unsafe_allow_html=True)

def filter_by_role(query, role, user):
    if role == "Admin":
        return query
    elif role in GROUP_MEMBERS:
        return query.filter(Ticket.assigned_to.in_(GROUP_MEMBERS[role]))
    else:
        return query.filter(Ticket.assigned_to == user)

# ---------- Pages ----------
# (Dashboard, New Ticket, Manage, Reports, Ticket Detail, User Management)
# --- [unchanged from earlier long version] ---

# ---------- App ----------
CURRENT_USER = st.session_state.get("user", None)
ROLE = st.session_state.get("role", None)

if not CURRENT_USER or not ROLE:
    login()
else:
    st.info(f"👋 Logged in as **{CURRENT_USER}** (Group: {ROLE})")
    if st.button("Logout"):
        st.session_state.clear()
        st.rerun()

    params = st.query_params
    if "ticket" in params:
        ticket_key = params["ticket"]
        with next(get_db()) as db:
            page_ticket_detail(db, ticket_key, CURRENT_USER, ROLE)
    else:
        if ROLE == "Admin":
            tabs = st.tabs(["📊 Dashboard","➕ New Ticket","🛠️ Manage","📈 Reports","👤 User Management"])
        else:
            tabs = st.tabs(["📊 Dashboard","➕ New Ticket","🛠️ Manage","📈 Reports"])

        with tabs[0]:
            with next(get_db()) as db:
                page_dashboard(db, CURRENT_USER, ROLE)

        with tabs[1]:
            with next(get_db()) as db:
                page_new_ticket(db, CURRENT_USER)

        with tabs[2]:
            with next(get_db()) as db:
                page_manage(db, CURRENT_USER, ROLE)

        with tabs[3]:
            with next(get_db()) as db:
                page_reports(db, CURRENT_USER, ROLE)

        if ROLE == "Admin":
            with tabs[4]:
                page_user_management()
