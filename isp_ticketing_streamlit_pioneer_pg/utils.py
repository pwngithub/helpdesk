from datetime import datetime, timedelta
from dateutil import tz

DEFAULT_TZ = tz.gettz("America/New_York")

PRIORITY_SLA_HOURS = {
    "Low": 72,
    "Medium": 24,
    "High": 8,
    "Critical": 2,
}

def compute_sla_due(priority: str, created_at: datetime) -> datetime:
    hours = PRIORITY_SLA_HOURS.get(priority, 72)
    return created_at + timedelta(hours=hours)

def fmt_dt(dt: datetime, tz_name: str | None = None) -> str:
    if not dt:
        return "-"
    target_tz = tz.gettz(tz_name) if tz_name else DEFAULT_TZ
    return dt.astimezone(target_tz).strftime("%Y-%m-%d %H:%M:%S")
