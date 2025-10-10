from datetime import datetime, timedelta
def fmt_dt(dt,tz='UTC'):
    if not dt: return ''
    return dt.strftime('%Y-%m-%d %H:%M')
def compute_sla_due(priority,created_at):
    hours={'Low':72,'Medium':48,'High':24,'Critical':8}.get(priority,48)
    return created_at + timedelta(hours=hours)
