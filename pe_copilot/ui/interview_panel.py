"""Independent inspection of one frozen chat answer; no canvas mutations."""
from copy import deepcopy
from hashlib import sha256
import json
import re

import streamlit as st

from pe_copilot.chat.quality import build_evaluation_packet


def _label(value, default='Not recorded'):
    return str(value).replace('_', ' ').strip().capitalize() if value else default


def _seconds(value):
    return f'{value / 1000:.2f}s' if isinstance(value, (int, float)) else '—'


def evaluation_cache_key(packet, reference):
    """A changed reference cannot reuse a score calculated for another reference."""
    frozen = {'packet_id': packet['packet_id'], 'reference': (reference or '').strip(),
              'evaluation': 'interview-v1'}
    return sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _evaluate_interview(packet, reference):
    # Import only for an explicit request; opening inspection is a local read.
    from pe_copilot.chat.interview_quality import evaluate_interview
    return evaluate_interview(packet, reference=reference or None)


def _workflow_diagram(nodes):
    colors = {'completed': '#dff3e7', 'ok': '#dff3e7', 'waiting': '#fff0cb',
              'failed': '#ffe0e2', 'error': '#ffe0e2'}
    lines = ['digraph { rankdir=LR; bgcolor="transparent"; ',
             'node [shape=box style="rounded,filled" fontname="Arial" fontsize=12 color="#a4b3c3"];',
             'edge [color="#8193a7"];']
    for index, row in enumerate(nodes):
        name = _label(row.get('name'))
        status = _label(row.get('status'))
        label = json.dumps(name + '\n' + status)
        color = colors.get(row.get('status'), '#edf1f5')
        lines.append(f'n{index} [label={label} fillcolor="{color}"];')
        if index:
            lines.append(f'n{index - 1} -> n{index};')
    return '\n'.join(lines) + '\n}'


def _render_routing(diagnostics):
    route = diagnostics.get('route', {})
    decision, kind, canvas = st.columns(3)
    decision.metric('Decision', _label(route.get('action')))
    kind.metric('Answer type', _label(route.get('answer_kind')))
    canvas.metric('Canvas section', _label(route.get('section'), 'Unchanged'))
    if route.get('decision_source'):
        st.caption('Routing basis · ' + _label(route['decision_source']))
    st.markdown('**Workflow taken by this answer**')
    nodes = diagnostics.get('workflow_nodes', [])
    if nodes:
        st.graphviz_chart(_workflow_diagram(nodes), width='stretch')
    else:
        st.write('No workflow trace was recorded for this answer.')
    st.markdown('**Architecture · available connections**')
    st.graphviz_chart(r'''digraph {
        rankdir=LR; bgcolor="transparent";
        node [shape=box style="rounded,filled" fillcolor="#edf1f5" color="#a4b3c3" fontname="Arial" fontsize=11];
        edge [color="#8193a7" style=dashed];
        request [label="Question + workspace"];
        router [label="LangGraph router"];
        services [label="Existing orchestrator\n+ specialist services"];
        result [label="Answer, canvas action\nor clarification"];
        request -> router; router -> services; router -> result; services -> result;
    }''', width='stretch')
    st.caption('LangGraph selects the workflow. The orchestrator and specialist services supply evidence or calculations when needed; the rows below show what this answer used.')
    st.markdown('**Agents and reusable services**')
    agents = diagnostics.get('agents', [])
    if agents:
        st.dataframe([{'Capability': row.get('name'), 'This answer': _label(row.get('status')),
                       'Recorded activities': row.get('activity_count', 0)} for row in agents],
                     hide_index=True, width='stretch')
    else:
        st.write('Service activity was not recorded for this answer.')
    with st.expander('Activity details'):
        activities = diagnostics.get('activities', [])
        if activities:
            st.dataframe([{'Service': row.get('actor'), 'Operation': row.get('operation') or row.get('name'),
                           'Status': _label(row.get('status')), 'Detail': row.get('detail') or '—'}
                          for row in activities], hide_index=True, width='stretch')
        st.caption('Reused means an existing result was read. Not invoked means no call was recorded for this answer; older answers may have incomplete receipts.')
        saved = diagnostics.get('saved_execution')
        if saved:
            st.markdown('**Original saved scenario execution**')
            st.caption(saved.get('timing_scope', 'This earlier run is separate from the current answer.'))
            if saved.get('trace'):
                st.dataframe([{'Service': row.get('actor'), 'Operation': row.get('operation') or row.get('name'),
                               'Status': _label(row.get('status')), 'Original elapsed': _seconds(row.get('duration_ms'))}
                              for row in saved['trace']], hide_index=True, width='stretch')


def _render_evaluation(packet):
    st.caption('Evaluate this saved answer and its original evidence. Opening this panel does not call an evaluation model.')
    references = st.session_state.setdefault('interview_references', {})
    reference_key = 'interview_reference_' + packet['packet_id']
    if reference_key not in st.session_state:
        st.session_state[reference_key] = references.get(packet['packet_id'], '')
    with st.expander('Independent reviewer reference · optional'):
        reference = st.text_area('Reference answer', key=reference_key,
                                 placeholder='An independently authored expected answer, not a copy of the copilot response.',
                                 max_chars=12000, height=120)
    references[packet['packet_id']] = reference
    cache_key = evaluation_cache_key(packet, reference)
    results = st.session_state.setdefault('interview_evaluations', {})
    if st.button('Run evaluation', key='interview_evaluate_' + packet['packet_id'], type='primary'):
        with st.spinner('Evaluating this saved answer…'):
            try:
                results[cache_key] = _evaluate_interview(deepcopy(packet), reference.strip())
            except Exception as error:
                results[cache_key] = {'status': 'unavailable', 'scores': {}, 'metrics': [],
                    'reason': f'Evaluation could not finish ({type(error).__name__}).',
                    'limitations': ['The saved answer and current analysis have not changed.']}
    result = results.get(cache_key)
    if not result:
        st.write('No evaluation has been run for this answer and reference.')
        st.caption('Without a reviewer reference, reference-based metrics remain not applicable.')
        return
    st.markdown('**Evaluation · ' + _label(result.get('status')) + '**')
    if result.get('reason'):
        st.write(result['reason'])
    metrics = result.get('metrics', [])
    if metrics:
        st.dataframe([{'Metric': row.get('label', _label(row.get('key'))),
                       'Score': row.get('score'), 'Status': _label(row.get('status')),
                       'Elapsed': _seconds(row.get('duration_ms')),
                       'Meaning / applicability': row.get('reason') or '—'} for row in metrics],
                     column_config={'Score': st.column_config.NumberColumn('Score', format='%.2f')},
                     hide_index=True, width='stretch')
    if result.get('claim_checks'):
        with st.expander('Claim-by-claim evidence check'):
            for claim in result['claim_checks']:
                status = 'Supported' if claim.get('verdict') == 1 else 'Needs review'
                statement = re.sub(r'(?<!\\)\$', r'\\$', str(claim.get('statement', '')))
                st.markdown(f'**{status}** · {statement}')
                if claim.get('reason'):
                    st.write(claim['reason'])
    st.caption(' · '.join([
        'Judge: ' + str(result.get('judge_provider') or 'not invoked') + ' / ' + str(result.get('judge_model') or '—'),
        'RAGAS ' + str(result.get('ragas_version') or 'not recorded'),
        'Evaluation: ' + (f"{result['evaluation_seconds']:.1f}s" if isinstance(result.get('evaluation_seconds'), (int, float)) else '—'),
    ]))
    with st.expander('Interpretation and limitations'):
        for limitation in result.get('limitations', []):
            st.write('• ' + str(limitation))


def _render_latency(diagnostics):
    timing = diagnostics.get('timing', {})
    total, model, api = st.columns(3)
    total.metric('Server-side answer duration', _seconds(diagnostics.get('total_ms')))
    model.metric('Language calls', _seconds(timing.get('model_calls_ms')))
    api.metric('Provider round trips', _seconds(timing.get('provider_roundtrips_ms')))
    st.caption(timing.get('note') or 'Workflow, service and model timings can overlap; these groups must not be added together.')
    nodes = diagnostics.get('workflow_nodes', [])
    if nodes:
        st.markdown('**Workflow timing**')
        st.dataframe([{'Step': _label(row.get('name')), 'Status': _label(row.get('status')),
                       'Elapsed': _seconds(row.get('duration_ms'))} for row in nodes],
                     hide_index=True, width='stretch')
    activities = diagnostics.get('activities', [])
    if activities:
        st.markdown('**Agent and service timing**')
        st.dataframe([{'Service': row.get('actor'), 'Operation': row.get('operation') or row.get('name'),
                       'Status': _label(row.get('status')), 'Elapsed': _seconds(row.get('duration_ms'))}
                      for row in activities], hide_index=True, width='stretch')
    calls = diagnostics.get('language_calls', [])
    if calls:
        st.markdown('**Language model calls**')
        st.dataframe([{'Purpose': _label(row.get('purpose')), 'Provider': row.get('provider') or '—',
                       'Model': row.get('model') or '—', 'Status': _label(row.get('status')),
                       'Elapsed': _seconds(row.get('duration_ms')),
                       'API round trip': _seconds(row.get('request_roundtrip_ms')),
                       'Input tokens': row.get('input_tokens'), 'Output tokens': row.get('output_tokens'),
                       'Reasoning tokens': row.get('reasoning_tokens')} for row in calls],
                     hide_index=True, width='stretch')
    stages = diagnostics.get('retrieval_stages', [])
    if stages:
        st.markdown('**Evidence retrieval**')
        st.dataframe([{'Stage': _label(row.get('name')), 'Status': _label(row.get('status')),
                       'Elapsed': _seconds(row.get('duration_ms')), 'Detail': row.get('detail') or '—'}
                      for row in stages], hide_index=True, width='stretch')
    with st.expander('Measurement limits'):
        st.write('Internal model thinking time and time to first token are not measured by the current non-streaming API calls.')
        for limitation in diagnostics.get('limitations', []):
            st.write('• ' + str(limitation))
    saved = diagnostics.get('saved_execution')
    if saved:
        with st.expander('Original saved scenario timing'):
            st.caption(saved.get('timing_scope', 'Separate from the current answer duration.'))
            st.metric('Original scenario elapsed', _seconds(saved.get('duration_ms')))
            if saved.get('trace'):
                st.dataframe([{'Service': row.get('actor'), 'Operation': row.get('operation') or row.get('name'),
                               'Status': _label(row.get('status')), 'Original elapsed': _seconds(row.get('duration_ms'))}
                              for row in saved['trace']], hide_index=True, width='stretch')


def render_answer_inspection(message, allow_usage=True):
    """Pure inspection of a frozen message, also independently testable."""
    from pe_copilot.chat.diagnostics import build_diagnostics
    frozen = deepcopy(message)
    packet = build_evaluation_packet(frozen.get('question', ''), frozen)
    diagnostics = build_diagnostics(frozen)
    scope = frozen.get('scope', {})
    labels = [str(scope.get(key)) for key in ('ticker', 'form', 'period_end') if scope.get(key)]
    if labels:
        st.caption(' · '.join(labels) + ' · saved answer')
    if frozen.get('question'):
        st.markdown('**Question** · ' + re.sub(r'(?<!\\)\$', r'\\$', frozen['question']))
    with st.expander('Saved answer'):
        st.markdown(re.sub(r'(?<!\\)\$', r'\\$', str(frozen.get('text') or '')))
    routing, evaluation, latency = st.tabs(['Routing & agents', 'RAGAS evaluation', 'Latency'])
    with routing:
        _render_routing(diagnostics)
    with evaluation:
        _render_evaluation(packet)
    with latency:
        _render_latency(diagnostics)
    if allow_usage and st.session_state.get('pilot_measurement_enabled') is True:
        from pe_copilot.ui.interview_dashboard import record_interview_usage
        with st.expander('Task feedback · anonymous'):
            completed = st.radio('Did it help you complete your task?', ['Not answered', 'Yes', 'No'],
                                 horizontal=True, key='interview_completed_' + packet['packet_id'])
            if st.button('Save feedback', key='interview_feedback_' + packet['packet_id'],
                         disabled=completed == 'Not answered'):
                result = record_interview_usage('feedback', frozen.get('turn_id') or packet['packet_id'],
                    helpful=None,
                    task_completed=None if completed == 'Not answered' else completed == 'Yes')
                if result.get('status') in ('recorded', 'duplicate'):
                    st.success('Feedback saved.')
                else:
                    st.caption('Feedback could not be saved; the analysis is unchanged.')


@st.dialog('Inspect answer', width='large', on_dismiss='ignore')
def show_answer_panel(message, allow_usage=True):
    if allow_usage and st.session_state.get('pilot_measurement_enabled') is True:
        from pe_copilot.ui.interview_dashboard import record_interview_usage
        record_interview_usage('inspection_opened', message.get('turn_id'))
    render_answer_inspection(message, allow_usage=allow_usage)
