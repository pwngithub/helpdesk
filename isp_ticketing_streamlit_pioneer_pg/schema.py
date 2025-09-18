from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Boolean
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import func

Base = declarative_base()

class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(Integer, primary_key=True, index=True)
    ticket_key = Column(String(32), unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    customer_name = Column(String(120), index=True)
    account_number = Column(String(64), index=True)
    phone = Column(String(64), index=True)
    address = Column(String(256))
    city = Column(String(128))
    state = Column(String(32))
    zip = Column(String(20))

    service_type = Column(String(64))   # Fiber, DSL, Fixed Wireless, TV, Voice
    equipment = Column(String(128))     # ONT, Router, Set-Top, etc.
    plan = Column(String(64))

    call_source = Column(String(32))    # phone, email, chat, walk-in
    call_reason = Column(String(64))    # outage, repair, billing, upgrade, cancel, new service
    description = Column(Text)

    status = Column(String(32), default="Open", index=True)
    priority = Column(String(16), default="Low", index=True)
    assigned_to = Column(String(64), index=True)

    sla_due = Column(DateTime, nullable=True, index=True)
    resolved_at = Column(DateTime, nullable=True, index=True)

    followup_required = Column(Boolean, default=False)
    followup_at = Column(DateTime, nullable=True)

    events = relationship("TicketEvent", back_populates="ticket", cascade="all, delete-orphan")

class TicketEvent(Base):
    __tablename__ = "ticket_events"
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"))
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    actor = Column(String(64))           # agent username or name
    action = Column(String(64))          # status_change, note, assign, escalate, resolve, close
    from_value = Column(String(64))
    to_value = Column(String(64))
    note = Column(Text)

    ticket = relationship("Ticket", back_populates="events")
