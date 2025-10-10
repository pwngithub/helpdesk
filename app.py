import os
import io
import re
import time
import urllib.request
from datetime import datetime
import pandas as pd
import streamlit as st
from sqlalchemy.orm import joinedload

from db import engine, get_db
from schema import Base, Ticket, TicketEvent, Customer
from utils import compute_sla_due, fmt_dt
from constants import STATUS_ORDER, PRIORITY_ORDER, STATUS_COLOR, PRIORITY_COLOR

# ---------------- Initialize ----------------
Base.metadata.create_all(bind=engine)
st.set_page_config(page_title="Pioneer Ticketing", page_icon
