"""Streamlit dashboard application.

Provides a read-only view of sync history, data quality, and recent errors.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go

# Ensure the package is importable if run directly
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from deliverect_sync.config import AppSettings
from deliverect_sync.storage.database import DatabaseManager


def load_data() -> tuple[DatabaseManager, AppSettings]:
    """Load config and initialize database connection."""
    settings = AppSettings.load()
    db = DatabaseManager(settings.db_path)
    db.initialize()
    return db, settings


def main() -> None:
    """Run the Streamlit dashboard."""
    st.set_page_config(
        page_title="Deliverect Order Sync Dashboard",
        page_icon="🍔",
        layout="wide",
    )

    st.title("Deliverect Order Sync Dashboard")

    try:
        db, settings = load_data()
    except Exception as e:
        st.error(f"Failed to load database: {e}")
        return

    # Tabs
    tab1, tab2, tab3 = st.tabs(["Overview", "Sync History", "Data Quality"])

    with tab1:
        render_overview(db)

    with tab2:
        render_sync_history(db)

    with tab3:
        render_data_quality(db)


def render_overview(db: DatabaseManager) -> None:
    """Render the main overview tab."""
    st.header("System Overview")

    # Metrics
    conn = db._get_conn()
    total_orders = conn.execute("SELECT COUNT(*) as cnt FROM orders").fetchone()["cnt"]
    last_run = db.get_last_run()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Orders Synced", f"{total_orders:,}")
    with col2:
        if last_run:
            st.metric("Last Sync Status", last_run.result.value)
        else:
            st.metric("Last Sync Status", "Never run")
    with col3:
        if last_run and last_run.finished_at:
            time_str = last_run.finished_at.strftime("%Y-%m-%d %H:%M")
            st.metric("Last Sync Time", time_str)
        else:
            st.metric("Last Sync Time", "N/A")
    with col4:
        active = "Yes" if db.is_run_active() else "No"
        st.metric("Sync In Progress", active)

    st.divider()

    # Charts
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Orders by Status")
        status_rows = conn.execute(
            "SELECT current_status, COUNT(*) as cnt FROM orders GROUP BY current_status"
        ).fetchall()
        if status_rows:
            status_counts = {r["current_status"] or "Unknown": r["cnt"] for r in status_rows}
            df_status = pd.DataFrame(list(status_counts.items()), columns=["Status", "Count"])
            fig = px.pie(df_status, values="Count", names="Status", hole=0.4)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No data available")

    with col2:
        st.subheader("Orders by Channel")
        channel_rows = conn.execute(
            "SELECT channel, COUNT(*) as cnt FROM orders GROUP BY channel"
        ).fetchall()
        if channel_rows:
            channel_counts = {r["channel"] or "Unknown": r["cnt"] for r in channel_rows}
            df_channel = pd.DataFrame(list(channel_counts.items()), columns=["Channel", "Count"])
            fig = px.bar(df_channel, x="Channel", y="Count", color="Channel")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No data available")


def render_sync_history(db: DatabaseManager) -> None:
    """Render the sync history tab."""
    st.header("Recent Sync Runs")

    conn = db._get_conn()
    rows = conn.execute("SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT 50").fetchall()
    if not rows:
        st.info("No sync runs found")
        return

    # Convert to DataFrame for table
    run_data = []
    for row in rows:
        d = dict(row)
        started = d.get("started_at", "")
        finished = d.get("finished_at")
        duration = 0
        if started and finished:
            from datetime import datetime
            try:
                s = datetime.fromisoformat(started)
                f = datetime.fromisoformat(finished)
                duration = int((f - s).total_seconds())
            except Exception:
                pass

        run_data.append({
            "Run ID": d["id"][:8],
            "Date": started[:16] if started else "",
            "Status": d.get("result", ""),
            "Duration (s)": duration,
            "Imported Rows": d.get("imported_rows", 0),
            "New Orders": d.get("new_orders", 0),
            "Rejected Rows": d.get("rejected_rows", 0),
        })

    df_runs = pd.DataFrame(run_data)

    # Apply coloring to Status column
    def color_status(val: str) -> str:
        if val == "SUCCESS":
            return "color: green"
        elif val == "SUCCESS_WITH_WARNINGS":
            return "color: orange"
        elif val == "IN_PROGRESS":
            return "color: blue"
        else:
            return "color: red"

    st.dataframe(
        df_runs.style.map(color_status, subset=["Status"]),
        use_container_width=True,
        hide_index=True,
    )


def render_data_quality(db: DatabaseManager) -> None:
    """Render the data quality tab."""
    st.header("Data Quality & Errors")

    conn = db._get_conn()
    error_rows = conn.execute(
        "SELECT * FROM import_errors ORDER BY timestamp DESC LIMIT 200"
    ).fetchall()

    if not error_rows:
        st.success("No import errors recorded. Data quality is excellent.")
        return

    errors = [dict(r) for r in error_rows]
    df_errors = pd.DataFrame(errors)

    # Aggregate errors by code
    error_counts = df_errors["error_code"].value_counts().reset_index()
    error_counts.columns = ["Error Code", "Count"]

    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("Errors by Type")
        fig = px.pie(error_counts, values="Count", names="Error Code")
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Recent Error Log")
        display_cols = [c for c in ["timestamp", "stage", "error_code", "error_message", "row_number"] if c in df_errors.columns]
        recent = df_errors.sort_values("timestamp", ascending=False).head(20)
        st.dataframe(recent[display_cols], use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
