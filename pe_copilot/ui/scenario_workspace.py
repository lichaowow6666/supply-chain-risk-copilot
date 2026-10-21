"""One report-scoped scenario workspace: controls, graph, KPIs and market charts."""
from .tabs import render_stress_test_tab
from .graph_explorer import render_supply_chain_tab
from .forecasting import render_forecast_controls
import streamlit as st


def render_scenario_workspace(orchestrator, ticker):
    def graph():
        context = st.session_state.get('baseline_context')
        if not context:
            st.info('Choose a report in Tab 1 to explore its graph and run a scenario.')
            return
        render_supply_chain_tab(orchestrator, ticker, pinned_context=context)

    render_stress_test_tab(ticker, orchestrator.run_stress_test,
                           run_scenario=orchestrator.invoke, render_graph=graph,
                           forecast_controls=lambda:render_forecast_controls(orchestrator,st.session_state.get('selected_metals',[])))
