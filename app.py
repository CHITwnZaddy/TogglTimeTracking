import json
import os
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from invoice_generator import generate_invoice
from toggl_client import TogglClient

load_dotenv()

COUNTER_FILE = Path(__file__).parent / "invoice_counter.json"


def load_counters() -> dict:
    if COUNTER_FILE.exists():
        return json.loads(COUNTER_FILE.read_text())
    return {}


def save_counters(counters: dict) -> None:
    COUNTER_FILE.write_text(json.dumps(counters, indent=2))


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
st.title("Toggl Invoice Generator")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Settings")
    api_token = st.text_input(
        "Toggl API Token",
        value=os.getenv("TOGGL_API_TOKEN", ""),
        type="password",
        help="Profile Settings → Profile → API Token (bottom of page)",
    )
    hourly_rate = st.number_input(
        "Hourly Rate ($)",
        min_value=0.0,
        value=float(os.getenv("HOURLY_RATE", 150)),
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

                st.download_button(
                    label="⬇️ Download Invoice",
                    data=xlsx,
                    file_name=filename,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
