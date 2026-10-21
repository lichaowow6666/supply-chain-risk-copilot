"""Optional, read-only dashboard for the scripted synthetic interview benchmark."""
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / 'evaluations/results/interview_100_latest.json'
METRICS = {
    'faithfulness': 'Evidence support',
    'answer_relevancy': 'Answer relevance',
    'context_precision_without_reference': 'Context precision',
    'context_recall': 'Reference context recall',
    'factual_correctness': 'Reference correctness',
}


def record_interview_usage(event, answer_id, *, helpful=None, task_completed=None):
    """Optional anonymous observations; never pass question/company/answer data."""
    if st.session_state.get('pilot_measurement_enabled') is not True:
        return {'status': 'disabled', 'stored': False}
    try:
        from pe_copilot.chat.adoption import anonymous_session_id, record_event
        identifier = st.session_state.get('pilot_measurement_session_id')
        if not identifier:
            identifier = anonymous_session_id()
            st.session_state['pilot_measurement_session_id'] = identifier
        return record_event(enabled=True, session_id=identifier, event=event, answer_id=answer_id,
                            helpful=helpful, task_completed=task_completed)
    except Exception:
        # Optional metadata must never interrupt a working answer or scenario.
        return {'status': 'unavailable', 'stored': False}


def _number(value):
    return value if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) else None


def _label(value):
    return str(value or 'Not recorded').replace('_', ' ').capitalize()


def _score(row, key):
    quality = row.get('quality') or {}
    status = next((metric.get('status') for metric in quality.get('metrics', [])
                   if metric.get('key') == key), None)
    if status and status != 'completed':
        return None
    return _number(quality.get('scores', {}).get(key))


def load_dashboard_report():
    """Only read authored cases and local reports; never execute benchmark work."""
    from pe_copilot.chat.benchmark_dataset import load_benchmark, DEFAULT_BENCHMARK
    dataset = load_benchmark()
    dataset_digest = sha256(DEFAULT_BENCHMARK.read_bytes()).hexdigest()
    catalog = dataset.get('cases', [])
    catalog_status, catalog_warning = 'current', None
    if REPORT_PATH.is_file():
        report = json.loads(REPORT_PATH.read_text())
        if report.get('synthetic') is not True:
            raise ValueError('The evaluation dashboard accepts synthetic benchmark reports only.')
        if report.get('suite_sha256') != dataset_digest:
            catalog_status = 'stale' if report.get('suite_sha256') else 'unverified'
            catalog_warning = ('Saved results use a different question-catalog version.' if catalog_status == 'stale'
                               else 'The saved report does not identify its question-catalog version.')
            catalog_warning += ' Showing saved results and their original references only; the current catalog is not merged into this run.'
            catalog = []
    else:
        report = {'schema_version': 1, 'suite_id': dataset.get('suite_id', dataset.get('version')),
                  'suite_sha256': dataset_digest, 'synthetic': True, 'status': 'not_run',
                  'case_count': len(catalog), 'results': []}
    return {**report, 'catalog': catalog, 'catalog_status': catalog_status, 'catalog_warning': catalog_warning}


def downloadable_report(report):
    """Export synthetic benchmark outputs, excluding full messages/evidence packets."""
    if report.get('synthetic') is not True:
        raise ValueError('Only a synthetic report can be downloaded here.')
    allowed = ('schema_version', 'suite_id', 'suite_sha256', 'run_id', 'synthetic', 'status',
               'started_at', 'finished_at', 'case_count', 'summary', 'limitations', 'synthetic_permission_check',
               'catalog_status', 'catalog_warning', 'protocol', 'versions', 'embedding_profile', 'setup_timings')
    exported = {key: deepcopy(report[key]) for key in allowed if key in report}
    exported['results'] = []
    row_keys = ('case_id', 'category', 'language', 'question', 'reference', 'response', 'status',
                'route', 'checks', 'capabilities', 'governance', 'task_outcome', 'diagnostics')
    quality_keys = ('status', 'scores', 'metrics', 'claim_checks', 'evaluation_seconds',
                    'judge_model', 'judge_provider', 'ragas_version', 'limitations')
    for row in report.get('results', []):
        cleaned = {key: deepcopy(row[key]) for key in row_keys if key in row}
        cleaned['quality'] = {key: deepcopy(row.get('quality', {})[key]) for key in quality_keys
                              if key in row.get('quality', {})}
        exported['results'].append(cleaned)
    return json.dumps(exported, ensure_ascii=False, indent=2, allow_nan=False)


def _metric_rows(summary, attempted):
    rows = []
    for key, label in METRICS.items():
        value = summary.get('ragas', {}).get(key, {})
        rows.append({'Metric': label, 'Mean': value.get('mean'), 'p50': value.get('median'),
                     'p95': value.get('p95'), 'Scored': value.get('count', 0),
                     'Attempted': attempted, 'Not applicable': value.get('not_applicable_count', 0),
                     'Unavailable': value.get('unavailable_count', 0),
                     'Missing': value.get('missing_count', max(0, attempted - value.get('count', 0)))})
    return rows


def _governance_rows(checks):
    return [{'Check': _label(row.get('name')), 'Passed': row.get('passed', 0),
             'Failed': row.get('failed', 0), 'Not applicable': row.get('not_applicable', 0),
             'Assessed': row.get('passed', 0) + row.get('failed', 0)} for row in checks]


def _category_rows(summary, metric):
    rows = []
    for category, values in sorted(summary.get('per_category', {}).items()):
        outcomes = values.get('task_outcomes', {})
        scores = values.get('ragas', {}).get(metric, {})
        rows.append({'Category': _label(category), 'Cases': values.get('attempt_count', 0),
                     'Workflow pass / assessed': f"{outcomes.get('passed', 0)} / {outcomes.get('assessed', 0)}",
                     'Failed contracts': outcomes.get('failed', 0),
                     'Mean score': scores.get('mean'), 'Scored': scores.get('count', 0),
                     'Not applicable': scores.get('not_applicable_count', 0),
                     'Unavailable': scores.get('unavailable_count', 0),
                     'Missing': scores.get('missing_count', 0)})
    return rows


def _render_quality(report, summary, results, attempted):
    rows = _metric_rows(summary, attempted)
    st.dataframe(rows, hide_index=True, width='stretch', column_config={
        key: st.column_config.NumberColumn(key, format='%.3f') for key in ('Mean', 'p50', 'p95')})
    st.caption('Each metric has its own denominator. Missing, unavailable and not applicable scores are excluded from averages, never replaced with zero. Unlike metrics are not combined.')
    with st.expander('Why a metric can be not applicable'):
        st.write('Navigation, preparation, clarification and fallback messages are excluded from these generated-answer metrics. General explanations skip evidence-based metrics. Recall and reference correctness require an independent expected answer; evidence metrics also require valid, bounded source context.')
        st.write('Eligibility currently follows the recorded answer type. A company question incorrectly routed as general can therefore skip evidence metrics. N/A is not a pass; review its routing checks and the saved reason. Judge failures are marked unavailable instead.')
    figure = go.Figure()
    colors = ['#3073bb', '#22a184', '#8270c7', '#ce9250', '#b26893']
    for (key, label), color in zip(METRICS.items(), colors):
        values = [_score(row, key) for row in results]
        values = [value for value in values if value is not None]
        if values:
            figure.add_trace(go.Box(y=values, name=label, boxpoints='all', jitter=.2,
                                   pointpos=0, marker_color=color, marker_size=4))
    if figure.data:
        figure.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=20),
                             showlegend=False, yaxis_title='Metric score', template='plotly_white')
        st.plotly_chart(figure, width='stretch', key='interview_quality_distribution')
    else:
        st.write('Scores will appear as the explicitly started benchmark completes its evaluations.')
    st.caption('Dots are individual questions; boxes show their distribution. A score of zero is a measured result. Reference correctness depends on the authored reference and judge.')
    if summary.get('per_category'):
        st.markdown('**Results by question category**')
        metric = st.selectbox('Compare categories by', list(METRICS), format_func=lambda key: METRICS[key],
                              key='interview_category_metric')
        st.dataframe(_category_rows(summary, metric), hide_index=True, width='stretch',
                     column_config={'Mean score': st.column_config.NumberColumn('Mean score', format='%.3f')})
        st.caption('Workflow contracts and RAGAS measure different things. Every attempted case remains visible, including failed contracts and unavailable scores.')
    if report.get('limitations'):
        with st.expander('Benchmark limitations'):
            for note in report['limitations']:
                st.write('• ' + str(note))


def _render_latency(summary, results):
    timing = summary.get('latency', {})
    labels = {'answer_ms': 'Answer generation', 'model_ms': 'Language model calls',
              'provider_ms': 'Provider round trips', 'judge_ms': 'RAGAS evaluation'}
    rows = []
    for key, label in labels.items():
        values = timing.get(key, {})
        rows.append({'Measurement': label, 'Measured': values.get('count', 0),
                     **{name: (_number(values.get(field)) / 1000 if _number(values.get(field)) is not None else None)
                        for name, field in [('Mean (s)', 'mean'), ('p50 (s)', 'median'), ('p90 (s)', 'p90'), ('p95 (s)', 'p95')]}})
    st.dataframe(rows, hide_index=True, width='stretch', column_config={
        key: st.column_config.NumberColumn(key, format='%.2f') for key in ('Mean (s)', 'p50 (s)', 'p90 (s)', 'p95 (s)')})
    st.caption('Server-side measured durations. Evaluation happens separately from answering. Workflow, service and model spans can overlap; they are not additive.')
    figure = go.Figure()
    for key, label in [('answer_ms', 'Answer'), ('judge_ms', 'RAGAS judge')]:
        values = []
        for row in results:
            if key == 'judge_ms' and ((row.get('quality') or {}).get('cache_hit')
                    or (row.get('quality') or {}).get('status') == 'not_applicable'):
                continue
            value = (row.get('diagnostics') or {}).get('total_ms') if key == 'answer_ms' else (row.get('quality') or {}).get('evaluation_seconds')
            value = _number(value)
            if value is not None:
                values.append(value / 1000 if key == 'answer_ms' else value)
        if values:
            figure.add_trace(go.Box(y=values, name=label, boxpoints='all', jitter=.2, marker_size=4))
    if figure.data:
        figure.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), showlegend=False,
                             yaxis_title='Seconds', template='plotly_white')
        st.plotly_chart(figure, width='stretch', key='interview_latency_distribution')
    span_mode = st.selectbox('Timing detail', ['Agent and service', 'Workflow stages', 'Retrieval stages'], key='interview_timing_detail')
    span_groups = {}
    for row in results:
        diagnostics = row.get('diagnostics') or {}
        field = {'Agent and service': 'activities', 'Workflow stages': 'workflow_nodes', 'Retrieval stages': 'retrieval_stages'}[span_mode]
        for span in diagnostics.get(field, []):
            duration = _number(span.get('duration_ms'))
            if duration is not None:
                label = span.get('actor') if field == 'activities' else span.get('name')
                span_groups.setdefault(label or 'Recorded span', []).append(duration / 1000)
    if span_groups:
        figure = go.Figure([go.Box(x=values, name=actor, boxpoints=False, orientation='h')
                            for actor, values in sorted(span_groups.items())])
        figure.update_layout(height=max(240, 45 * len(span_groups)), margin=dict(l=10, r=10, t=10, b=10),
                             showlegend=False, xaxis_title='Recorded span seconds · inclusive', template='plotly_white')
        st.plotly_chart(figure, width='stretch', key='interview_service_latency')
    st.caption('Timeouts and failures remain benchmark attempts. Duration is missing when it was not recorded; missing timings are not zero.')


def _render_adoption():
    from pe_copilot.chat.adoption import anonymous_session_id, summarize_events
    usage = summarize_events()
    feedback = usage.get('feedback', {})
    st.markdown('**Answer feedback**')
    positive, negative, total, rate = st.columns(4)
    positive.metric('Thumbs up', feedback.get('positive', 0))
    negative.metric('Thumbs down', feedback.get('negative', 0))
    total.metric('Rated answers', feedback.get('total', 0))
    fraction = feedback.get('positive_rate')
    rate.metric('Met expectations', f'{100 * fraction:.0f}%' if fraction is not None else '—')
    st.caption('Explicit user ratings, separate from RAGAS and scripted cases. Changing a vote replaces the previous rating.')
    reasons = [row for row in feedback.get('reason_counts', []) if row.get('count', 0)]
    if reasons:
        figure = go.Figure(go.Bar(x=[row['count'] for row in reasons],
                                 y=[row['label'] for row in reasons], orientation='h',
                                 marker_color='#2563eb'))
        figure.update_layout(height=max(220, len(reasons) * 42), template='plotly_white',
                             margin=dict(l=10, r=10, t=10, b=10), xaxis_title='Negative ratings',
                             yaxis=dict(autorange='reversed'))
        st.plotly_chart(figure, width='stretch', key='feedback_reasons_chart')
        st.dataframe([{'Reason': row['label'], 'Ratings': row['count'],
                       'Share of negative ratings': row['count'] / feedback['negative']}
                      for row in reasons], hide_index=True, width='stretch',
                     column_config={'Share of negative ratings': st.column_config.NumberColumn(format='percent')})
        st.caption(f"{feedback.get('negative_with_reasons', 0)} of {feedback.get('negative', 0)} negative ratings include reasons. Multiple reasons are allowed.")
    elif feedback.get('negative'):
        st.write('No reasons have been selected for the negative ratings yet.')
    else:
        st.write('Negative-feedback reasons will appear here after users submit them.')
    if usage.get('status') == 'unavailable':
        st.warning('Saved feedback is temporarily unavailable.')
    with st.expander('How feedback becomes improvements'):
        st.write('Review recurring reasons, reproduce the affected behavior, add a regression case, then compare the next version against the same evaluation set. Feedback does not automatically change prompts, calculations or model weights.')
    from pe_copilot.ui.feedback import answer_identity
    votes = st.session_state.get('answer_feedback', {})
    rated = {answer_identity(message): message for message in st.session_state.get('copilot_messages', [])
             if message.get('role') == 'assistant'
             and votes.get(answer_identity(message), {}).get('helpful') is not None
             and votes.get(answer_identity(message), {}).get('status') != 'unavailable'}
    if rated:
        with st.expander('Review rated answers in this conversation'):
            selected = st.selectbox('Rated answer', list(rated), key='feedback_review_answer',
                format_func=lambda key: ('Positive · ' if votes[key]['helpful'] else 'Negative · ')
                    + str(rated[key].get('question') or rated[key].get('text', ''))[:100])
            if st.button('Inspect rated answer', key='feedback_inspect_answer'):
                from pe_copilot.ui.interview_panel import show_answer_panel
                show_answer_panel(deepcopy(rated[selected]))
    st.divider()
    st.markdown('**Optional usage measurement**')
    enabled = st.toggle('Record anonymous usage counts', key='pilot_measurement_enabled', value=False)
    if enabled and not st.session_state.get('pilot_measurement_session_id'):
        st.session_state['pilot_measurement_session_id'] = anonymous_session_id()
    columns = st.columns(3)
    columns[0].metric('Observed sessions', usage.get('session_count', 0))
    columns[1].metric('Answers observed', usage.get('answers_rendered', 0))
    helpful = usage.get('helpfulness', {})
    value = helpful.get('value')
    columns[2].metric('Marked helpful', f'{100 * value:.0f}%' if isinstance(value, (int, float)) else '—',
                      help=f"{helpful.get('numerator', 0)} of {helpful.get('denominator', 0)} submitted helpfulness ratings")
    completion = usage.get('task_completion', {})
    st.caption(f"User-reported completion: {completion.get('numerator', 0)} / {completion.get('denominator', 0)} submitted ratings. Sessions are not unique users; these observations do not establish adoption or retention.")
    st.caption('Usage tracking is off by default. Explicit votes are saved independently. Only anonymous identifiers, ratings and selected reasons are stored; no questions, company names or answers.')


def _render_capabilities(summary, permission=None):
    outcomes = summary.get('task_outcomes', {})
    assessed = outcomes.get('assessed', 0)
    rate = outcomes.get('completion_rate')
    one, two, three = st.columns(3)
    one.metric('Scripted task completion proxy', f'{100 * rate:.0f}%' if isinstance(rate, (int, float)) else '—')
    two.metric('Contracts assessed', assessed)
    three.metric('Failed task contracts', outcomes.get('failed', 0))
    st.caption('Success against authored workflow contracts; this is not a user-adoption or satisfaction measure.')
    st.caption(f"Passed: {outcomes.get('passed', 0)} · Failed: {outcomes.get('failed', 0)} · Not assessed: {outcomes.get('not_assessed', 0)}")
    proxies = summary.get('adoption', {})
    proxy_rows = []
    automation = proxies.get('automation_coverage')
    if isinstance(automation, dict):
        value = _number(automation.get('value'))
        proxy_rows.append({'Scripted workflow observation': 'Completed without clarification',
                           'Observed': automation.get('numerator'), 'Attempts': automation.get('denominator'),
                           'Rate': f'{100 * value:.1f}%' if value is not None else '—'})
    if 'clarification_count' in proxies and 'case_count' in proxies:
        value = _number(proxies.get('clarification_rate'))
        proxy_rows.append({'Scripted workflow observation': 'Requested clarification',
                           'Observed': proxies['clarification_count'], 'Attempts': proxies['case_count'],
                           'Rate': f'{100 * value:.1f}%' if value is not None else '—'})
    if 'answer_fallback_count' in proxies and 'case_count' in proxies:
        value = _number(proxies.get('answer_fallback_rate'))
        proxy_rows.append({'Scripted workflow observation': 'Returned a fallback answer',
                           'Observed': proxies['answer_fallback_count'], 'Attempts': proxies['case_count'],
                           'Rate': f'{100 * value:.1f}%' if value is not None else '—'})
    if proxy_rows:
        st.dataframe(proxy_rows, hide_index=True, width='stretch')
        st.caption('Recorded workflow proxies, not answer-quality or user-adoption measures.')
    capabilities = summary.get('capabilities', {})
    operations = capabilities.get('operations', [])
    if operations:
        st.markdown('**Reusable capabilities observed**')
        st.dataframe([{'Capability': item.get('actor'), 'Operation': item.get('operation'),
                       'Calls': item.get('calls', 0), 'Completed': item.get('completed', 0),
                       'Reused': item.get('reused', 0), 'Failed': item.get('failed', 0),
                       'Scope count': item.get('scopes'),
                       'Question categories': ', '.join(item.get('categories', []))}
                      for item in operations], hide_index=True, width='stretch')
    governance = summary.get('governance', {})
    if governance.get('checks'):
        st.markdown('**Scope and workflow safeguards**')
        st.dataframe(_governance_rows(governance['checks']),
                     hide_index=True, width='stretch')
        with st.expander('How safeguards pass or fail'):
            st.write('These are project-specific deterministic checks against each authored case: expected action, answer type and canvas section; selected company/report identifiers; preserved analysis-state fingerprints; valid citation numbers; and source provenance metadata.')
            st.write('A workflow contract passes only when no applicable check fails. Missing applicability is shown separately. These checks do not establish that every claim is correct or that production access controls exist. Open a question in the catalog to compare its expected and actual route.')
    st.caption('Synthetic safeguard checks demonstrate these specific contracts; they do not certify enterprise authorization or production security.')
    if isinstance(permission, dict) and permission.get('checks'):
        with st.expander('Isolated synthetic permission-policy check'):
            st.caption('Actual decisions from the standalone synthetic policy. This is not an authenticated access-control layer on the live workspace.')
            st.dataframe([{'Resource': row.get('name'), 'Result': _label(row.get('status')),
                           'Scope': row.get('detail')} for row in permission['checks']],
                         hide_index=True, width='stretch')


def _render_catalog(report, results):
    catalog = {str(row.get('id') or row.get('case_id')): deepcopy(row) for row in report.get('catalog', [])}
    for result in results:
        key = str(result.get('case_id') or result.get('id'))
        catalog[key] = {**catalog.get(key, {}), **result, 'case_id': key}
    values = list(catalog.values())
    if not values:
        st.write('The question catalog is unavailable.')
        return
    first, second, third = st.columns(3)
    category = first.selectbox('Category', ['All categories'] + sorted({str(row.get('category', 'Uncategorized')) for row in values}), key='interview_catalog_category')
    language = second.selectbox('Language', ['All languages'] + sorted({str(row.get('language', 'Not specified')) for row in values}), key='interview_catalog_language')
    status = third.selectbox('Answer status', ['All statuses'] + sorted({str(row.get('status', 'not_run')) for row in values}), key='interview_catalog_status')
    selected = [row for row in values if (category == 'All categories' or row.get('category', 'Uncategorized') == category)
                and (language == 'All languages' or row.get('language', 'Not specified') == language)
                and (status == 'All statuses' or row.get('status', 'not_run') == status)]
    st.dataframe([{'ID': row.get('case_id') or row.get('id'), 'Category': row.get('category'),
                   'Language': row.get('language'), 'Question': row.get('question'),
                   'Answer status': _label(row.get('status', 'not_run')),
                   'Evaluation': _label((row.get('quality') or {}).get('status', 'not_run'))}
                  for row in selected], hide_index=True, width='stretch', height=330)
    if not selected:
        return
    choices = {str(row.get('case_id') or row.get('id')): row for row in selected}
    key = st.selectbox('Open question', list(choices), key='interview_catalog_case',
                       format_func=lambda value: value + ' · ' + str(choices[value].get('question', ''))[:100])
    row = choices[key]
    st.markdown('**' + str(row.get('question', '')) + '**')
    expected, actual = st.columns(2)
    with expected:
        st.markdown('**Authored expected answer**')
        st.write(row.get('reference') or 'No reference specified.')
    with actual:
        st.markdown('**Actual copilot answer**')
        st.write(row.get('response') or 'Not available yet.')
    if isinstance(row.get('reply'), dict) and row['reply'].get('text'):
        if st.button('Inspect this answer', key='interview_benchmark_inspect_' + key):
            from pe_copilot.chat.quality import build_evaluation_packet
            from pe_copilot.ui.interview_panel import show_answer_panel, evaluation_cache_key
            packet = build_evaluation_packet(row['reply'].get('question', row.get('question', '')), row['reply'])
            quality = row.get('quality') or {}
            reference = str(row.get('reference') or '').strip()
            if quality.get('packet_id') == packet['packet_id']:
                st.session_state['interview_reference_' + packet['packet_id']] = reference
                st.session_state.setdefault('interview_evaluations', {})[
                    evaluation_cache_key(packet, reference)] = deepcopy(quality)
            # Scripted benchmark inspection is not observed user adoption.
            show_answer_panel(deepcopy(row['reply']), allow_usage=False)
    quality = row.get('quality') or {}
    if quality.get('metrics'):
        with st.expander('Saved per-question evaluation'):
            st.dataframe([{'Metric': item.get('label') or _label(item.get('key')),
                           'Score': item.get('score'), 'Status': _label(item.get('status')),
                           'Detail': item.get('reason')} for item in quality['metrics']],
                         hide_index=True, width='stretch')
    checks = row.get('checks') or (row.get('assessment') or {}).get('checks')
    if checks:
        with st.expander('This question’s workflow checks'):
            outcome = row.get('task_outcome') or row.get('assessment', {}).get('task_outcome', {})
            st.markdown('**Workflow contract · ' + _label(outcome.get('status')) + '**')
            expected_route, actual_route = row.get('expected', {}), row.get('route', {})
            if expected_route:
                st.dataframe([{'Decision': label, 'Expected': ', '.join(expected_route.get(expected_key, []))
                    if expected_key != 'section' else expected_route.get('section', 'Not specified'),
                    'Actual': actual_route.get(actual_key) or 'Not recorded'}
                    for label, expected_key, actual_key in [('Action', 'actions', 'action'),
                        ('Answer type', 'answer_kinds', 'answer_kind'), ('Canvas section', 'section', 'section')]],
                    hide_index=True, width='stretch')
            st.dataframe(checks, hide_index=True, width='stretch')


def render_interview_dashboard(report=None):
    """Read-only benchmark UI; the caller owns its optional workbench tab."""
    st.subheader('Copilot evaluation')
    try:
        report = load_dashboard_report() if report is None else deepcopy(report)
        if report.get('synthetic') is not True:
            raise ValueError('A synthetic benchmark report is required.')
        from pe_copilot.chat.benchmark_metrics import summarize
        results = report.get('results', [])
        planned = report.get('case_count', 100)
        summary = summarize(results, case_count=planned)
    except (ImportError, OSError, ValueError, TypeError, KeyError) as error:
        st.info(f'The evaluation benchmark is being prepared ({type(error).__name__}).')
        return
    st.caption(f'{planned} scripted synthetic questions · authored references · development benchmark, not a held-out or real-user study.')
    if report.get('catalog_warning'):
        st.warning(report['catalog_warning'])
    attempted = summary.get('attempt_count', len(results))
    answered = sum(bool(row.get('response')) for row in results)
    judged = sum(any(_score(row, key) is not None for key in METRICS) for row in results)
    count, answers, scores, state = st.columns(4)
    count.metric('Attempted', f'{attempted} / {planned}')
    answers.metric('Answers returned', answered)
    scores.metric('Answers scored', judged)
    state.metric('Run status', _label(report.get('status')))
    st.progress(min(1., attempted / planned) if planned else 0.)
    quality, latency, safeguards, feedback, catalog = st.tabs(['Response quality', 'Latency', 'Capabilities & governance', 'Feedback', '100-question catalog'], key='interview_dashboard_tabs')
    with quality:
        _render_quality(report, summary, results, attempted)
    with latency:
        _render_latency(summary, results)
    with safeguards:
        _render_capabilities(summary, report.get('synthetic_permission_check'))
    with feedback:
        _render_adoption()
    with catalog:
        _render_catalog(report, results)
    download, refresh = st.columns([.8, .2])
    with download:
        st.download_button('Download synthetic results', downloadable_report({**report, 'summary': summary}),
                           file_name='copilot-evaluation-synthetic-results.json', mime='application/json')
    with refresh:
        if st.button('Refresh report', key='interview_refresh_report'):
            st.rerun()
