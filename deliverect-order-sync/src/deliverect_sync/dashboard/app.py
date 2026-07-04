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

from deliverect_sync.config import load_config
from deliverect_sync.storage.database import DatabaseManager
from deliverect_sync.storage.repositories import OrderRepository


def load_data() -> tuple[DatabaseManager, OrderRepository]:
    """Load config and initialize database connection."""
    config = load_config()
    db = DatabaseManager(config.database_path)
    repo = OrderRepository(db)
    return db, repo


def main() -> None:
    """Run the Streamlit dashboard."""
    st.set_page_config(
        page_title="Deliverect Order Sync Dashboard",
        page_icon="🍔",
        layout="wide",
    )

    st.title("Deliverect Order Sync Dashboard")

    try:
        db, repo = load_data()
    except Exception as e:
        st.error(f"Failed to load database: {e}")
        return

    # Tabs
    tab1, tab2, tab3 = st.tabs(["Overview", "Sync History", "Data Quality"])

    with tab1:
        render_overview(db, repo)

    with tab2:
        render_sync_history(db)

    with tab3:
        render_data_quality(db)


def render_overview(db: DatabaseManager, repo: OrderRepository) -> None:
    """Render the main overview tab."""
    st.header("System Overview")

    # Metrics
    total_orders = repo.count_orders()
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
        status_counts = repo.count_by_status()
        if status_counts:
            df_status = pd.DataFrame(list(status_counts.items()), columns=["Status", "Count"])
            fig = px.pie(df_status, values="Count", names="Status", hole=0.4)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No data available")

    with col2:
        st.subheader("Orders by Channel")
        channel_counts = repo.count_by_channel()
        if channel_counts:
            df_channel = pd.DataFrame(list(channel_counts.items()), columns=["Channel", "Count"])
            fig = px.bar(df_channel, x="Channel", y="Count", color="Channel")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No data available")


def render_sync_history(db: DatabaseManager) -> None:
    """Render the sync history tab."""
    st.header("Recent Sync Runs")

    runs = db.get_all_runs()
    if not runs:
        st.info("No sync runs found")
        return

    # Convert to DataFrame for table
    run_data = []
    for run in runs[:50]:  # Limit to last 50
        run_data.append({
            "Run ID": run.id[:8],
            "Date": run.started_at.strftime("%Y-%m-%d %H:%M") if run.started_at else "",
            "Status": run.result.value if run.result else "",
            "Duration (s)": int((run.finished_at - run.started_at).total_seconds()) if run.finished_at and run.started_at else 0,
            "Imported Rows": run.imported_rows,
            "New Orders": run.new_orders,
            "Rejected Rows": run.rejected_rows,
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

    errors = db.get_import_errors()
    if not errors:
        st.success("No import errors recorded. Data quality is excellent.")
        return

    # Aggregate errors by code
    df_errors = pd.DataFrame(errors)
    error_counts = df_errors["error_code"].value_counts().reset_index()
    error_counts.columns = ["Error Code", "Count"]

    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("Errors by Type")
        fig = px.pie(error_counts, values="Count", names="Error Code")
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Recent Error Log")
        recent = df_errors.sort_values("timestamp", ascending=False).head(20)
        display_df = recent[["timestamp", "stage", "error_code", "error_message", "row_number"]]
        st.dataframe(display_df, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
