import os
import json
from datetime import datetime, timedelta
from typing import List, Tuple, Dict

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
td { white-space: normal !important; word-wrap: break-word !important; max-width: 480px; }
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

# ---------- Users & Groups (json-backed) ----------
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

def load_users() -> Dict[str, Dict[str, str]]:
    if os.path.exists(USER_FILE):
        with open(USER_FILE, "r") as f:
            return json.load(f)
    return DEFAULT_USERS

def save_users(users: Dict[str, Dict[str, str]]) -> None:
    with open(USER_FILE, "w") as f:
        json.dump(users, f, indent=2)

USERS = load_users()

def compute_group_members(users: Dict[str, Dict[str, str]]) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {}
    for name, info in users.items():
        grp = info.get("group", "Support")
        groups.setdefault(grp, []).append(name)
    # Admin should be able to see all users
    groups["Admin"] = list(users.keys())
    return groups

GROUP_MEMBERS = compute_group_members(USERS)

# Assignee choices (kept as earlier; includes departments and individual names you asked for)
ASSIGNEES = [
    "All", "Billing", "Support", "Sales", "BJ", "Megan",
    "Billy", "Gillian", "Gabby", "Chuck", "Aidan"
]

# ---------- Taxonomies ----------
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

# ---------- Login ----------
def login():
    st.title("🔐 Pioneer Ticketing Login")
    users = load_users()  # refresh in case JSON changed
    user = st.selectbox("Employee", list(users.keys()))
    pw = st.text_input("Password", type="password")
    if st.button("Login"):
        if user in users and pw == users[user]["password"]:
            st.session_state["user"] = user
            st.session_state["role"] = users[user]["group"]
            st.success(f"Welcome {user} ({users[user]['group']})")
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

def filter_by_role(query, role: str, user: str):
    if role == "Admin":
        return query
    allowed = set(GROUP_MEMBERS.get(role, []))
    return query.filter(Ticket.assigned_to.in_(allowed)) if allowed else query.filter(Ticket.assigned_to == user)

# ---------- Pages ----------
def page_dashboard(db: Session, current_user: str, role: str):
    q = filter_by_role(db.query(Ticket), role, current_user)
    total = q.count()
    active = q.filter(Ticket.status.in_(STATUS_ORDER[:4])).count()
    resolved = q.filter(Ticket.status.in_(["Resolved", "Closed"])).count()

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Tickets", total)
    c2.metric("Active Tickets", active)
    c3.metric("Resolved/Closed", resolved)

    with st.expander("Filters", expanded=True):
        f1, f2, f3, f4 = st.columns(4)
        statuses = f1.multiselect("Status", STATUS_ORDER,
                                  default=["Open","In Progress","Escalated","On Hold"],
                                  key="dash_status")
        priorities = f2.multiselect("Priority", PRIORITY_ORDER, key="dash_priority")
        assignee_filter = f3.selectbox("Assigned To", ["All"] + sorted(list({t.assigned_to for t in q})), key="dash_assignee")
        search = f4.text_input("Search (Key / Customer / Phone)", "", key="dash_search")

    if statuses: q = q.filter(Ticket.status.in_(statuses))
    if priorities: q = q.filter(Ticket.priority.in_(priorities))
    if assignee_filter and assignee_filter != "All":
        q = q.filter(Ticket.assigned_to == assignee_filter)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (Ticket.ticket_key.ilike(like)) |
            (Ticket.customer_name.ilike(like)) |
            (Ticket.phone.ilike(like))
        )

    rows = q.order_by(Ticket.created_at.desc()).all()
    render_df_html(dataframe_with_badges(rows))

def page_new_ticket(db: Session, current_user: str):
    st.subheader("Create New Ticket")
    # No form wrapper => Enter will NOT auto-submit
    customer_name = st.text_input("Customer Name", key="new_name")
    account_number = st.text_input("Account Number", key="new_acct")
    phone = st.text_input("Phone", key="new_phone")
    service_type = st.selectbox("Service Type", ["Fiber","DSL","Fixed Wireless","TV","Voice","Other"])
    call_source = st.selectbox("Call Source", ["phone","email","chat","walk-in"])
    call_reason = st.selectbox("Call Reason", ["outage","repair","billing","upgrade","cancel","new service","other"])
    priority = st.selectbox("Priority", PRIORITY_ORDER, index=1)
    description = st.text_area("Description / Notes", height=120)
    assigned_to = st.selectbox("Assign To", ASSIGNEES[1:])  # exclude "All"

    if st.button("Create Ticket", use_container_width=True):
        created_at = datetime.utcnow()
        t = Ticket(
            ticket_key=f"TCK-{int(created_at.timestamp())}",
            created_at=created_at,
            customer_name=customer_name.strip(),
            account_number=account_number.strip(),
            phone=phone.strip(),
            service_type=service_type,
            call_source=call_source,
            call_reason=call_reason,
            description=description.strip(),
            status="Open",
            priority=priority,
            assigned_to=assigned_to,
            sla_due=compute_sla_due(priority, created_at),
        )
        db.add(t); db.commit(); db.refresh(t)
        db.add(TicketEvent(ticket_id=t.id, actor=current_user, action="create", note="Ticket created")); db.commit()
        st.success(f"✅ Ticket created: {t.ticket_key}")

def page_manage(db: Session, current_user: str, role: str):
    st.subheader("Manage Tickets")
    q = filter_by_role(db.query(Ticket), role, current_user)
    glob_q = st.text_input("Global search (Key / Customer / Phone / Desc)", "", key="manage_search")
    if glob_q.strip():
        like = f"%{glob_q}%"
        q = q.filter(
            (Ticket.ticket_key.ilike(like))
            | (Ticket.customer_name.ilike(like))
            | (Ticket.phone.ilike(like))
            | (Ticket.description.ilike(like))
        )
    statuses = st.multiselect("Status", STATUS_ORDER, default=[], key="manage_status")
    if statuses:
        q = q.filter(Ticket.status.in_(statuses))
    rows = q.order_by(Ticket.created_at.desc()).limit(300).all()
    render_df_html(dataframe_with_badges(rows))

def page_reports(db: Session, current_user: str, role: str):
    st.subheader("Reports & Analytics")
    q = filter_by_role(db.query(Ticket), role, current_user)
    rows: List[Ticket] = q.order_by(Ticket.created_at.asc()).all()
    if not rows:
        st.info("No tickets yet."); return
    df = pd.DataFrame([{"created_at": t.created_at, "status": t.status} for t in rows])
    df["created_date"] = df["created_at"].dt.date
    last_30 = pd.date_range(datetime.utcnow().date() - timedelta(days=29), periods=30)
    by_day = df.groupby("created_date").size().reindex(last_30.date, fill_value=0)
    st.line_chart(by_day)

def page_ticket_detail(db: Session, ticket_key: str, current_user: str, role: str):
    t = db.query(Ticket).filter(Ticket.ticket_key == ticket_key).first()
    if not t:
        st.error("Ticket not found."); return
    if role != "Admin" and t.assigned_to not in GROUP_MEMBERS.get(role, []):
        st.error("⛔ You do not have permission to view this ticket."); return

    st.markdown(f"### 🎫 {t.ticket_key} — {t.customer_name}")
    st.write(f"**Created:** {fmt_dt(t.created_at, TZ)} | **SLA Due:** {fmt_dt(t.sla_due, TZ) if t.sla_due else '-'}")

    st.write("#### Ticket Description")
    st.markdown(f"> {t.description or '_No description provided._'}")

    note_events = [e for e in t.events if e.note]
    if note_events:
        with st.expander("📜 Show all notes"):
            for e in sorted(note_events, key=lambda ev: ev.created_at, reverse=True):
                st.markdown(f"- *{fmt_dt(e.created_at, TZ)}* **{e.actor}**: {e.note}")
    else:
        st.write("_No notes yet._")

    with st.form("update_ticket", clear_on_submit=False):
        c1, c2, c3 = st.columns(3)
        new_status = c1.selectbox("Status", STATUS_ORDER, index=STATUS_ORDER.index(t.status), key="detail_status")
        new_priority = c2.selectbox("Priority", PRIORITY_ORDER, index=PRIORITY_ORDER.index(t.priority), key="detail_priority")
        assignees_only_people = [a for a in ASSIGNEES[1:] if a not in ("Billing","Support","Sales","All")]
        default_idx = assignees_only_people.index(t.assigned_to) if t.assigned_to in assignees_only_people else 0
        new_assigned = c3.selectbox("Assigned To", assignees_only_people, index=default_idx, key="detail_assigned")

        new_note = st.text_area("Add Note", key="detail_note")
        submitted = st.form_submit_button("💾 Save Changes")

        if submitted:
            t.status = new_status
            t.priority = new_priority
            t.assigned_to = new_assigned
            db.commit()

            if new_note.strip():
                db.add(TicketEvent(ticket_id=t.id, actor=current_user, action="note", note=new_note.strip()))
                db.commit()

            st.success("✅ Ticket updated successfully!")
            st.query_params.clear()
            st.rerun()

    if note_events:
        st.write("#### Recent Notes")
        recent = sorted(note_events, key=lambda ev: ev.created_at, reverse=True)[:3]
        for e in recent:
            st.markdown(f"- *{fmt_dt(e.created_at, TZ)}* **{e.actor}**: {e.note}")

    if st.button("⬅ Back to Dashboard"):
        st.query_params.clear()
        st.rerun()

def page_user_management():
    st.subheader("👤 User Management (Admin Only)")
    users = load_users()

    st.write("### Current Users")
    st.table(pd.DataFrame([{"User": u, "Group": info["group"]} for u, info in users.items()]))

    st.write("### ➕ Add User")
    new_user = st.text_input("Username")
    new_pass = st.text_input("Password", type="password")
    new_group = st.selectbox("Group", ["Admin", "Support", "Billing/Sales"])
    if st.button("Add User"):
        if not new_user.strip():
            st.error("Username is required.")
        elif new_user in users:
            st.error("User already exists!")
        else:
            users[new_user] = {"password": new_pass or "pass123", "group": new_group}
            save_users(users)
            st.success(f"User {new_user} added.")
            st.rerun()

    st.write("### 🔑 Change Password")
    if users:
        sel_user = st.selectbox("Select User", list(users.keys()))
        new_pw = st.text_input("New Password", type="password")
        if st.button("Update Password"):
            users[sel_user]["password"] = new_pw or users[sel_user]["password"]
            save_users(users)
            st.success(f"Password for {sel_user} updated.")

    st.write("### ❌ Remove User")
    if users:
        del_user = st.selectbox("Delete User", list(users.keys()))
        if st.button("Delete User"):
            if del_user == "Admin":
                st.error("Cannot delete Admin account!")
            else:
                users.pop(del_user, None)
                save_users(users)
                st.success(f"User {del_user} deleted.")
                st.rerun()

# ---------- App ----------
CURRENT_USER = st.session_state.get("user", None)
ROLE = st.session_state.get("role", None)

if not CURRENT_USER or not ROLE:
    login()
else:
    # Recompute groups on each run in case Admin changed users.json
    USERS = load_users()
    GROUP_MEMBERS = compute_group_members(USERS)

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
