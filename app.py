import json
import os
from datetime import date
from pathlib import Path

import pandas as pd
import pyotp
import streamlit as st
from dotenv import load_dotenv

from email_sender import send_invoice_email
from invoice_generator import generate_invoice
from toggl_client import TogglClient

load_dotenv()

COUNTER_FILE = Path(__file__).parent / "invoice_counter.json"
CLIENT_EMAILS_FILE = Path(__file__).parent / "client_emails.json"


def load_client_emails() -> dict:
    if CLIENT_EMAILS_FILE.exists():
        return json.loads(CLIENT_EMAILS_FILE.read_text())
    return {}


def _get_secret(key: str, default: str = "") -> str:
    """Read from st.secrets (Streamlit Cloud) or os.getenv (local .env)."""
    try:
        return st.secrets[key]
    except (KeyError, FileNotFoundError):
        return os.getenv(key, default)


def load_counters() -> dict:
    if COUNTER_FILE.exists():
        return json.loads(COUNTER_FILE.read_text())
    return {}


def save_counters(counters: dict) -> None:
    try:
        COUNTER_FILE.write_text(json.dumps(counters, indent=2))
    except OSError:
        pass  # Read-only filesystem on some cloud hosts — counter resets gracefully


def next_invoice_number(project: str, period_start: date) -> str:
    counters = load_counters()
    num = counters.get(project, 1000) + 1
    month = period_start.strftime("%b").upper()   # "APR"
    year = period_start.strftime("%y")             # "26"
    return f"{month} {year} - {project} - {num}"


def bump_counter(project: str) -> None:
    counters = load_counters()
    counters[project] = counters.get(project, 1000) + 1
    save_counters(counters)


# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Toggl Invoice Generator", layout="wide")


# ── MFA Login gate ────────────────────────────────────────────────────────────
def check_login() -> bool:
    return st.session_state.get("authenticated", False)


def login_screen() -> None:
    st.title("🔒 Toggl Invoice Generator")
    st.markdown("Enter your password and authenticator code to continue.")

    with st.form("login_form"):
        password = st.text_input("Password", type="password")
        totp_code = st.text_input("Authenticator Code (6 digits)", max_chars=6)
        submitted = st.form_submit_button("Log In", type="primary")

    if submitted:
        correct_password = _get_secret("APP_PASSWORD")
        totp_secret = _get_secret("TOTP_SECRET")

        password_ok = password == correct_password
        totp_ok = pyotp.TOTP(totp_secret).verify(totp_code, valid_window=1)

        if password_ok and totp_ok:
            st.session_state["authenticated"] = True
            st.rerun()
        elif not password_ok:
            st.error("Incorrect password.")
        else:
            st.error("Incorrect authenticator code. Make sure your phone's clock is synced.")


if not check_login():
    login_screen()
    st.stop()  # Nothing below this runs until logged in


st.title("Toggl Invoice Generator")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Settings")
    api_token = st.text_input(
        "Toggl API Token",
        value=_get_secret("TOGGL_API_TOKEN"),
        type="password",
        help="Profile Settings → Profile → API Token (bottom of page)",
    )
    hourly_rate = st.number_input(
        "Hourly Rate ($)",
        min_value=0.0,
        value=float(_get_secret("HOURLY_RATE", "150")),
        step=5.0,
        format="%.2f",
    )
    st.caption("Times are shown in UTC (as stored in Toggl).")

# ── Period ────────────────────────────────────────────────────────────────────
col1, col2 = st.columns(2)
with col1:
    start_date = st.date_input("Period Start", value=date.today().replace(day=1))
with col2:
    end_date = st.date_input("Period End", value=date.today())

# ── Fetch ─────────────────────────────────────────────────────────────────────
if st.button("Fetch Time Entries", disabled=not api_token, type="primary"):
    if start_date > end_date:
        st.error("Period Start must be before Period End.")
    else:
        with st.spinner("Fetching from Toggl Track..."):
            try:
                client = TogglClient(api_token)
                entries = client.get_enriched_entries(start_date, end_date)
                st.session_state["entries"] = entries
            except Exception as e:
                st.error(f"Error: {e}")

# ── Preview & generate ────────────────────────────────────────────────────────
if "entries" in st.session_state:
    entries = st.session_state["entries"]

    if not entries:
        st.warning("No completed time entries found for this period.")
    else:
        # ── Project filter ────────────────────────────────────────────────────
        all_projects = sorted({e["project"] for e in entries if e["project"]})
        selected_projects = st.multiselect(
            "Filter by Project",
            options=all_projects,
            default=all_projects,
            help="Deselect projects to exclude them from the invoice.",
        )
        filtered = [e for e in entries if e["project"] in selected_projects]

        # ── Auto invoice number ───────────────────────────────────────────────
        # Only auto-fill when exactly one project is selected
        if len(selected_projects) == 1:
            auto_num = next_invoice_number(selected_projects[0], start_date)
        else:
            auto_num = ""

        invoice_number = st.text_input(
            "Invoice Number",
            value=auto_num,
            placeholder="e.g. APR 26 - STL - 1001",
        )

        st.success(f"Showing **{len(filtered)}** of {len(entries)} time entries")

        # ── Preview table ─────────────────────────────────────────────────────
        rows = []
        for e in filtered:
            h = e["duration_seconds"] // 3600
            m = (e["duration_seconds"] % 3600) // 60
            rows.append(
                {
                    "Start": e["start"].strftime("%d %b %y").upper(),
                    "Stop": e["stop"].strftime("%d %b %y").upper() if e["stop"] else "",
                    "Project": e["project"],
                    "Task": e["task"],
                    "Description": e["description"],
                    "Duration": f"{h}:{m:02d}",
                    "Member": e["member"],
                    "Amount (USD)": f"${e['duration_seconds'] / 3600 * hourly_rate:,.2f}",
                }
            )

        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # ── Totals ────────────────────────────────────────────────────────────
        total_seconds = sum(e["duration_seconds"] for e in filtered)
        total_amount = total_seconds / 3600 * hourly_rate
        th, tm = total_seconds // 3600, (total_seconds % 3600) // 60

        m1, m2 = st.columns(2)
        m1.metric("Total Duration", f"{th}:{tm:02d}")
        m2.metric("Total Amount", f"${total_amount:,.2f} USD")

        st.divider()

        # ── Generate ──────────────────────────────────────────────────────────
        if not invoice_number:
            st.info("Enter an invoice number above to generate the file.")
        elif not filtered:
            st.warning("No entries to include — check your project filter.")
        else:
            if st.button("Generate Invoice (.xlsx)", type="primary"):
                xlsx = generate_invoice(
                    entries=filtered,
                    invoice_number=invoice_number,
                    period_start=start_date,
                    period_end=end_date,
                    hourly_rate=hourly_rate,
                )
                safe_name = invoice_number.replace(" ", "_").replace("/", "-")
                filename = f"INVOICE - {safe_name}.xlsx"

                # Bump counter for single-project invoices
                if len(selected_projects) == 1:
                    bump_counter(selected_projects[0])

                # Persist across reruns so Download + Email can both use it
                st.session_state["generated_xlsx"] = xlsx
                st.session_state["generated_filename"] = filename
                st.session_state["generated_invoice_number"] = invoice_number
                st.session_state["generated_total_amount"] = total_amount
                st.session_state["generated_period_start"] = start_date
                st.session_state["generated_period_end"] = end_date

        # ── Download & Email (shown after generation) ────────────────────────
        if "generated_xlsx" in st.session_state:
            st.download_button(
                label="⬇️ Download Invoice",
                data=st.session_state["generated_xlsx"],
                file_name=st.session_state["generated_filename"],
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

            st.divider()
            st.subheader("📧 Email Invoice")

            client_emails = load_client_emails()
            default_to = (
                client_emails.get(selected_projects[0], "")
                if len(selected_projects) == 1
                else ""
            )

            to = st.text_input("To (client)", value=default_to)
            cc = st.text_input(
                "Cc (yourself)", value=_get_secret("SMTP_USER")
            )

            gen_num = st.session_state["generated_invoice_number"]
            gen_start = st.session_state["generated_period_start"]
            gen_end = st.session_state["generated_period_end"]
            gen_total = st.session_state["generated_total_amount"]

            default_subject = (
                f"Invoice {gen_num} — "
                f"{gen_start.strftime('%m/%d/%Y')} to {gen_end.strftime('%m/%d/%Y')}"
            )
            subject = st.text_input("Subject", value=default_subject)

            default_body = (
                f"Hi,\n\n"
                f"Please find attached invoice {gen_num} for the period "
                f"{gen_start.strftime('%m/%d/%Y')}–{gen_end.strftime('%m/%d/%Y')}.\n\n"
                f"Total: ${gen_total:,.2f} USD\n\n"
                f"Thank you,\nAustin Guzman"
            )
            body = st.text_area("Message", value=default_body, height=200)

            if st.button("Send Email", type="primary", disabled=not to):
                with st.spinner("Sending…"):
                    try:
                        send_invoice_email(
                            smtp_user=_get_secret("SMTP_USER"),
                            smtp_password=_get_secret("SMTP_PASSWORD"),
                            to_addrs=[to.strip()],
                            cc_addrs=[cc.strip()] if cc else [],
                            subject=subject,
                            body=body,
                            xlsx_bytes=st.session_state["generated_xlsx"],
                            filename=st.session_state["generated_filename"],
                        )
                        st.success(f"✅ Sent to {to}")
                    except Exception as e:
                        st.error(f"Failed to send: {e}")
