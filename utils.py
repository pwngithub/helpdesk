from datetime import datetime,timedelta

def fmt_dt(dt,tz=None):
    return dt.strftime('%Y-%m-%d %H:%M') if dt else ''

def compute_sla_due(priority,created_at):
    hours={'High':4,'Medium':8,'Low':24}.get(priority,8)
    return created_at+timedelta(hours=hours)
