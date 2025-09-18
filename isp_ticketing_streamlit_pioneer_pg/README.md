# Pioneer Ticketing (Streamlit MVP with Postgres)

A Pioneer-branded Streamlit app for help desk ticketing. Tracks caller details,
issue types, SLA timers, escalations, and resolution history. Uses SQLite by default,
with optional Postgres support for production.

## Features
- Pioneer branding (colors, logo, styled UI)
- Create, search, update, and close tickets
- Auto ticket ID, timestamps, and SLA due calculation
- Statuses: Open, In Progress, Escalated, On Hold, Resolved, Closed
- Priorities: Low, Medium, High, Critical
- Call source, reason, service type, equipment
- Add internal notes & status changes (full event history)
- Dashboard: open tickets, SLA breaches, average resolution time, filters
- SQLite for local testing OR Postgres for production

## Quick Start (SQLite)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Quick Start (Postgres)
1. Ensure Postgres is running and create a database, e.g. `ticketsdb`
2. Set environment variable in `.env`:
   ```
   DB_URL=postgresql+psycopg2://username:password@hostname:5432/ticketsdb
   ```
3. Run the app:
   ```bash
   streamlit run app.py
   ```

## Docker Example for Postgres
```bash
docker run --name pioneer-tickets -e POSTGRES_PASSWORD=secret -e POSTGRES_DB=ticketsdb -p 5432:5432 -d postgres
```
And in `.env`:
```
DB_URL=postgresql+psycopg2://postgres:secret@localhost:5432/ticketsdb
```
