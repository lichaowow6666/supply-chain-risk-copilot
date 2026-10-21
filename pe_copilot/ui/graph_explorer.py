"""Tab 3 workbench over the same governed graph request used by future chat."""
import streamlit as st

from pe_copilot.contracts import GraphRequest, HighlightContext, WorkspaceContext
from pe_copilot.supply_chain.explorer import LENSES
from .graph_visualization import render_graph_html
from pe_copilot.supply_chain.scenario_impacts import apply_saved_annotations
from .navigation import render_canvas_anchor


def _period_label(period):
    start, end = period
    if start[5:] == '01-01' and end[5:] == '12-31' and start[:4] == end[:4]:
        return f'{end[:4]} annual figures'
    from pe_copilot.filings.history import period_kind
    return f'{period_kind(start,end)} · {start} to {end}'


def render_supply_chain_tab(orchestrator, ticker, pinned_context=None):
    render_canvas_anchor('graph')
    st.subheader('2. Supply-chain scenario map' if pinned_context else 'Supply-chain evidence explorer')
    try:
        snapshots = orchestrator.get_graph_snapshots(ticker)
    except (ValueError, KeyError, OSError) as error:
        st.error(f'The local graph could not be loaded: {error}')
        return
    if not snapshots:
        st.info('No evidence graph is available for this company yet.')
        return
    if pinned_context:
        selected = next((s for s in snapshots if s['company_id']==pinned_context['company_id'] and s['snapshot_id']==pinned_context['snapshot_id']),None)
        if selected is None:
            st.warning('The graph for the selected report is unavailable. Financial results remain below; no other filing was substituted.')
            return
        st.caption(f"{ticker} · {pinned_context.get('form','SEC report')} · {pinned_context['period_end']}")
        second, third = st.columns([2,1])
    else:
        first, second, third = st.columns([2, 2, 1.5])
        with first:
            selected = st.selectbox('Company / filing snapshot', snapshots,
                format_func=lambda s: f'{s["ticker"]} · {" / ".join(s["forms"])} · period ended {s["period_end"]} · {s["snapshot_id"]}',
                index=next((i for i,s in enumerate(snapshots) if s['snapshot_id']==st.session_state.get('graph_requested_snapshot')),0),
                key=f'graph_snapshot_{ticker}')
    scope = selected['company_id'] + ':' + selected['snapshot_id']
    with second:
        lens = st.selectbox('Relationship view', list(LENSES), index=3 if selected.get('automated') else 0, format_func=LENSES.get, key='graph_lens')
    with third:
        if pinned_context:
            wanted = (pinned_context.get('duration_start'), pinned_context['period_end'])
            period = wanted if wanted in selected['periods'] else None
        else:
            period = st.selectbox('Financial observation period', selected['periods'], format_func=_period_label,
                                  key='graph_period_' + scope) if selected['periods'] else None
    context = WorkspaceContext(selected['company_id'], selected['snapshot_id'], *(period or (None, None)))
    with st.expander('Graph options'):
        confirmed_only = st.checkbox('Confirmed evidence only', value=not selected.get('automated',False), key='graph_confirmed_'+scope)
        mode = ('Latest Tab 2 run' if st.session_state.get('sim_context') else 'No scenario') if pinned_context else st.selectbox(
            'Exposure highlighting', ['No scenario', 'Latest Tab 2 run', 'Chatbot request'], key='graph_overlay_mode')
    base = orchestrator.invoke(GraphRequest(context, lens=lens, confirmed_only=confirmed_only))
    if base.status != 'ok':
        for warning in base.warnings:
            st.warning(warning)
        return
    entities = base.data['nodes']
    # Selection is handled inside the graph. Filtering the payload to a selected
    # chain would erase scenario-affected relationships outside that chain.
    focus = None
    signal = None
    if mode == 'Latest Tab 2 run':
        candidate = st.session_state.get('sim_context')
        if not isinstance(candidate, HighlightContext):
            st.info('Run a stress test in Tab 2 to highlight its simulated materials and exposure paths here.')
        elif not st.session_state.get('sim_inputs_current', False):
            st.warning('Tab 2 inputs or company selection changed after the saved run. Rerun the stress test before applying its graph highlights.')
        else:
            signal = candidate
    elif mode == 'Chatbot request':
        candidate = st.session_state.get('graph_external_highlight')
        if isinstance(candidate, HighlightContext) and candidate.source == 'chatbot':
            signal = candidate
        else:
            st.info('Chatbot question routing is in development. The shared graph request interface is ready for a future “Explore in graph” action.')
    if pinned_context and signal and (signal.company_id != context.company_id or signal.snapshot_id != context.snapshot_id):
        st.warning('The saved scenario belongs to another report; its highlights have been cleared.')
        signal = None
    request = GraphRequest(context, lens, focus, False, confirmed_only, signal)
    result = orchestrator.invoke(request)
    if result.status != 'ok':
        for warning in result.warnings:
            st.warning(warning)
        return
    payload = result.data
    if signal:
        payload = apply_saved_annotations(payload, st.session_state.get('sim_execution'))
    # Do not send hidden model reasoning or opaque application state to the component.
    st.iframe(render_graph_html(payload), height=700)
    if payload.get('scenario_explanations'):
        with st.expander('Scenario explanations'):
            for explanation in payload['scenario_explanations']:
                st.markdown('**' + explanation['material'] + '**')
                st.write(explanation['text'].replace('$', r'\$'))
                st.caption(explanation['formula'])
                for limit in explanation['limits']:
                    st.caption(limit)
                with st.expander('Supporting filing excerpts — ' + explanation['material']):
                    if explanation['evidence']:
                        for chunk in explanation['evidence']:
                            st.caption(' · '.join(str(chunk[k]) for k in ('section','filing_date','chunk_id') if chunk.get(k)))
                            st.text(chunk.get('text', 'Source excerpt unavailable'))
                            url = chunk.get('source_url', '')
                            if url.startswith(('https://', 'http://')):
                                st.markdown(f'[Open source filing]({url})')
                    else:
                        st.info('No material excerpts in this run. Inspect node/edge citations for disclosed connections.')
            st.caption('Revenue remains unchanged in the existing model; passed-through costs are an offset in its cost waterfall. The model does not estimate sales volume or demand responses.')
    render_entity_history(orchestrator, context, entities, focus)
    with st.expander('Financial observations and reported sensitivities'):
        selected_rows = [r for r in payload['observations'] if not focus or r['raw']['subject'] == focus or r['raw']['related_entity_id'] == focus]
        if selected_rows:
            st.dataframe([{'Metric':r['metric'], 'Value':r['value'], 'Period / horizon':r['period'],
                           'Basis':r['basis'], 'Scope':r['raw']['subject'], 'Assumptions':'; '.join(r['assumptions'])} for r in selected_rows],
                         hide_index=True, width="stretch")
        else:
            st.info('No quantified observations are available for the selected entity and period. Relationships remain inspectable.')
        st.caption('Disclosed annual sensitivities are shown separately by their horizon/as-of labels. No interpolation, annualization, or quarterly conversion is performed.')
    with st.expander('Relationships and source evidence'):
        if payload['edges']:
            labels = {n['id']:n['label'] for n in payload['nodes']}
            edges_by_id = {e['id']: e for e in payload['edges']}
            edge_labels = {e['id']: f'{labels[e["source"]]} → {e["label"]} → {labels[e["target"]]}' for e in payload['edges']}
            edge_id = st.selectbox('Inspect a relationship', list(edges_by_id),
                                   format_func=edge_labels.get,
                                   key='graph_evidence_'+scope+':'+lens+':'+str(focus))
            edge = edges_by_id[edge_id]
            for cite in edge['evidence']:
                ref = cite['reference']
                st.caption(f'{ref["source_document"]} · {ref["evidence_section"]} · filed {ref["filing_date"]} · {cite["locator"]}')
                st.text(ref['evidence_excerpt'])
                if (ref.get('source_url') or '').startswith(('https://', 'http://')):
                    st.markdown(f'[Open source filing]({ref["source_url"]})')
        else:
            st.info('No relationships match this view.')
    with st.expander('Coverage gaps, assumptions, and limits'):
        if selected.get('automated'):
            st.caption('Automated filing graph: narrative entities and relationships are labelled inferred (LLM-extracted; source quotations checked). Inline-XBRL financial facts are parsed deterministically. No human review is implied.')
        if signal and payload['overlay']['source']:
            labels = ', '.join(f'{s.material} {s.shock_fraction:+.2%}' for s in signal.material_shocks)
            st.caption(f'Exposure overlay: {labels or "selected entities"} · {signal.horizon or "question context"} · run {signal.run_id[:8]}. '
                       'Downstream effects are conditional, not allocated losses.')
        for warning in result.warnings:
            st.warning(warning)
        st.dataframe([{'Candidate':g['candidate'], 'Status':g['disposition'], 'Reason':g['reason']} for g in payload['gaps']],
                     hide_index=True, width="stretch")
        st.caption('Accepted records carry their review provenance: curated source review or automated extraction checks. Neither implies human approval. Graph financial-loss allocation is in development.')
    with st.expander('How this graph was produced'):
        st.caption(f'Graph request {result.run_id} · snapshot {context.snapshot_id} · {result.duration_ms:.0f} ms')
        st.dataframe([{'Step':s.name, 'Status':s.status, 'Detail':s.detail, 'Time (ms)':round(s.duration_ms,2)} for s in result.trace],
                     hide_index=True, width="stretch")
        st.json({'company_id':context.company_id,'snapshot_id':context.snapshot_id,
                 'observation_period_start':context.period_start,'observation_period_end':context.period_end,
                 'filings':payload['summary']['documents']}, expanded=False)


def render_entity_history(orchestrator, context, entities, default_focus):
    """History remains explicit about reporting duration and revision selection."""
    with st.expander('Entity history across annual and quarterly filings'):
        st.caption('Matching uses stable company-scoped identities and exact normalized names. Renames and segment reorganizations are not automatically merged. A relationship missing from a quarter may simply not be repeated.')
        if not st.checkbox('Show filing history', key='graph_show_history_'+context.company_id):
            return
        names = {n['id']:n['label']+' · '+n['kind'] for n in entities}
        ids = list(names)
        if not ids:
            return
        chosen = st.selectbox('Historical entity',ids,index=ids.index(default_focus) if default_focus in ids else 0,
                              format_func=names.get,key='graph_history_entity_'+context.company_id)
        policy = st.radio('Disclosure version',['Latest available disclosure','As originally filed'],horizontal=True,key='graph_history_policy_'+context.company_id)
        history = orchestrator.get_graph_history(context.company_id,chosen,'latest' if policy.startswith('Latest') else 'original')
        rows = history['observations']
        if rows:
            metrics = sorted({r['metric'] for r in rows})
            metric = st.selectbox('Historical metric',metrics,key='graph_history_metric_'+chosen)
            selected = [r for r in rows if r['metric']==metric]
            kinds = sorted({r['period_kind'] for r in selected})
            kind = st.selectbox('Reporting duration',kinds,key='graph_history_duration_'+chosen+metric)
            selected = [r for r in selected if r['period_kind']==kind]
            series = sorted({(r['unit'],r['currency'] or '',r['related_entity_id'] or '',r['basis']) for r in selected})
            unit = st.selectbox('Comparable series',series,format_func=lambda s:' · '.join(v for v in s if v),key='graph_history_series_'+chosen+metric+kind)
            selected = [r for r in selected if (r['unit'],r['currency'] or '',r['related_entity_id'] or '',r['basis'])==unit]
            import pandas as pd
            frame = pd.DataFrame(selected)
            valid = frame[frame['value'].notna()]
            if not valid.empty:
                # Index includes the start date so non-comparable intervals are not collapsed.
                plot = valid.assign(period=valid['period_start']+' → '+valid['period_end']).set_index('period')[['value']]
                st.line_chart(plot, y_label=f"{metric} ({unit[1]} {unit[0]})")
            st.dataframe(frame[['period_start','period_end','decimal_value','unit','currency','availability','basis','form','filing_date','accession','disclosure_changed','source_url']],hide_index=True,width='stretch')
            with st.expander('Historical observation provenance'):
                st.json([{'period_start':r['period_start'],'period_end':r['period_end'],'accession':r['accession'],'evidence':r['evidence']} for r in selected],expanded=False)
            st.caption('Quarterly, annual and cumulative year-to-date measures stay separate. No annualization, quarter interpolation or inferred Q4 calculation is performed. Latest disclosure may reflect a restatement; original disclosure preserves the earliest collected version.')
        else:
            st.info('No quantified historical observations are available for this entity. Its dated relationships remain inspectable below.')
        st.dataframe(history['relationships'],hide_index=True,width='stretch')
        st.caption('These are relationships disclosed in each individual filing. Absence is not proof that a facility closed, a product stopped, or a supply relationship ended.')
