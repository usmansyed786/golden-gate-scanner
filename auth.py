"""
auth.py
Simple access-code gate for the paid tier of the scanner.

HOW IT WORKS
------------
- You keep a Google Sheet with one access code per row (column A).
- When a customer pays you (via Razorpay/Instamojo/UPI payment link), you
  generate a code (any string, e.g. "GG-4F91A2") and add a new row to the sheet.
- The app checks whether the code a user types in exists in that sheet.

This needs NO backend server - Streamlit Cloud can read a Google Sheet directly.

ONE-TIME SETUP (see README "Monetization Setup" section for full steps):
1. Create a Google Sheet, put access codes in column A (one per row).
2. Create a Google Cloud service account, share the sheet with its email
   (Viewer access is enough).
3. Add the service account JSON key and the sheet URL to Streamlit secrets
   (Settings -> Secrets on Streamlit Cloud, or .streamlit/secrets.toml locally).
"""

import streamlit as st

try:
    import gspread
    from google.oauth2.service_account import Credentials
    _GSPREAD_AVAILABLE = True
except ImportError:
    _GSPREAD_AVAILABLE = False

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


@st.cache_resource(show_spinner=False)
def _get_sheet():
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet_url = st.secrets["access_codes_sheet_url"]
    return client.open_by_url(sheet_url).sheet1


def is_configured() -> bool:
    """True if the secrets needed for the access-code system are present."""
    if not _GSPREAD_AVAILABLE:
        return False
    return "gcp_service_account" in st.secrets and "access_codes_sheet_url" in st.secrets


def is_valid_code(code: str) -> bool:
    """Checks whether `code` exists in the access-codes Google Sheet."""
    if not code or not code.strip():
        return False
    if not is_configured():
        return False
    try:
        sheet = _get_sheet()
        valid_codes = [c.strip().upper() for c in sheet.col_values(1) if c.strip()]
        return code.strip().upper() in valid_codes
    except Exception:
        return False
