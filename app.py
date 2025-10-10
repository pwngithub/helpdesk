# app.py (no-login version)
# Simplified ticketing system without authentication
import os, re, io, urllib.request
import pandas as pd
import streamlit as st
from datetime import datetime
from sqlalchemy.orm import Session, joinedload
from db import engine, get_db
from schema import Base, Ticket, TicketEvent, Customer
from utils import compute_sla_due, fmt_dt
from constants import STATUS_ORDER, PRIORITY_ORDER, STATUS_COLOR, PRIORITY_COLOR

Base.metadata.create_all(bind=engine)
st.set_page_config(page_title="Pioneer Ticketing", page_icon="🎫", layout="wide")

PIONEER_LOGO = "https://images.squarespace-cdn.com/content/v1/651eb4433b13e72c1034f375/369c5df0-5363-4827-b041-1add0367f447/PBB+long+logo.png?format=1500w"
st.markdown(f"<div style='display:flex;align-items:center;gap:10px;background:#002856;padding:10px;border-radius:8px;'><img src='{PIONEER_LOGO}' height='40'><h2 style='color:white;'>Pioneer Ticketing</h2></div>", unsafe_allow_html=True)

USER = "Admin"; ROLE = "Admin"

def badge(text, color): return f'<span style="background:{color};padding:3px 8px;border-radius:6px;color:white;">{text}</span>'

def dataframe_with_badges(rows):
    data=[]; now=datetime.utcnow()
    for t in rows:
        latest=t.description or "-"
        if getattr(t,"events",None):
            evs=[e for e in t.events if e.note]
            if evs: latest=sorted(evs,key=lambda e:e.created_at)[-1].note
        data.append({
            "Key": f"<a href='?ticket={t.ticket_key}' target='_self'>{t.ticket_key}</a>",
            "Created": fmt_dt(t.created_at),
            "Customer": t.customer_name,
            "Acct #": t.account_number,
            "Phone": t.phone,
            "Status": badge(t.status, STATUS_COLOR.get(t.status,'gray')),
            "Priority": badge(t.priority, STATUS_COLOR.get(t.priority,'gray')),
            "Assigned": t.assigned_to,
            "Note": latest
        })
    return pd.DataFrame(data)

def render_df_html(df): st.write(df.to_html(escape=False,index=False), unsafe_allow_html=True)

def fetch_customers_from_sheet(url):
    m=re.search(r'/spreadsheets/d/([\w-]+)',url)
    if not m: raise ValueError("Bad Sheet URL")
    sid=m.group(1)
    link=f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv"
    req=urllib.request.Request(link,headers={"User-Agent":"Mozilla"})
    with urllib.request.urlopen(req) as r:
        return pd.read_csv(io.StringIO(r.read().decode("utf-8")))

def normalize_customer_df(df):
    cols={c:str(c).strip().lower() for c in df.columns}; df=df.rename(columns=cols)
    if 'account number' in df.columns: df.rename(columns={'account number':'account_number'},inplace=True)
    if 'customer name' in df.columns: df.rename(columns={'customer name':'name'},inplace=True)
    for c in ['account_number','name','phone']: df[c]=df.get(c,'')
    return df[['account_number','name','phone']]

def upsert_customers(db,df):
    ins=upd=0
    for _,r in df.iterrows():
        acct=r['account_number']
        if not acct: continue
        c=db.query(Customer).filter(Customer.account_number==acct).first()
        if c: c.name=r['name']; c.phone=r['phone']; upd+=1
        else: db.add(Customer(account_number=acct,name=r['name'],phone=r['phone'])); ins+=1
    db.commit(); return ins,upd

def page_dashboard(db):
    rows=db.query(Ticket).options(joinedload(Ticket.events)).order_by(Ticket.created_at.desc()).all()
    st.subheader("📊 Dashboard Overview")
    render_df_html(dataframe_with_badges(rows))

def page_new_ticket(db):
    st.subheader("➕ New Ticket")
    acct=st.text_input("Account Number (search)")
    name=st.text_input("Customer Name (search)")
    matches=[]
    if acct.strip(): matches=db.query(Customer).filter(Customer.account_number.ilike(f"%{acct}%")).all()
    elif name.strip(): matches=db.query(Customer).filter(Customer.name.ilike(f"%{name}%")).limit(20).all()
    if matches:
        labels=[f"{c.name} — {c.account_number}" for c in matches]
        sel=st.selectbox("Select customer",[""]+labels)
        if sel:
            c=matches[labels.index(sel)-1]
            st.session_state["new_acct"]=c.account_number
            st.session_state["new_name"]=c.name
            st.session_state["new_phone"]=c.phone
    customer_name=st.text_input("Customer Name",key="new_name")
    account_number=st.text_input("Account Number",key="new_acct")
    phone=st.text_input("Phone",key="new_phone")
    service_type=st.selectbox("Service Type",["Fiber","DSL","Wireless","TV","Voice","Other"])
    call_reason=st.selectbox("Reason",["outage","repair","billing","upgrade","cancel","other"])
    priority=st.selectbox("Priority",PRIORITY_ORDER,1)
    description=st.text_area("Description",height=120)
    assigned_to=st.selectbox("Assign To",["Unassigned","Billing","Support","Sales","BJ","Megan","Billy","Gillian","Gabby","Chuck","Aidan"])
    if st.button("Create Ticket"):
        t=Ticket(ticket_key=f"TCK-{int(datetime.utcnow().timestamp())}",created_at=datetime.utcnow(),
                 customer_name=customer_name,account_number=account_number,phone=phone,
                 service_type=service_type,call_reason=call_reason,description=description,
                 status="Open",priority=priority,assigned_to=assigned_to,
                 sla_due=compute_sla_due(priority,datetime.utcnow()))
        db.add(t); db.commit(); st.success(f"✅ Ticket created: {t.ticket_key}")

def page_customers_admin():
    st.subheader("👥 Customers")
    url=st.text_input("Google Sheet URL")
    c1,c2=st.columns(2)
    with c1: preview=st.button("🔎 Preview")
    with c2: imp=st.button("⬇️ Import / Upsert")
    if preview or imp:
        try:
            df=normalize_customer_df(fetch_customers_from_sheet(url))
            st.dataframe(df.head(20))
            if imp:
                with next(get_db()) as db:
                    ins,upd=upsert_customers(db,df)
                st.success(f"✅ Imported {ins} new, {upd} updated")
        except Exception as e: st.error(str(e))

ticket_key=st.query_params.get("ticket")
if ticket_key:
    with next(get_db()) as db:
        t=db.query(Ticket).filter(Ticket.ticket_key==ticket_key).first()
        if not t: st.error("Ticket not found")
        else:
            st.title(f"{t.ticket_key} — {t.customer_name}")
st.write(f"**Account:** {t.account_number}  \n**Phone:** {t.phone}")
st.write(t.description or "")

else:
    st.info(f"👋 Logged in automatically as {USER} ({ROLE})")
    tabs=st.tabs(["Dashboard","New Ticket","Customers"])
    with tabs[0]: 
        with next(get_db()) as db: page_dashboard(db)
    with tabs[1]: 
        with next(get_db()) as db: page_new_ticket(db)
    with tabs[2]: page_customers_admin()
