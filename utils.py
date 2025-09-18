from datetime import datetime, timedelta
import pytz

def compute_sla_due(priority: str, created_at: datetime):
    hours = {"Low": 72, "Medium": 48, "High": 24, "Critical": 8}
    return created_at + timedelta(hours=hours.get(priority, 48))

def fmt_dt(dt: datetime, tz: str = "UTC") -> str:
    if not dt:
        return "-"
    local = pytz.timezone(tz)
    return dt.astimezone(local).strftime("%Y-%m-%d %H:%M")
