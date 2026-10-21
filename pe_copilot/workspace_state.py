"""Shared analysis state used by both conversation and canvas."""

_PERSISTENT_WIDGETS = {'workbench_tab', 'forecast_horizon', 'graph_lens', 'graph_overlay_mode'}
_PERSISTENT_PREFIXES = ('report_', 'materials_', 'kpis_', 'spend_', 'unhedged_', 'pass_through_',
    'forecast_model_', 'forecast_compare_', 'forecast_price_', 'forecast_shock_', 'forecast_ceiling_',
    'graph_snapshot_', 'graph_period_', 'graph_confirmed_', 'graph_focus_', 'graph_evidence_',
    'graph_show_history_', 'graph_history_', 'risk_metric_', 'risk_threshold_', 'risk_direction_')


def preserve_analysis_widgets(state):
    """Retain canvas values when a sidebar action reruns before widgets render.

    Promote only durable controls to application-owned session values. Button
    triggers and chart/component events must never be persisted or replayed.
    Call at the start of the run, before rendering any widgets or resetting scope.
    """
    for key in list(state):
        if key in _PERSISTENT_WIDGETS or key.startswith(_PERSISTENT_PREFIXES):
            state[key] = state[key]

def reset_analysis_state(state):
    exact = {'baselines','top_materials','selected_metals','discovery_attempted','baseline_context','preparation_info',
             'selected_report_baseline','selected_report_accession','workbench_tab', 'Income Statement Pool','Balance Sheet Pool','Cash Flow Pool','Choose Target Commodity Exposures:'}
    for key in list(state):
        if key in exact or key.startswith(('sim_','graph_','forecast_','spend_','unhedged_','materials_','kpis_','pass_through_','report_','risk_')):
            del state[key]
    state.pop('canvas_focus_request', None)


def activate_prepared_company(state, prepared, baseline, materials, pools):
    """Publish a complete prepared workspace together, preserving conversation history.

    Call only after preparation and the selected-report scan succeed, so a failed
    company switch leaves the current analysis usable.
    """
    company = prepared['company']
    current = state.get('baseline_context', {})
    same_report = (current.get('company_id') == 'company:' + company['cik']
                   and current.get('accession') == baseline['accession']
                   and state.get('selected_report_baseline', {}).get('source_sha256') == baseline.get('source_sha256'))
    if not same_report:
        reset_analysis_state(state)
    state.update(active_company=company, preparation_info=prepared,
                 selected_report_baseline=baseline, selected_report_accession=baseline['accession'],
                 top_materials=materials, baselines=pools, discovery_attempted=True)
    state['baseline_context'] = {
        'company_id': 'company:' + company['cik'], 'snapshot_id': baseline['snapshot_id'],
        'accession': baseline['accession'], 'period_end': baseline['period_end'],
        'currency': baseline['currency'], 'form': baseline['form'],
        'duration_basis': baseline['duration_basis'], 'duration_start': baseline.get('duration_start')}
    state['graph_requested_snapshot'] = baseline['snapshot_id']
    state[f"report_{company['ticker']}"] = baseline['accession']
