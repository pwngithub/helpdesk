from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from db import get_db
from schema import Ticket, Customer
from utils import compute_sla_due
from datetime import datetime

app = FastAPI(title="Pioneer Helpdesk API")

@app.get("/tickets")
def list_tickets(db: Session = Depends(get_db)):
    return db.query(Ticket).all()

@app.post("/tickets")
def create_ticket(ticket: dict, db: Session = Depends(get_db)):
    t = Ticket(
        ticket_key=f"TCK-{int(datetime.utcnow().timestamp())}",
        customer_name=ticket.get("customer_name", ""),
        account_number=ticket.get("account_number", ""),
        phone=ticket.get("phone", ""),
        service_type=ticket.get("service_type", "Other"),
        call_reason=ticket.get("call_reason", "other"),
        description=ticket.get("description", ""),
        status="Open",
        priority=ticket.get("priority", "Normal"),
        assigned_to=ticket.get("assigned_to", "Unassigned"),
        created_at=datetime.utcnow(),
        sla_due=compute_sla_due(ticket.get("priority", "Normal"), datetime.utcnow())
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"message": "Ticket created", "ticket": t.ticket_key}

@app.get("/customers")
def list_customers(search: str = "", db: Session = Depends(get_db)):
    q = db.query(Customer)
    if search:
        q = q.filter(Customer.name.ilike(f"%{search}%"))
    return q.limit(50).all()
