import os
import zipfile

# Define project structure
project_name = "helpdesk_app_ready"
base_dir = f"/mnt/data/{project_name}"
os.makedirs(base_dir, exist_ok=True)

# Full app.py content (from last corrected version with login persistence fix)
app_py_content = """<FULL APP.PY CODE FROM EARLIER RESPONSE HERE>"""

# Files content
files_content = {
    "app.py": app_py_content,
    "db.py": """from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "sqlite:///tickets.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
""",
    "schema.py": """from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime

Base = declarative_base()

class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, index=True)
    ticket_key = Column(String, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    customer_name = Column(String)
    account_number = Column(String)
    phone = Column(String)
    service_type = Column(String)
    call_source = Column(String)
    call_reason = Column(String)
    description = Column(String)
    status = Column(String)
    priority = Column(String)
    assigned_to = Column(String)
    sla_due = Column(DateTime)

    events = relationship("TicketEvent", back_populates="ticket", cascade="all, delete")

class TicketEvent(Base):
    __tablename__ = "ticket_events"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    actor = Column(String)
    action = Column(String)
    note = Column(String)

    ticket = relationship("Ticket", back_populates="events")
""",
    "utils.py": """from datetime import datetime, timedelta, timezone

def compute_sla_due(priority: str, created_at: datetime):
    if priority == "Critical":
        return created_at + timedelta(hours=4)
    if priority == "High":
        return created_at + timedelta(hours=12)
    if priority == "Medium":
        return created_at + timedelta(days=1)
    if priority == "Low":
        return created_at + timedelta(days=3)
    return None

def fmt_dt(dt: datetime, tz: str = "America/New_York") -> str:
    if not dt:
        return "-"
    return dt.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
""",
    "requirements.txt": """streamlit
sqlalchemy
pandas
python-dotenv
"""
}

# Write files to base_dir
for fname, content in files_content.items():
    with open(os.path.join(base_dir, fname), "w") as f:
        f.write(content)

# Create ZIP file
zip_path = f"/mnt/data/{project_name}.zip"
with zipfile.ZipFile(zip_path, 'w') as zf:
    for fname in files_content.keys():
        zf.write(os.path.join(base_dir, fname), arcname=fname)

zip_path
