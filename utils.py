from datetime import datetime, timedelta, timezone

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
