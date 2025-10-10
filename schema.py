from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
Base = declarative_base()
class Ticket(Base):
    __tablename__='tickets'
    id=Column(Integer,primary_key=True,index=True)
    ticket_key=Column(String,unique=True,index=True)
    created_at=Column(DateTime,default=datetime.utcnow)
    customer_name=Column(String)
    account_number=Column(String)
    phone=Column(String)
    service_type=Column(String)
    call_reason=Column(String)
    description=Column(Text)
    status=Column(String,default="Open")
    priority=Column(String,default="Medium")
    assigned_to=Column(String)
    sla_due=Column(DateTime)
    events=relationship("TicketEvent",back_populates="ticket")
class TicketEvent(Base):
    __tablename__='ticket_events'
    id=Column(Integer,primary_key=True,index=True)
    ticket_id=Column(Integer,ForeignKey('tickets.id'))
    actor=Column(String); action=Column(String); note=Column(Text)
    created_at=Column(DateTime,default=datetime.utcnow)
    ticket=relationship("Ticket",back_populates="events")
class Customer(Base):
    __tablename__='customers'
    id=Column(Integer,primary_key=True,index=True)
    account_number=Column(String,unique=True,index=True)
    name=Column(String); phone=Column(String); email=Column(String); address=Column(String)
    service_type=Column(String); notes=Column(Text)
