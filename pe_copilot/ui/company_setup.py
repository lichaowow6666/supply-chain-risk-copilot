"""One-company-at-a-time preparation and explicit isolation of analysis state."""
import streamlit as st


from pe_copilot.workspace_state import reset_analysis_state, activate_prepared_company


def render_company_setup(orchestrator):
    st.sidebar.title('Company setup')
    with st.sidebar.form('company_setup'):
        query = st.text_input('Company name or ticker', placeholder='Enter one listed company', key='company_query')
        refresh = st.checkbox('Check SEC for new filings', key='company_refresh')
        submit = st.form_submit_button('Prepare company & scan', type='primary')
    st.sidebar.caption('One active company · three annual reporting years plus available quarterly filings. Completed local data is reused.')
    if submit:
        try:
            with st.status('Preparing company filings and evidence…', expanded=True) as status:
                progress = st.empty()
                prepared = orchestrator.prepare_company(query, progress.write, refresh)
                company = prepared['company']
                materials, baselines = orchestrator.discover_target(company['ticker'])
                reset_analysis_state(st.session_state)
                st.session_state['active_company'] = company
                st.session_state['preparation_info'] = prepared
                st.session_state['top_materials'] = materials
                st.session_state['baselines'] = baselines
                st.session_state['discovery_attempted'] = True
                st.session_state['baseline_context'] = {'company_id':'company:'+company['cik'],
                    'snapshot_id':prepared['baseline_snapshot_id'],'period_end':prepared['baseline']['period_end'],
                    'accession':prepared['baseline']['accession'],'currency':prepared['baseline']['currency']}
                status.update(label='Data already exists — scan complete' if prepared['reused'] else 'Preparation complete — scan complete',state='complete',expanded=False)
        except (ValueError,RuntimeError,OSError) as error:
            st.error(str(error))
    return st.session_state.get('active_company')


def render_report_selection(orchestrator, ticker):
    service = orchestrator.preparation_service
    reports = service.reports(ticker)
    by_id = {r['accessionNumber']:r for r in reports}
    default = st.session_state.get('baseline_context', {}).get('accession')
    if default not in by_id:
        default = next((r['accessionNumber'] for r in reports if r['form']=='10-K'), reports[0]['accessionNumber'])
    accession = st.selectbox('Analysis report', list(by_id), index=list(by_id).index(default),
        format_func=lambda a:f"{by_id[a]['form']} · period ending {by_id[a]['reportDate']} · filed {by_id[a]['filingDate']}",
        key=f'report_{ticker}')
    if st.session_state.get('selected_report_accession') != accession:
        baseline = service.report_baseline(ticker, accession)
        # Change all financial/evidence/graph scope together; discard prior simulations.
        for key in list(st.session_state):
            if key.startswith(('sim_', 'graph_', 'forecast_', 'materials_', 'kpis_', 'spend_', 'unhedged_', 'risk_')):
                del st.session_state[key]
        st.session_state['top_materials'] = []
        st.session_state['selected_metals'] = []
        st.session_state['discovery_attempted'] = False
        st.session_state['baselines'] = baseline['pools']
        st.session_state['selected_report_baseline'] = baseline
        st.session_state['selected_report_accession'] = accession
        st.session_state['baseline_context'] = {
            'company_id':'company:'+st.session_state['active_company']['cik'],
            'snapshot_id':baseline['snapshot_id'], 'period_end':baseline['period_end'],
            'accession':accession, 'currency':baseline['currency'],
            'form':baseline['form'], 'duration_basis':baseline['duration_basis'],
            'duration_start':baseline['duration_start']}
        st.session_state['graph_requested_snapshot'] = baseline['snapshot_id']
        with st.spinner('Searching the selected report for available futures exposures…'):
            materials, pools = orchestrator.discover_target(ticker, accession)
        st.session_state['top_materials'] = materials
        st.session_state['baselines'] = pools
        st.session_state['discovery_attempted'] = True
        # The sidebar is rendered first; refresh its scope label and source actions
        # immediately after a manual report change on the canvas.
        st.rerun()
