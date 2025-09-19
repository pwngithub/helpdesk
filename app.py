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

# ---------------- Users & Groups (json-backed) ----------------
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

def load_groups(users: Dict[str, Dict[str, str]]) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {}
    for name, info in users.items():
        grp = info.get("group", "Support")
        groups.setdefault(grp, []).append(name)
    groups["Admin"] = list(users.keys())
    return groups

USERS = load_users()
GROUP_MEMBERS = load_groups(USERS)

# ---------------- Auth cookie (HMAC-signed) ----------------
cookie_manager = stx.CookieManager()

def _secret_key() -> bytes:
    try:
        sk = st.secrets["auth_secret"]
    except Exception:
        sk = os.getenv("AUTH_SECRET", "dev-insecure-secret-change-me")
    return sk.encode("utf-8")

def _sign_token(user: str, role: str, exp_ts: int) -> str:
    msg = f"{user}|{role}|{exp_ts}"
    sig = hmac.new(_secret_key(), msg.encode(), hashlib.sha256).hexdigest()
    b64 = base64.urlsafe_b64encode(msg.encode()).decode()
    return f"{b64}.{sig}"

def _verify_token(token: str) -> Optional[Tuple[str, str]]:
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
    cookie_manager.set(
        "pioneer_auth", token,
        expires_at=exp,
        key="auth_set_cookie",
        path="/",
        same_site="Lax",
        secure=False  # set True for HTTPS-only
    )

def _clear_auth_cookie():
    try:
        cookie_manager.delete("pioneer_auth", key="auth_delete_cookie", path="/")
    except Exception:
        pass
    past = datetime.utcnow() - timedelta(days=1)
    cookie_manager.set(
        "pioneer_auth", "",
        expires_at=past,
        key="auth_clear_cookie",
        path="/",
        same_site="Lax",
        secure=False
    )

def _read_auth_cookie():
    token = cookie_manager.get("pioneer_auth")
    if not token:
        return None
    return _verify_token(token)

# ---------------- Taxonomies ----------------
STATUS_ORDER = ["Open", "In Progress", "Escalated", "On Hold", "Resolved", "Closed"]
PRIORITY_ORDER = ["Low", "Medium", "High", "Critical"]
STATUS_COLOR = {"Open":"blue","In Progress":"purple","Escalated":"red","On Hold":"orange","Resolved":"green","Closed":"gray"}
PRIORITY_COLOR = {"Low":"gray","Medium":"blue","High":"orange","Critical":"red"}
ASSIGNEES = ["All", "Billing", "Support", "Sales", "BJ", "Megan", "Billy", "Gillian", "Gabby", "Chuck", "Aidan"]

# ---------------- Helpers ----------------
def badge(text: str, color: str) -> str:
    return f'<span class="badge {color}">{text}</span>'

def sla_countdown(now: datetime, due: Optional[datetime]) -> Tuple[str, str]:
    if not due:
        return "-", "gray"
    hours = (due - now).total_seconds() / 3600
    if hours < 0:
        return f"{abs(int(hours))}h overdue", "red"
    if hours <= 4:
        return f"{int(hours)}h left", "orange"
    days = int(hours // 24)
    if days >= 1:
        return f"{days}d left", "green"
    return f"{int(hours)}h left", "green"

def dataframe_with_badges(rows: List['Ticket']) -> pd.DataFrame:
    now = datetime.utcnow()
    data = []
    for t in rows:
        sla_txt, sla_class = sla_countdown(now, t.sla_due)
        class_name = "overdue" if sla_class == "red" else ("almost" if sla_class == "orange" else "ok")
        latest_note = t.description or "-"
        if getattr(t, "events", None):
            note_events = [e for e in t.events if e.note]
            if note_events:
                latest_note = sorted(note_events, key=lambda e: e.created_at)[-1].note
        data.append({
            "Key": f'<a href="?ticket={t.ticket_key}" target="_self">{t.ticket_key}</a>',
            "Created": fmt_dt(t.created_at, TZ),
            "Customer": t.customer_name,
            "Acct #": t.account_number,
            "Phone": t.phone,
            "Status": badge(t.status, STATUS_COLOR.get(t.status, "gray")),
            "Priority": badge(t.priority, PRIORITY_COLOR.get(t.priority, "gray")),
            "Assigned": t.assigned_to or "-",
            "SLA": f'<span class="{class_name}">{sla_txt}</span>',
            "Reason": t.call_reason,
            "Service": t.service_type,
            "Latest Note": latest_note,
        })
    return pd.DataFrame(data)

def render_df_html(df: pd.DataFrame):
    st.write(df.to_html(escape=False, index=False), unsafe_allow_html=True)

def filter_by_role(query, role: str, user: str):
    if role == "Admin":
        return query
    allowed = set(GROUP_MEMBERS.get(role, []))
    return query.filter(Ticket.assigned_to.in_(allowed)) if allowed else query.filter(Ticket.assigned_to == user)

def set_qp(key: Optional[str], value: Optional[str] = None):
    try:
        if key is None:
            st.query_params.clear()
        else:
            st.query_params[key] = value
    except Exception:
        if key is None:
            st.experimental_set_query_params()
        else:
            st.experimental_set_query_params(**{key: value})

def _get_ticket_from_query() -> Optional[str]:
    try:
        params = st.query_params
        val = params.get("ticket")
    except Exception:
        params = st.experimental_get_query_params()
        val = params.get("ticket")
    if isinstance(val, list):
        return val[0] if val else None
    return val

def open_ticket_picker(rows: List['Ticket'], key_prefix: str):
    options = [f"{t.ticket_key} — {t.customer_name} — {t.phone or ''}" for t in rows]
    if not options:
        return
    sel = st.selectbox("Open a ticket…", [""] + options, index=0, key=f"{key_prefix}_open_select")
    if sel:
        ticket_key = sel.split(" — ")[0]
        set_qp("ticket", ticket_key)
        st.rerun()

# ---------------- Google Sheets Import (robust) ----------------
def build_candidate_csv_urls(sheet_url: str) -> list[str]:
    urls = []
    if "/spreadsheets/d/e/" in sheet_url and "pub" in sheet_url:
        if "output=csv" in sheet_url:
            urls.append(sheet_url)
        else:
            if "output=" in sheet_url:
                urls.append(re.sub(r"output=[^&]+", "output=csv", sheet_url))
            else:
                sep = "&" if "?" in sheet_url else "?"
                urls.append(f"{sheet_url}{sep}output=csv")
        return urls
    m = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', sheet_url)
    if not m:
        return urls
    sheet_id = m.group(1)
    mgid = re.search(r'gid=([0-9]+)', sheet_url)
    if mgid:
        gid = mgid.group(1)
        urls.append(f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}")
    urls.append(f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv")
    urls.append(f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid=0")
    return urls

def read_csv_from_url(url: str, timeout: int = 20) -> pd.DataFrame:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", errors="ignore")
    return pd.read_csv(io.StringIO(text))

def fetch_customers_from_sheet(sheet_url: str) -> pd.DataFrame:
    candidates = build_candidate_csv_urls(sheet_url)
    last_err = None
    for u in candidates:
        try:
            return read_csv_from_url(u)
        except Exception as e:
            last_err = e
    raise ValueError(
        "Could not download CSV from Google Sheets. "
        "Tips: (1) Click the correct sheet tab so the URL has `gid=`; "
        "(2) Share as 'Anyone with the link – Viewer'; "
        "(3) Or File → Share → Publish to the web → CSV and use that link. "
        f"Last error: {last_err}"
    )

def normalize_customer_df(df: pd.DataFrame) -> pd.DataFrame:
    cols = {c: str(c).strip().lower() for c in df.columns}
    df = df.rename(columns=cols)
    rename_map = {}
    for c in df.columns:
        if c in ["customer", "customer name", "name", "full name"]:
            rename_map[c] = "name"
        elif c in ["account", "acct", "account number", "account #", "acct #", "acct number"]:
            rename_map[c] = "account_number"
        elif c in ["phone", "phone number", "tel", "telephone"]:
            rename_map[c] = "phone"
        elif c in ["email", "e-mail", "mail"]:
            rename_map[c] = "email"
        elif c in ["address", "addr", "location"]:
            rename_map[c] = "address"
        elif c in ["service", "service type", "product", "plan"]:
            rename_map[c] = "service_type"
        elif c in ["notes", "note", "comments"]:
            rename_map[c] = "notes"
    df = df.rename(columns=rename_map)
    keep = ["account_number","name","phone","email","address","service_type","notes"]
    for k in keep:
        if k not in df.columns:
            df[k] = ""
    df["account_number"] = df["account_number"].astype(str).str.strip()
    df["name"] = df["name"].astype(str).str.strip()
    return df[keep]

def upsert_customers(db: Session, df: pd.DataFrame) -> tuple[int, int]:
    inserted = 0
    updated = 0
    for _, row in df.iterrows():
        acct = (row.get("account_number") or "").strip()
        if not acct or acct.lower() == "nan":
            continue
        c = db.query(Customer).filter(Customer.account_number == acct).first()
        if c:
            for f in ["name","phone","email","address","service_type","notes"]:
                val = (row.get(f) or "").strip()
                if val:
                    setattr(c, f, val)
            updated += 1
        else:
            db.add(Customer(
                account_number=acct,
                name=(row.get("name") or "").strip(),
                phone=(row.get("phone") or "").strip(),
                email=(row.get("email") or "").strip(),
                address=(row.get("address") or "").strip(),
                service_type=(row.get("service_type") or "").strip(),
                notes=(row.get("notes") or "").strip(),
            ))
            inserted += 1
    db.commit()
    return inserted, updated

# ---------------- Pages ----------------
def login():
    st.title("🔐 Pioneer Ticketing Login")
    users = load_users()
    user = st.selectbox("Employee", list(users.keys()), key="login_user")
    pw = st.text_input("Password", type="password", key="login_pw")
    if st.button("Login", key="login_btn"):
        if user in users and pw == users[user]["password"]:
            st.session_state["user"] = user
            st.session_state["role"] = users[user]["group"]
            _write_auth_cookie(user, users[user]["group"])
            st.session_state.pop("force_login", None)  # allow cookie restore next time
            st.success(f"Welcome {user} ({users[user]['group']})")
            st.rerun()
        else:
            st.error("Invalid login")

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
        statuses = f1.multiselect("Status", STATUS_ORDER, default=["Open","In Progress","Escalated","On Hold"], key="dash_status")
        priorities = f2.multiselect("Priority", PRIORITY_ORDER, key="dash_priority")
        assignees = sorted({t.assigned_to for t in q if t.assigned_to})
        assignee_filter = f3.selectbox("Assigned To", ["All"] + assignees, key="dash_assignee")
        search = f4.text_input("Search (Key / Customer / Phone)", "", key="dash_search")
    if statuses: q = q.filter(Ticket.status.in_(statuses))
    if priorities: q = q.filter(Ticket.priority.in_(priorities))
    if assignee_filter and assignee_filter != "All":
        q = q.filter(Ticket.assigned_to == assignee_filter)
    if search:
        like = f"%{search}%"
        q = q.filter((Ticket.ticket_key.ilike(like)) | (Ticket.customer_name.ilike(like)) | (Ticket.phone.ilike(like)))
    rows = q.order_by(Ticket.created_at.desc()).all()
    render_df_html(dataframe_with_badges(rows))
    open_ticket_picker(rows, "dash")

def page_new_ticket(db: Session, current_user: str):
    st.subheader("Create New Ticket")

    # ---------------- Live customer lookup ----------------
    with st.expander("Lookup existing customer", expanded=True):
        acct_search = st.text_input("Account Number", key="lookup_acct")
        name_search = st.text_input("Customer Name", key="lookup_name")

        matches = []
        if acct_search.strip():
            matches = db.query(Customer).filter(
                Customer.account_number.ilike(f"%{acct_search.strip()}%")
            ).all()
        elif name_search.strip():
            matches = db.query(Customer).filter(
                Customer.name.ilike(f"%{name_search.strip()}%")
            ).order_by(Customer.name.asc()).limit(20).all()

        if matches:
            if len(matches) == 1:
                c = matches[0]
                st.session_state["new_acct"] = c.account_number or ""
                st.session_state["new_name"] = c.name or ""
                st.session_state["new_phone"] = c.phone or ""
                st.success(f"Loaded customer: {c.name} ({c.account_number})")
            else:
                labels = [f"{c.name} — {c.account_number} — {c.phone}" for c in matches]
                sel = st.selectbox("Select a customer", labels, key="lookup_sel")
                if sel:
                    idx = labels.index(sel)
                    c = matches[idx]
                    st.session_state["new_acct"] = c.account_number or ""
                    st.session_state["new_name"] = c.name or ""
                    st.session_state["new_phone"] = c.phone or ""
                    st.success(f"Loaded customer: {c.name} ({c.account_number})")

    # ---------------- New Ticket form fields ----------------
    customer_name = st.text_input("Customer Name", key="new_name")
    account_number = st.text_input("Account Number", key="new_acct")
    phone = st.text_input("Phone", key="new_phone")
    service_type = st.selectbox("Service Type", ["Fiber","DSL","Fixed Wireless","TV","Voice","Other"])
    call_source = st.selectbox("Call Source", ["phone","email","chat","walk-in"])
    call_reason = st.selectbox("Call Reason", ["outage","repair","billing","upgrade","cancel","new service","other"])
    priority = st.selectbox("Priority", PRIORITY_ORDER, index=1)
    description = st.text_area("Description / Notes", height=120)
    assigned_to = st.selectbox("Assign To", [a for a in ASSIGNEES[1:]])

    if st.button("Create Ticket", use_container_width=True, key="create_ticket_btn"):
        created_at = datetime.utcnow()
        t = Ticket(
            ticket_key=f"TCK-{int(created_at.timestamp())}",
            created_at=created_at,
            customer_name=(st.session_state.get("new_name") or customer_name or "").strip(),
            account_number=(st.session_state.get("new_acct") or account_number or "").strip(),
            phone=(st.session_state.get("new_phone") or phone or "").strip(),
            service_type=service_type,
            call_source=call_source,
            call_reason=call_reason,
            description=(description or "").strip(),
            status="Open",
            priority=priority,
            assigned_to=assigned_to,
            sla_due=compute_sla_due(priority, created_at),
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        db.add(TicketEvent(ticket_id=t.id, actor=current_user, action="create", note="Ticket created"))
        db.commit()
        st.success(f"✅ Ticket created: {t.ticket_key}")



                
            if name:
                from sqlalchemy import func
                found = (qry.filter(func.lower(Customer.name).ilike(f"%{name.lower()}%"))
                           .order_by(Customer.name.asc()).limit(50).all())
                if len(found) == 0:
                    st.warning("No matching customer found. Refine the name.")
                elif len(found) == 1:
                    c = found[0]
                    st.session_state["new_acct"] = c.account_number or ""
                    st.session_state["new_name"] = c.name or ""
                    st.session_state["new_phone"] = c.phone or ""
                    st.success(f"Loaded customer: {c.name or c.account_number}")
                    st.rerun()
                else:
                    st.session_state["lookup_matches"] = [
                        {"id": c.id, "account_number": c.account_number or "", "name": c.name or "", "phone": c.phone or ""}
                        for c in found
                    ]
                    st.info(f"Found {len(found)} matches. Pick one below.")
        matches = st.session_state.get("lookup_matches", [])
        if isinstance(matches, list) and len(matches) > 1:
            labels = [f'{m["name"]} — {m["account_number"]} — {m["phone"]}' for m in matches]
            idx = st.selectbox("Select customer", options=list(range(len(matches))), format_func=lambda i: labels[i], key="lookup_pick_idx")
            if st.button("Use selected", key="use_selected_btn"):
                m = matches[idx]
                st.session_state["new_acct"] = m["account_number"]
                st.session_state["new_name"] = m["name"]
                st.session_state["new_phone"] = m["phone"]
                st.session_state.pop("lookup_matches", None)
                st.success(f'Loaded customer: {m["name"]} ({m["account_number"]})')
                st.rerun()
    customer_name = st.text_input("Customer Name", key="new_name")
    account_number = st.text_input("Account Number", key="new_acct")
    phone = st.text_input("Phone", key="new_phone")
    service_type = st.selectbox("Service Type", ["Fiber","DSL","Fixed Wireless","TV","Voice","Other"])
    call_source = st.selectbox("Call Source", ["phone","email","chat","walk-in"])
    call_reason = st.selectbox("Call Reason", ["outage","repair","billing","upgrade","cancel","new service","other"])
    priority = st.selectbox("Priority", PRIORITY_ORDER, index=1)
    description = st.text_area("Description / Notes", height=120)
    assigned_to = st.selectbox("Assign To", [a for a in ASSIGNEES[1:]])
    if st.button("Create Ticket", use_container_width=True, key="create_ticket_btn"):
        created_at = datetime.utcnow()
        t = Ticket(
            ticket_key=f"TCK-{int(created_at.timestamp())}",
            created_at=created_at,
            customer_name=(st.session_state.get("new_name") or customer_name or "").strip(),
            account_number=(st.session_state.get("new_acct") or account_number or "").strip(),
            phone=(st.session_state.get("new_phone") or phone or "").strip(),
            service_type=service_type,
            call_source=call_source,
            call_reason=call_reason,
            description=(description or "").strip(),
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
        q = q.filter((Ticket.ticket_key.ilike(like)) | (Ticket.customer_name.ilike(like)) | (Ticket.phone.ilike(like)) | (Ticket.description.ilike(like)))
    statuses = st.multiselect("Status", STATUS_ORDER, default=[], key="manage_status")
    if statuses: q = q.filter(Ticket.status.in_(statuses))
    rows = q.order_by(Ticket.created_at.desc()).limit(300).all()
    render_df_html(dataframe_with_badges(rows))
    open_ticket_picker(rows, "manage")

def page_reports(db: Session, current_user: str, role: str):
    st.subheader("Reports & Analytics")
    q = filter_by_role(db.query(Ticket), role, current_user)
    rows: List['Ticket'] = q.order_by(Ticket.created_at.asc()).all()
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
                db.add(TicketEvent(ticket_id=t.id, actor=current_user, action="note", note=new_note.strip())); db.commit()
            st.success("✅ Ticket updated successfully!")
            set_qp(None)
            st.rerun()
    if note_events:
        st.write("#### Recent Notes")
        recent = sorted(note_events, key=lambda ev: ev.created_at, reverse=True)[:3]
        for e in recent:
            st.markdown(f"- *{fmt_dt(e.created_at, TZ)}* **{e.actor}**: {e.note}")
    if st.button("⬅ Back to Dashboard"):
        set_qp(None)
        st.rerun()

def page_user_management():
    st.subheader("👤 User Management (Admin Only)")
    users = load_users()
    st.table(pd.DataFrame([{"User": u, "Group": info["group"]} for u, info in users.items()]))
    st.write("### ➕ Add User")
    new_user = st.text_input("Username", key="um_new_user")
    new_pass = st.text_input("Password", type="password", key="um_new_pass")
    new_group = st.selectbox("Group", ["Admin", "Support", "Billing/Sales"], key="um_new_group")
    if st.button("Add User", key="um_add_btn"):
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
        sel_user = st.selectbox("Select User", list(users.keys()), key="um_sel_user")
        new_pw = st.text_input("New Password", type="password", key="um_new_pw")
        if st.button("Update Password", key="um_upd_pw"):
            users[sel_user]["password"] = new_pw or users[sel_user]["password"]
            save_users(users)
            st.success(f"Password for {sel_user} updated.")
    st.write("### ❌ Remove User")
    if users:
        del_user = st.selectbox("Delete User", list(users.keys()), key="um_del_user")
        if st.button("Delete User", key="um_del_btn"):
            if del_user == "Admin":
                st.error("Cannot delete Admin account!")
            else:
                users.pop(del_user, None)
                save_users(users)
                st.success(f"User {del_user} deleted.")
                st.rerun()

def page_customers_admin(default_url: str = ""):
    st.subheader("👥 Customers (Admin Only)")
    st.caption("Import/Sync customers from Google Sheets and browse them here.")
    st.info("Tips: Copy a URL with **gid=...** for the tab you want, or publish to CSV. You can also upload a CSV.")
    sheet_url = st.text_input("Google Sheet URL (viewable or published CSV link)", value=default_url or "", key="cust_sheet_url")
    c1, c2 = st.columns(2)
    with c1: preview = st.button("🔎 Preview", key="cust_preview")
    with c2: do_import = st.button("⬇️ Import / Upsert", key="cust_import")
    uploaded = st.file_uploader("…or upload a CSV file", type=["csv"], key="cust_file")
    df_norm = None
    if uploaded is not None:
        try:
            df_raw = pd.read_csv(uploaded)
            df_norm = normalize_customer_df(df_raw)
            st.success("CSV uploaded.")
        except Exception as e:
            st.error(f"Failed to parse uploaded CSV: {e}")
    if sheet_url and (preview or do_import) and df_norm is None:
        try:
            df_raw = fetch_customers_from_sheet(sheet_url)
            df_norm = normalize_customer_df(df_raw)
        except Exception as e:
            st.error(f"Failed to read sheet: {e}")
    if df_norm is not None:
        st.write("**Preview (first 20 rows after normalization):**")
        st.dataframe(df_norm.head(20))
        if do_import:
            with next(get_db()) as db:
                ins, upd = upsert_customers(db, df_norm)
            st.success(f"✅ Import complete: {ins} inserted, {upd} updated")

# ---------------- App ----------------
# Force-login flag prevents cookie restore until a new login succeeds.
force_login = st.session_state.get("force_login", False)
cookie_auth = None if force_login else _read_auth_cookie()

if cookie_auth and "user" not in st.session_state:
    st.session_state["user"], st.session_state["role"] = cookie_auth

CURRENT_USER = st.session_state.get("user")
ROLE = st.session_state.get("role")

if not CURRENT_USER or not ROLE:
    login()
else:
    USERS = load_users()
    GROUP_MEMBERS = load_groups(USERS)
    st.info(f"👋 Logged in as **{CURRENT_USER}** (Group: {ROLE})")
    colA, colB = st.columns([0.85, 0.15])
    with colB:
        if st.button("Logout", key="logout_btn"):
            _clear_auth_cookie()
            for k in ("user", "role"):
                st.session_state.pop(k, None)
            st.session_state["force_login"] = True
            try:
                st.query_params.clear()
            except Exception:
                st.experimental_set_query_params()
            st.rerun()

    ticket_key = _get_ticket_from_query()
    if ticket_key:
        with next(get_db()) as db:
            page_ticket_detail(db, ticket_key, CURRENT_USER, ROLE)
    else:
        if ROLE == "Admin":
            tabs = st.tabs(["📊 Dashboard","➕ New Ticket","🛠️ Manage","📈 Reports","👤 User Management","👥 Customers"])
        else:
            tabs = st.tabs(["📊 Dashboard","➕ New Ticket","🛠️ Manage","📈 Reports"])
        with tabs[0]:
            with next(get_db()) as db: page_dashboard(db, CURRENT_USER, ROLE)
        with tabs[1]:
            with next(get_db()) as db: page_new_ticket(db, CURRENT_USER)
        with tabs[2]:
            with next(get_db()) as db: page_manage(db, CURRENT_USER, ROLE)
        with tabs[3]:
            with next(get_db()) as db: page_reports(db, CURRENT_USER, ROLE)
        if ROLE == "Admin":
            with tabs[4]: page_user_management()
            with tabs[5]: page_customers_admin("https://docs.google.com/spreadsheets/d/1ywqLJIzydhifdUjX9Zo03B536LEUhH483hRAazT3zV8/edit?usp=sharing")
