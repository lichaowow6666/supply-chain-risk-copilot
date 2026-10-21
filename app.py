"""Streamlit application shell for Supply Chain Risk Copilot."""

import streamlit as st

from pe_copilot.agents.fundamental_agent import FundamentalAgent
from pe_copilot.agents.market_agent import MarketAgent
from pe_copilot.orchestrator import CopilotOrchestrator
from pe_copilot.filings.service import CompanyPreparationService
from pe_copilot.market_data import refresh_prices
from pe_copilot.contracts import GraphRequest
from pe_copilot.ui.company_setup import render_report_selection
from pe_copilot.ui.tabs import (
    render_material_discovery_tab,
)

from pe_copilot.ui.scenario_workspace import render_scenario_workspace
from pe_copilot.ui.navigation import render_workbench_tabs, render_pending_canvas_focus
from pe_copilot.ui.sidebar_chat import render_sidebar_chat
from pe_copilot.workspace_state import preserve_analysis_widgets

st.set_page_config(
    page_title="Supply Chain Risk Copilot",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Style action controls only; chart and graph impact colors remain semantic.
st.html("""
<style>
:is([data-testid="stButton"], [data-testid="stDownloadButton"], [data-testid="stFormSubmitButton"])
button[kind="secondary"]:not(:disabled) {
    color: #1d4ed8;
    background-color: #eff6ff;
    border-color: #93c5fd;
}
:is([data-testid="stButton"], [data-testid="stDownloadButton"], [data-testid="stFormSubmitButton"])
button[kind="secondary"]:not(:disabled):hover {
    color: #1e40af;
    background-color: #dbeafe;
    border-color: #3b82f6;
}
:is([data-testid="stButton"], [data-testid="stDownloadButton"], [data-testid="stFormSubmitButton"])
button[kind="tertiary"]:not(:disabled) {
    color: #2563eb;
}
:is([data-testid="stButton"], [data-testid="stDownloadButton"], [data-testid="stFormSubmitButton"])
button[kind="tertiary"]:not(:disabled):hover {
    color: #1d4ed8;
    background-color: #eff6ff;
}
:is([data-testid="stButton"], [data-testid="stDownloadButton"], [data-testid="stFormSubmitButton"])
button:is([kind="secondary"], [kind="tertiary"]):not(:disabled):focus-visible {
    outline: 3px solid #93c5fd;
    outline-offset: 2px;
}
</style>
""")


@st.cache_resource
def init_orchestrator(contract_generation: int) -> CopilotOrchestrator:
    """Initialize and cache application agents and their UI-neutral coordinator."""
    preparation = CompanyPreparationService()
    market_agent = MarketAgent(db_path="market_data.duckdb")
    fundamental_agent = FundamentalAgent(
        db_path="chroma_db",
        collection_name="annual_reports",
        preparation_service=preparation,
    )
    return CopilotOrchestrator(market_agent, fundamental_agent, preparation_service=preparation)


def initialize_session_state() -> None:
    """Initialize state required by the existing working tabs."""
    if "baselines" not in st.session_state:
        st.session_state["baselines"] = {}
    if "top_materials" not in st.session_state:
        st.session_state["top_materials"] = []
    if "selected_metals" not in st.session_state:
        st.session_state["selected_metals"] = []
    if "discovery_attempted" not in st.session_state:
        st.session_state["discovery_attempted"] = False


def main() -> None:
    """Collect global inputs and route rendering through the application tabs."""
    preserve_analysis_widgets(st.session_state)
    if 'pilot_measurement_enabled' in st.session_state:
        st.session_state['pilot_measurement_enabled'] = st.session_state['pilot_measurement_enabled']
    # Streamlit reloads imported dataclasses after source edits. Cached services
    # must share that generation or isinstance-based request validation fails.
    orchestrator = init_orchestrator(id(GraphRequest))
    initialize_session_state()
    # Render before chat's early rerun so Streamlit cannot clean up this toggle.
    interview_enabled = st.sidebar.toggle('Evaluation', value=False,
                                          key='interview_evaluation_enabled')
    company = render_sidebar_chat(orchestrator)

    with st.spinner('Checking daily futures data…'):
        try:
            market_status = refresh_prices()
            if not market_status['busy']:
                st.session_state['market_version'] = tuple((r['ticker'], r['latest_session'], r['checked'], r['status']) for r in market_status['contracts'])
            with st.sidebar.expander('Futures data freshness'):
                st.dataframe(market_status['contracts'], hide_index=True)
                if st.button('Retry market refresh now'):
                    refresh_prices(force=True)
                    st.rerun()
            for warning in market_status['warnings']:
                st.sidebar.warning(warning)
        except Exception as error:
            st.sidebar.warning(f'Market refresh unavailable ({type(error).__name__}); existing data may be stale.')

    if not company:
        st.title('Supply Chain Risk Copilot')
    else:
        st.title(f"Supply Chain Risk Copilot · {company['ticker']}")

    tabs = render_workbench_tabs(interview_enabled)
    tab1, tab2 = tabs[:2]
    if interview_enabled:
        from pe_copilot.ui.interview_dashboard import render_interview_dashboard
        with tabs[2]:
            render_interview_dashboard()
    if not company:
        with tab1:
            st.empty()
        with tab2:
            st.empty()
        return
    ticker = company['ticker']
    db_metals = orchestrator.get_available_commodities()

    def discover_target(target_ticker: str):
        return orchestrator.discover_target(target_ticker, st.session_state.get('selected_report_accession'))

    with tab1:
        render_material_discovery_tab(ticker, db_metals, discover_target, lambda: render_report_selection(orchestrator, ticker))
    with tab2:
        render_scenario_workspace(orchestrator, ticker)
    render_pending_canvas_focus(st.session_state)


try:
    main()
except Exception as error:
    st.error(f"Initialization Error: {error}")
