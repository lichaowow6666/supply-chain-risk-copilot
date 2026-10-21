"""Discovery and scenario workbench renderers."""

from typing import Any, Callable, Dict, Iterable, Mapping, Tuple

import streamlit as st
from dataclasses import replace, asdict
from pe_copilot.contracts import capture_stress_context, stress_input_signature, ScenarioRequest, ScenarioMaterial, ScenarioResult, WorkspaceContext
from .navigation import SCENARIO_TAB, render_canvas_anchor
from pe_copilot.analytics.scenario_attribution import contribution_rows, compare_scenarios

from .presentation import (
    available_discovered_materials,
    build_history_chart,
    build_kpi_card,
    build_stochastic_chart,
    build_waterfall_message,
)


DiscoveryCallback = Callable[[str], Tuple[list, dict]]
StressTestCallback = Callable[[Mapping[str, Mapping[str, float]], float, Mapping], dict]


def render_material_discovery_tab(
    ticker: str,
    db_metals: Iterable[str],
    discover_target: DiscoveryCallback,
    report_setup=None,
) -> None:
    """Render 10-K material discovery and commodity selection."""
    render_canvas_anchor('overview')
    st.header("1. Financial Overview & Material Exposure")
    if report_setup:
        report_setup()
    prepared = st.session_state.get('preparation_info')
    baseline = st.session_state.get('selected_report_baseline') or (prepared or {}).get('baseline')
    if baseline:
        render_financial_overview(baseline)
    render_canvas_anchor('exposures')

    top_materials = st.session_state.get("top_materials", [])
    if top_materials and not any('search_mode' in item for item in top_materials):
        with st.spinner('Upgrading the material audit with full disclosure evidence…'):
            top_materials, baselines = discover_target(ticker)
            st.session_state['top_materials'] = top_materials
            st.session_state['baselines'] = baselines
            st.session_state.pop(f'materials_{ticker}', None)
            st.session_state['selected_metals'] = []
    if not top_materials:
        if st.session_state.get("discovery_attempted", False):
            st.warning(
                "No independently evidenced material exposures were returned. "
                "Check preparation notes and source coverage before running a stress test."
            )
        return

    for row_start in range(0, len(top_materials), 4):
        row_materials = top_materials[row_start:row_start + 4]
        columns = st.columns(len(row_materials))
        for offset, (column, item) in enumerate(zip(columns, row_materials)):
            with column:
                index = row_start + offset
                st.subheader(f"#{index + 1} {item.get('material', 'Unknown')}")
                if not item.get('market_covered', item.get('material') in db_metals):
                    st.caption('No futures coverage')
                st.markdown(
                    f"**Inherent Exposure:** `{item.get('inherent_exposure_level', 'N/A')}`"
                )
                st.markdown(f"**Hedge Level:** `{item.get('hedge_level', 'N/A')}`")
                st.markdown(f"**Executive Brief:**\n{item.get('explanation', '')}")

                raw_chunks = item.get("raw_chunks", [])
                with st.expander(f"Evidence · {len(raw_chunks)} sources"):
                    st.write(item.get('ranking_basis', 'Qualitative exposure assessment'))
                    for gap in item.get('evidence_gaps', []):
                        st.write(gap)
                    for chunk in raw_chunks:
                        st.caption(
                            f"Source ID #{chunk.get('chunk_id', 'N/A')} | "
                            f"Evidence: {chunk.get('distance', 'N/A')}"
                        )
                        if chunk.get('supports'):
                            st.caption('Supports: '+', '.join(chunk['supports'])+' · '+chunk.get('section',''))
                        st.write(f'"{chunk.get("text", "")}"')
                        if chunk.get('source_url'):
                            st.link_button(f"Open {chunk.get('document_type','filing')} · {chunk.get('period_end','')}",chunk['source_url'])
                        st.markdown("---")

    st.markdown("---")
    render_canvas_anchor('materials')
    st.subheader("2. Scenario materials")

    valid_intersections = available_discovered_materials(top_materials, db_metals)
    uncovered = [r['material'] for r in top_materials if r['material'].title() not in valid_intersections]
    if not valid_intersections:
        st.warning("None of the discovered materials currently have futures data in your DuckDB database.")
        return

    selected = st.multiselect(
        "Choose Target Commodity Exposures:",
        options=valid_intersections,
        default=valid_intersections[:2] if len(valid_intersections) >= 2 else valid_intersections,
        key=f"materials_{ticker}",
    )
    st.session_state["selected_metals"] = selected

    if st.button("Open scenario", type="primary"):
        if selected:
            st.session_state['requested_tab'] = SCENARIO_TAB
            st.rerun()
        else:
            st.warning("Please select at least one material before proceeding.")


def render_financial_overview(baseline):
    st.subheader('Financial condition at a glance')
    st.caption(f"{baseline.get('form','10-K')} · {baseline.get('duration_basis','Annual')} · {baseline['period_end']} · USD millions")
    metrics = ('Revenue','Gross Profit','Operating Income','Net Income','Cash & Equivalents','Total Debt','Total Assets','Total Equity','Operating Cash Flow','CapEx','Free Cash Flow','Working Capital')
    values = {k:v for pool in baseline['pools'].values() for k,v in pool.items()}
    for start in range(0,len(metrics),4):
        for column, name in zip(st.columns(4),metrics[start:start+4]):
            with column:
                value = values.get(name)
                st.metric(name, f"${value:,.1f}M" if value is not None else 'Unavailable')
    if baseline.get('source_url'):
        st.link_button('Open selected SEC report',baseline['source_url'])
    with st.expander('Financial sources and calculation notes'):
        for warning in baseline['warnings']:
            st.warning(warning)
        st.dataframe([{'Metric':metric,**row} for metric,row in baseline['evidence'].items()],hide_index=True,width='stretch')


def _render_kpi_pool(
    heading: str,
    selected_metrics: Iterable[str],
    pool_name: str,
    pools: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> None:
    selected = list(selected_metrics)
    if not selected or pool_name not in pools:
        return

    st.markdown(heading)
    cards = st.columns(len(selected))
    for index, metric in enumerate(selected):
        with cards[index]:
            if metric in pools[pool_name]:
                st.metric(**build_kpi_card(metric, pools[pool_name][metric], pool_name))
            else:
                st.info(f'{metric}: not calculated because required baseline inputs are missing.')


def render_stress_test_tab(
    ticker: str,
    run_stress_test: StressTestCallback,
    run_scenario=None,
    render_graph=None,
    forecast_controls=None,
) -> None:
    """Render stress-test inputs, results, KPI cards, and market charts."""
    render_canvas_anchor('assumptions')
    st.header("Scenario Analysis")

    selected_metals = st.session_state.get("selected_metals", [])
    baselines = st.session_state.get("baselines", {})
    st.session_state['sim_inputs_current'] = False
    if not selected_metals or not baselines:
        if render_graph:
            render_graph()
        return

    with st.expander('Choose monitored financial KPIs', expanded=False):
        income_metrics = list(baselines.get("income_statement", {}).keys())
        balance_metrics = list(baselines.get("balance_sheet", {}).keys())
        cash_flow_metrics = list(baselines.get("cash_flow", {}).keys())

        income_column, balance_column, cash_flow_column = st.columns(3)
        with income_column:
            selected_income = st.multiselect(
                "Income Statement Pool",
                options=income_metrics,
                default=[m for m in ("COGS", "EBITDA") if m in income_metrics] or income_metrics,
                key=f"kpis_income_{ticker}",
            )
        with balance_column:
            selected_balance = st.multiselect(
                "Balance Sheet Pool",
                options=balance_metrics,
                default=[m for m in ("Total Debt", "Total Equity") if m in balance_metrics] or balance_metrics,
                key=f"kpis_balance_{ticker}",
            )
        with cash_flow_column:
            selected_cash_flow = st.multiselect(
                "Cash Flow Pool",
                options=cash_flow_metrics,
                default=["Free Cash Flow"] if "Free Cash Flow" in cash_flow_metrics else cash_flow_metrics,
                key=f"kpis_cash_{ticker}",
            )

    st.markdown("---")
    st.subheader("1. Scenario assumptions")
    price_selections=forecast_controls() if forecast_controls else {}
    prices_available=price_selections is not None
    price_selections=price_selections or {}
    price_horizon=st.session_state.get('forecast_horizon',30) if forecast_controls else 30
    baseline_context = st.session_state.get('baseline_context') or {}
    quarterly = baseline_context.get('form','').startswith('10-Q')
    spend_basis = 'year-to-date' if quarterly else 'annual'
    baseline_context = st.session_state.get('baseline_context')
    if baseline_context:
        st.caption(f"Baseline: {ticker} · {baseline_context.get('duration_basis','Annual')} · {baseline_context['period_end']} · USD millions")
    pass_through_pct = st.slider(
        "Assumed pass-through rate (% of cost surge offset)",
        min_value=0,
        max_value=100,
        value=75,
        step=5,
        key=f"pass_through_{ticker}",
    ) / 100.0

    material_configs: Dict[str, Dict[str, float]] = {}
    material_columns = st.columns(len(selected_metals))
    for index, metal in enumerate(selected_metals):
        with material_columns[index]:
            st.markdown(f"**{metal} scenario assumptions**")
            spend = st.number_input(f"Assumed {spend_basis} spend ($M)", value=25.0, min_value=0.0, step=5.0, key=f"spend_{metal}")
            unhedged = st.slider(
                "Assumed unhedged exposure (%)", 0, 100, 80, 5, key=f"unhedged_{metal}"
            ) / 100.0
            material_configs[metal] = {"annual_spend": spend, "unhedged_pct": unhedged}
            material_configs[metal].update(price_selections.get(metal,{}))

    st.markdown("<br>", unsafe_allow_html=True)
    run_simulation = st.button("Run scenario", type="primary",disabled=not prices_available)
    if run_simulation:
        try:
            if run_scenario is not None:
                if not baseline_context:
                    raise ValueError('Select a company report in Tab 1 before running a scenario.')
                request = ScenarioRequest(
                    WorkspaceContext(baseline_context['company_id'],baseline_context['snapshot_id'],
                                     baseline_context.get('duration_start'),baseline_context['period_end'] if baseline_context.get('duration_start') else None),
                    ticker,baseline_context['accession'],
                    tuple(ScenarioMaterial(name,cfg['annual_spend'],cfg['unhedged_pct'],**price_selections.get(name,{})) for name,cfg in material_configs.items()),
                    pass_through_pct,spend_basis,shock_horizon_days=price_horizon)
                with st.status('Agents are building the scenario…',expanded=True) as progress:
                    execution = run_scenario(request,on_step=lambda step:progress.write(f'{step.actor} · {step.name} · {step.status}: {step.detail}'))
                    st.session_state['sim_execution'] = execution
                    progress.update(label='Scenario ready' if execution.status=='ok' else 'Scenario has limitations' if execution.status=='partial' else 'Scenario could not complete',
                                    state='error' if execution.status=='failed' else 'complete',expanded=execution.status=='failed')
                if execution.status == 'failed':
                    for key in ('sim_results','sim_context'):
                        st.session_state.pop(key,None)
                    if render_graph:
                        render_graph()
                    render_execution_record(execution)
                    return
                simulation, signal = execution.simulation, execution.highlight
                baselines = execution.baseline['pools']
                st.session_state['baselines'] = baselines
            else:
                simulation = run_stress_test(material_configs, pass_through_pct, baselines)
                signal = capture_stress_context(ticker, material_configs, pass_through_pct, baselines, simulation)
                if baseline_context:
                    signal = replace(signal,company_id=baseline_context['company_id'],snapshot_id=baseline_context['snapshot_id'],horizon=f'30-day market shock applied to {spend_basis} spend inputs')
            if baseline_context:
                st.session_state.pop(f'graph_snapshot_{ticker}',None)
                st.session_state['graph_requested_snapshot'] = baseline_context['snapshot_id']
            st.session_state['sim_market_version'] = st.session_state.get('market_version')
            st.session_state['sim_results'] = simulation
            st.session_state['sim_context'] = signal
            st.session_state['graph_overlay_mode'] = 'Latest Tab 2 run'
        except ValueError as error:
            st.error(str(error))
            return

    saved_context = st.session_state.get('sim_context')
    st.session_state['sim_inputs_current'] = bool(
        saved_context and st.session_state.get('sim_market_version') == st.session_state.get('market_version') and saved_context.input_signature == stress_input_signature(
            ticker, material_configs, pass_through_pct, baselines
        ) and (not forecast_controls or (st.session_state.get('sim_execution') and st.session_state['sim_execution'].request.shock_horizon_days==price_horizon))
    )

    execution = st.session_state.get('sim_execution')
    if saved_context and not st.session_state['sim_inputs_current']:
        st.warning('Assumptions or market data changed. The graph highlights are cleared; financial results below belong to the previous run. Run the scenario again to update all views.')
    if render_graph:
        render_graph()
    if not run_simulation and "sim_results" not in st.session_state:
        if isinstance(execution, ScenarioResult):
            with st.expander('Scenario execution record'):
                render_execution_record(execution)
        return

    simulation = st.session_state.get("sim_results", {})
    if not st.session_state['sim_inputs_current']:
        st.warning('These results belong to an earlier input configuration or market-data refresh. Rerun the stress test to refresh results and graph highlights.')
    for warning in simulation.get("warnings",[]):
        st.warning(warning)
    waterfall = simulation.get("waterfall_summary", {})
    pools = simulation.get("stressed_pools", {})

    st.markdown("---")
    render_canvas_anchor('impact')
    st.subheader("3. Financial impact")
    st.markdown("**Cost transmission**")
    st.write(build_waterfall_message(waterfall, simulation.get('materials',{}), ticker))
    st.markdown('**Where the COGS and operating-income changes come from**')
    st.dataframe(contribution_rows(simulation), hide_index=True, width='stretch')
    with st.expander('Calculation basis'):
        st.write('User assumptions: period spend × price shock × unhedged fraction × (1 − pass-through). Starting spend $25M, unhedged 80%, and pass-through 75% are illustrative defaults, not reported contract terms. The entered period spend is exposed to a sustained price change; it is not prorated to the forecast horizon.')
        st.write('Product allocation: unavailable without validated consumption weights and contract terms. The graph does not allocate financial losses to products.')

    st.markdown("**Monitored KPI impacts**")
    _render_kpi_pool(
        "##### Income Statement Metrics ($M)", selected_income, "income_statement", pools
    )
    _render_kpi_pool(
        "##### Balance Sheet Metrics ($M)", selected_balance, "balance_sheet", pools
    )
    _render_kpi_pool(
        "##### Cash Flow Statement Metrics ($M)", selected_cash_flow, "cash_flow", pools
    )
    if isinstance(execution, ScenarioResult) and execution.status in ('ok','partial'):
        render_scenario_comparison(execution, st.session_state['sim_inputs_current'])

    if isinstance(execution, ScenarioResult) and execution.status in ('ok','partial') and forecast_controls:
        from .company_risk import render_company_risk
        render_company_risk(execution, st.session_state['sim_inputs_current'])

    if not forecast_controls:
        st.markdown("---")
        st.subheader("4. Market history & illustrative forward paths")
        st.markdown("##### a. Historical Price Trends (Local Futures History)")
        history_columns = st.columns(max(1, len(simulation.get("materials", {}))))
        for index, (metal, material_data) in enumerate(simulation.get("materials", {}).items()):
            with history_columns[index]:
                st.caption(f"{material_data.get('market_ticker', metal)} · {material_data.get('price_unit', 'provider quote units')} · latest completed session {material_data.get('market_as_of', 'unknown')}")
                st.plotly_chart(
                    build_history_chart(metal, material_data["history_df"]),
                    width="stretch",
                )

        st.markdown("##### b. Stressed Forward Projections (Geometric Brownian Motion + 95% Confidence Band)")
        stochastic_columns = st.columns(max(1, len(simulation.get("materials", {}))))
        for index, (metal, material_data) in enumerate(simulation.get("materials", {}).items()):
            with stochastic_columns[index]:
                projection = material_data.get("stochastic_projection", {})
                if projection.get("days", []):
                    st.plotly_chart(
                        build_stochastic_chart(metal, material_data),
                        width="stretch",
                    )

    if isinstance(execution, ScenarioResult):
        with st.expander('How this scenario was produced — agents, assumptions and evidence'):
            render_execution_record(execution)



def render_scenario_comparison(execution, inputs_current):
    with st.expander('Compare scenarios', expanded=False):
        if st.button('Save this run as comparison reference', disabled=not inputs_current):
            st.session_state['sim_comparison_reference'] = execution
        reference = st.session_state.get('sim_comparison_reference')
        if reference is None:
            return
        st.caption(f'Reference {reference.run_id[:8]} · current {execution.run_id[:8]}')
        if st.button('Clear comparison reference'):
            st.session_state.pop('sim_comparison_reference', None)
            st.rerun()
        try:
            comparison = compare_scenarios(reference, execution)
        except ValueError as error:
            st.warning(str(error))
            return
        st.dataframe(comparison['assumptions'], hide_index=True, width='stretch')
        st.markdown('**Change in material contributions versus reference**')
        st.dataframe(comparison['materials'], hide_index=True, width='stretch')
        st.markdown('**Financial figures versus reference and reported baseline**')
        st.dataframe(comparison['kpis'], hide_index=True, width='stretch')


def render_execution_record(result):
    st.subheader('How this result was produced')
    st.caption(f'Run {result.run_id[:8]} · {result.request.ticker} · report {result.request.accession} · {result.status}. Recorded actions, sources and outputs from this run.')
    completed = [step for step in result.trace if step.status != 'running']
    for step in completed:
        st.markdown(f"**{step.actor} — {step.name}** · {step.status}")
        st.write(step.detail)
    for warning in result.warnings:
        st.warning(warning)
    with st.expander('Inspect inputs, assumptions and source evidence'):
        st.write('User assumptions')
        st.dataframe([asdict(m) for m in result.request.materials],hide_index=True,width='stretch')
        st.write(f'Pass-through: {result.request.pass_through_fraction:.0%} · Spending basis: {result.request.spend_basis} · USD millions')
        for assumption in result.assumptions:
            st.caption(assumption)
        st.write('Market observations used')
        st.json(result.market_snapshot)
        st.write('Evidence supporting the selected report and graph')
        st.json(list(result.evidence_references))
    import json
    st.download_button('Download scenario execution record',
        data=json.dumps(result.to_dict(),indent=2,allow_nan=False),file_name=f'scenario-{result.run_id}.json',
        mime='application/json',key='download_scenario_'+result.run_id)
