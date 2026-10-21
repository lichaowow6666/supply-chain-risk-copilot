"""Passive, bounded execution receipts for one saved conversation response.

These records describe measured application activity, never hidden model thoughts.
Nested workflow, service, retrieval and model spans are deliberately kept apart.
"""
import math
import re


_ACTORS = ('Orchestrator', 'Fundamental Agent', 'Market Agent',
           'Forecasting Agent', 'Graph service', 'LLM')
_ACTOR_ALIASES = {'FundamentalAgent': 'Fundamental Agent', 'MarketAgent': 'Market Agent',
                  'ForecastingAgent': 'Forecasting Agent'}
_STATUSES = {'completed', 'failed', 'reused', 'waiting', 'waiting_for_clarification',
             'not_invoked', 'unavailable', 'limited', 'skipped_language', 'no_candidates',
             'empty_report', 'unknown'}
_TIMING_NOTE = ('Workflow, activity, retrieval and language timings are inclusive, nested views. '
                'Do not add these groups together. Overall time measures server-side answer handling; '
                'browser rendering and user-to-server network time are not measured. '
                'Model-call sums cover sequential recorded language calls only; provider round trips '
                'also include network time and SDK retries.')


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return value[:100] if isinstance(value, list) else []


def _text(value, limit=300):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'[\x00-\x1f\x7f]+', ' ', value)
    value = re.sub(r'https?://\S+', '[URL omitted]', value)
    value = re.sub(r'(?i)\bBearer\s+\S+', 'Bearer [redacted]', value)
    value = re.sub(r'(?i)\b(api[_ -]?key|authorization|password|secret|access_token)\s*[:=]\s*\S+',
                   r'\1=[redacted]', value)
    value = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[redacted]', value)
    return value[:limit]


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return round(value, 3) if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def _tokens(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _status(value):
    value = {'ok': 'completed', 'generated': 'completed', 'error': 'failed'}.get(value, value) if isinstance(value, str) else None
    return value if value in _STATUSES else 'unknown'


def _error_type(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', value) else None


def _activity(row):
    row = _mapping(row)
    actor = _text(row.get('actor'), 60)
    return {'name': _text(row.get('name') or row.get('node'), 100),
            'actor': _ACTOR_ALIASES.get(actor, actor),
            'operation': _text(row.get('operation'), 100), 'status': _status(row.get('status')),
            'duration_ms': _number(row.get('duration_ms')), 'detail': _text(row.get('detail')),
            'error_type': _error_type(row.get('error_type'))}


def _sum_measured(rows, field):
    values = [row[field] for row in rows if row.get(field) is not None]
    return round(sum(values), 2) if values else None


def build_diagnostics(message):
    """Normalize old, partial and current responses without executing any services.

    Missing timings remain null. An absent agent means no invocation was recorded;
    an old response without instrumentation cannot prove the agent did not run.
    """
    message = _mapping(message)
    runtime = _mapping(message.get('runtime'))
    workflow = _mapping(runtime.get('workflow'))
    route = _mapping(runtime.get('route'))
    retrieval = _mapping(runtime.get('retrieval'))
    elapsed = _number(message.get('elapsed_seconds'))
    overall = _number(runtime.get('response_duration_ms'))
    if overall is None and elapsed is not None:
        overall = round(elapsed * 1000, 3)
    nodes = [_activity(row) for row in _rows(workflow.get('nodes')) if isinstance(row, dict)]
    activities = [_activity(row) for row in _rows(runtime.get('activities')) if isinstance(row, dict)]
    planning_count = _tokens(runtime.get('planning_call_count')) or 0
    calls = []
    for index, row in enumerate(_rows(runtime.get('language_calls'))):
        if not isinstance(row, dict):
            continue
        calls.append({'purpose': 'Understanding the request' if index < planning_count else 'Writing the answer',
                      'provider': _text(row.get('provider'), 60) or None,
                      'model': _text(row.get('model'), 100) or None,
                      'status': _status(row.get('status')), 'duration_ms': _number(row.get('duration_ms')),
                      'error_type': _error_type(row.get('error_type')),
                      'request_roundtrip_ms': _number(row.get('request_roundtrip_ms')),
                      'input_tokens': _tokens(row.get('input_tokens')),
                      'output_tokens': _tokens(row.get('output_tokens')),
                      'reasoning_tokens': _tokens(row.get('reasoning_tokens')),
                      'thinking_time_ms': None, 'time_to_first_token_ms': None})
    retrieval_stages = []
    for row in _rows(retrieval.get('stages')):
        if not isinstance(row, dict):
            continue
        stage = _activity({**row, 'name': row.get('stage')})
        stage.update(candidate_count=_tokens(row.get('candidate_count')),
                     coverage=_text(row.get('coverage'), 60), model=_text(row.get('model'), 160))
        retrieval_stages.append(stage)
    agents = []
    actors = list(_ACTORS)
    for row in activities:
        if row['actor'] and row['actor'] not in actors:
            actors.append(row['actor'])
    for actor in actors:
        spans = calls if actor == 'LLM' else [row for row in activities if row['actor'] == actor]
        statuses = {row['status'] for row in spans}
        status = ('not_invoked' if not spans else 'failed' if 'failed' in statuses else
                  'reused' if statuses == {'reused'} else 'completed' if statuses <= {'completed', 'reused'} else 'limited')
        # Multiple service spans can nest: show each activity rather than invent
        # an exclusive agent total. Language calls are sequential in this flow.
        duration = (_sum_measured(spans, 'duration_ms') if actor == 'LLM' else
                    spans[0]['duration_ms'] if len(spans) == 1 else None)
        agents.append({'name': actor, 'status': status, 'duration_ms': duration,
                       'activity_count': len(spans),
                       'timing_basis': 'sequential_recorded_calls' if actor == 'LLM' else 'single_span' if len(spans) == 1 else 'see_activity_rows'})
    saved = _mapping(runtime.get('saved_scenario'))
    saved_execution = None
    if saved:
        saved_execution = {'run_id': _text(saved.get('run_id'), 100), 'status': _status(saved.get('status')),
                           'duration_ms': _number(saved.get('duration_ms')),
                           'trace': [_activity(row) for row in _rows(saved.get('trace')) if isinstance(row, dict)],
                           'timing_scope': 'Original saved scenario run; excluded from this response latency.'}
    limitations = ['Hidden LLM thinking time and time to first token are unavailable from the current non-streaming API.',
                   'Reasoning-token counts are shown only when supplied by the provider; they are not reasoning duration.',
                   'Not invoked means no call was recorded for this answer. Older messages may have incomplete instrumentation.',
                   'Execution receipts are not a factual-quality score or hidden chain-of-thought.']
    return {'version': 1, 'route': {'action': _text(route.get('action'), 60),
             'answer_kind': _text(route.get('answer_kind') or message.get('answer_kind'), 60),
             'section': _text(route.get('section') or message.get('section'), 60),
             'decision_source': _text(route.get('decision_source'), 100),
             'status': _status(workflow.get('status') or message.get('status'))},
            'total_ms': overall, 'workflow_nodes': nodes, 'activities': activities, 'agents': agents,
            'language_calls': calls, 'retrieval_stages': retrieval_stages, 'saved_execution': saved_execution,
            'timing': {'overall_ms': overall, 'model_calls_ms': _sum_measured(calls, 'duration_ms'),
                       'provider_roundtrips_ms': _sum_measured(calls, 'request_roundtrip_ms'),
                       'thinking_time_ms': None, 'time_to_first_token_ms': None, 'note': _TIMING_NOTE},
            'limitations': limitations}
