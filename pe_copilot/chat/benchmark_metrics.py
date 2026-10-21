"""Transparent scripted contracts and separate benchmark summaries.

No lexical assertion is advertised as semantic accuracy, and no scripted case
is a real user-adoption observation. Metrics retain their own denominators.
"""
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
import math
import re
from statistics import mean, median

from .diagnostics import build_diagnostics


METRICS = ('faithfulness', 'answer_relevancy', 'context_precision_without_reference', 'context_recall', 'factual_correctness')
_STATE_KEYS = {'selected_metals', 'selected_kpis', 'selected_report_accession', 'baseline_context',
    'selected_report_baseline', 'forecast_prepared', 'forecast_horizon', 'sim_execution',
    'sim_results', 'sim_inputs_current', 'reference_scenario', 'scenario_reference'}
_STATE_PREFIXES = ('materials_', 'kpis_', 'forecast_model_', 'forecast_compare_',
    'forecast_price_', 'forecast_shock_', 'spend_', 'unhedged_', 'pass_through_', 'risk_')


def _stable(value):
    if is_dataclass(value) and not isinstance(value, type):
        return _stable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _stable(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_stable(item) for item in value]
    if isinstance(value, set):
        return sorted((_stable(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if type(value).__module__.split('.')[0] == 'pandas' and hasattr(value, 'columns') and hasattr(value, 'to_dict'):
        return {'kind': 'dataframe', 'columns': _stable(list(value.columns)),
                'index': _stable(list(value.index)), 'records': _stable(value.to_dict(orient='records'))}
    if hasattr(value, 'tolist'):
        return _stable(value.tolist())
    if hasattr(value, 'item'):
        return _stable(value.item())
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    # Unknown live resources are never serialized into benchmark snapshots.
    # Supported fixture analysis state should use plain data or dataclasses.
    return {'unsupported_type': type(value).__name__}


def snapshot_workspace(state):
    """Hash analysis data before/after a turn; omit conversation and canvas focus.

    The hash is an equality check for these explicitly selected fields, not a
    claim that every arbitrary Streamlit/session key has been compared.
    """
    scope = state.get('baseline_context', {})
    company = state.get('active_company', {})
    fields = {key: _stable(value) for key, value in state.items()
              if key in _STATE_KEYS or key.startswith(_STATE_PREFIXES)}
    def supported(value):
        if isinstance(value, dict):
            return 'unsupported_type' not in value and all(supported(item) for item in value.values())
        return all(supported(item) for item in value) if isinstance(value, list) else True
    return {'scope': {key: scope.get(key) for key in ('company_id', 'accession', 'snapshot_id')}
            | {'ticker': company.get('ticker', scope.get('ticker'))},
            'analysis_comparable': supported(fields),
            'analysis_fingerprint': sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False,
                                                      allow_nan=False).encode()).hexdigest()}


def _check(name, value, detail):
    return {'name': name, 'status': 'not_applicable' if value is None else 'passed' if value else 'failed',
            'detail': detail}


def _expected_list(value):
    return [value] if isinstance(value, str) else value if isinstance(value, list) else []


def _contains(text, term):
    if isinstance(term, list):
        return any(_contains(text, item) for item in term)
    return isinstance(term, str) and bool(term) and term.casefold() in text.casefold()


def assess_case(case, reply, before, after):
    """Assess authored workflow contracts using execution records and literal checks.

    before/after come from snapshot_workspace. No LLM judge, no inference of
    undisclosed values, and no assumption that mentioning a policy enforces it.
    """
    expected = case.get('expected', {})
    runtime = reply.get('runtime', {})
    route = runtime.get('route', {})
    checks, governance = [], []
    checks.append(_check('response_completed', reply.get('status') == 'ok', 'Recorded reply status; not semantic answer correctness.'))
    for field, actual in [('actions', route.get('action')), ('answer_kinds', route.get('answer_kind') or reply.get('answer_kind'))]:
        allowed = _expected_list(expected.get(field))
        checks.append(_check('expected_' + field, actual in allowed if allowed else None,
                             'Actual validated route compared with the authored allowed values.'))
    checks.append(_check('expected_section', reply.get('section') == expected['section'] if 'section' in expected else None,
                         'Selected section metadata compared with the authored expected section.'))
    preserve = expected.get('must_preserve_workspace') is True
    fingerprints_available = (isinstance(before.get('analysis_fingerprint'), str)
        and isinstance(after.get('analysis_fingerprint'), str)
        and before.get('analysis_comparable', True) and after.get('analysis_comparable', True))
    state_check = _check('analysis_state_preserved',
        before['analysis_fingerprint'] == after['analysis_fingerprint'] if preserve and fingerprints_available else None,
        'Equality of selected analysis-state fields; conversation and canvas-focus keys are excluded.')
    if preserve and not fingerprints_available:
        state_check['detail'] = 'Required before/after analysis fingerprints are unavailable; this contract is not assessed.'
    checks.append(state_check)
    governance.append(state_check)
    target = case.get('scope') if isinstance(case.get('scope'), dict) else before.get('scope', {})
    actual_scope = reply.get('scope', {})
    scoped = [key for key in ('company_id', 'ticker', 'accession', 'snapshot_id') if target.get(key)]
    check_scope = route.get('action') != 'prepare' or isinstance(case.get('scope'), dict)
    scope_check = _check('selected_scope_retained',
        all(actual_scope.get(key) == target[key] for key in scoped) if scoped and check_scope else None,
        'Company/report identifiers compared with fixture scope; preparation without an explicit target scope is excluded.')
    checks.append(scope_check)
    governance.append(scope_check)
    sources = reply.get('citations', []) if isinstance(reply.get('citations'), list) else []
    inline = {int(value) for value in re.findall(r'\[(\d+)\]', str(reply.get('text') or ''))}
    identifiers = _check('citation_identifiers_valid', all(1 <= index <= len(sources) for index in inline) if inline else None,
                         'Inline citation identifiers exist; this does not test whether a source entails a claim.')
    scope_ok = all(isinstance(row, dict) and all(not row.get(key)
                    or row[key] == actual_scope.get(key) for key in ('ticker', 'accession')) for row in sources)
    has_source_scope = any(isinstance(row, dict) and (row.get('ticker') or row.get('accession')) for row in sources)
    source_scope = _check('source_scope_valid', scope_ok if has_source_scope else None,
                         'Source ticker/accession metadata, when present, matches the selected reply scope.')
    provenance = _check('source_provenance_present', all(isinstance(row, dict) and isinstance(row.get('text'), str)
        and bool(row['text'].strip()) and any(row.get(key) for key in ('locator', 'chunk_id', 'document_id', 'source_url'))
        for row in sources) if sources else None,
        'Each supplied source has text and a locator/document/source identifier; source truth is not established by this check.')
    governance.extend([identifiers, source_scope, provenance])
    checks.extend([identifiers, source_scope, provenance])
    for key, wanted in [('required_terms', True), ('forbidden_terms', False)]:
        terms = _expected_list(expected.get(key))
        marker = _check('literal_' + key, all(_contains(str(reply.get('text') or ''), term) == wanted for term in terms) if terms else None,
                        'Literal fixture-marker check only, not semantic correctness or an access-control test.')
        checks.append(marker)
        fixture_labels = ' '.join([str(case.get('category', '')), *_expected_list(case.get('tags'))]).casefold()
        if any(label in fixture_labels for label in ('governance', 'adversarial', 'injection', 'permission', 'allocation', 'evidence_gap')):
            governance.append(marker)
    activities = build_diagnostics(reply)['activities']
    capabilities = {'activities': activities,
        'observed_operations': sorted({row['actor'] + ':' + row['operation'] for row in activities if row['operation']}),
        'recorded_reuse_count': sum(row['status'] == 'reused' for row in activities),
        'meaning': 'Actual recorded service operations. Reading a saved result is reuse, not a fresh model/agent execution.'}
    applicable = [row for row in checks if row['status'] != 'not_applicable']
    failed = [row['name'] for row in applicable if row['status'] == 'failed']
    unassessed_required = preserve and not fingerprints_available
    outcome = 'failed' if failed else 'not_assessed' if unassessed_required or not applicable else 'passed'
    return {'checks': checks, 'capabilities': capabilities,
        'governance': {'checks': governance, 'limitations': [
            'Report scope, citation identifiers, provenance metadata and state retention are deterministic boundary checks.',
            'Literal adversarial markers do not prove semantic safety or enforce enterprise knowledge permissions.',
            'DemoPermissionPolicy is a separate synthetic demonstration; no active enterprise identity/ACL guarantee is claimed.']},
        'task_outcome': {'status': outcome, 'check_count': len(applicable), 'failed_checks': failed,
                         'kind': 'scripted_contract_proxy'}}


def synthetic_permission_check():
    """Execute the existing isolated demo policy, explicitly outside workspace ACLs."""
    from .permissions import DemoPermissionPolicy
    policy = DemoPermissionPolicy()
    expectations = {'synthetic:public-filing': 'allow', 'synthetic:treasury-memo': 'deny', 'synthetic:unknown': 'deny'}
    checks = [_check(resource, policy.authorize(resource)['decision'] == wanted,
                     'Standalone synthetic policy decision; not an authenticated workspace access-control test.')
              for resource, wanted in expectations.items()]
    return {'synthetic': True, 'active_workspace_acl': False, 'checks': checks}


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def distribution(values):
    """Separate metric distribution with linearly interpolated sample quantiles."""
    numbers = sorted(float(value) for value in values if _finite(value))
    def percentile(fraction):
        if not numbers:
            return None
        position = (len(numbers) - 1) * fraction
        lower = math.floor(position)
        return numbers[lower] + (numbers[math.ceil(position)] - numbers[lower]) * (position - lower)
    return {'count': len(numbers), 'mean': mean(numbers) if numbers else None,
            'median': median(numbers) if numbers else None, 'p90': percentile(.90), 'p95': percentile(.95),
            'min': numbers[0] if numbers else None, 'max': numbers[-1] if numbers else None}


def _assessment(row):
    value = row.get('assessment', row.get('casechecks'))
    return value if isinstance(value, dict) else row


def _rate(numerator, denominator):
    return numerator / denominator if denominator else None


def _summarize(rows, case_count, include_categories):
    ragas = {}
    for metric in METRICS:
        values, statuses = [], Counter()
        for row in rows:
            quality = row.get('quality') or {}
            detail = next((item for item in quality.get('metrics', []) if isinstance(item, dict) and item.get('key') == metric), {})
            score = quality.get('scores', {}).get(metric)
            lower = -1 if metric == 'answer_relevancy' else 0
            if _finite(score) and lower <= score <= 1:
                values.append(score)
                statuses['completed'] += 1
            elif detail.get('status') == 'not_applicable' or quality.get('status') == 'not_applicable':
                statuses['not_applicable'] += 1
            elif (detail.get('status') in ('unavailable', 'failed') or metric in quality.get('errors', {})
                  or quality.get('status') in ('unavailable', 'failed')):
                statuses['unavailable'] += 1
            else:
                statuses['missing'] += 1
        ragas[metric] = {**distribution(values), 'not_applicable_count': statuses['not_applicable'],
                        'unavailable_count': statuses['unavailable'], 'missing_count': statuses['missing']}
    latencies = {name: [] for name in ('answer_ms', 'model_ms', 'provider_ms', 'judge_ms', 'evaluation_screening_ms')}
    operations, service_times, checks, outcomes, status_counts = {}, defaultdict(list), {}, Counter(), Counter()
    clarification_count = error_count = automated_count = answer_fallback_count = 0
    for row in rows:
        reply = row.get('reply') or {}
        diagnostics = row.get('diagnostics') or build_diagnostics(reply)
        timing = diagnostics.get('timing', {})
        latencies['answer_ms'].append(diagnostics.get('total_ms'))
        latencies['model_ms'].append(timing.get('model_calls_ms'))
        latencies['provider_ms'].append(timing.get('provider_roundtrips_ms'))
        quality = row.get('quality') or {}
        seconds = quality.get('evaluation_seconds')
        measured_evaluation = _finite(seconds) and seconds >= 0 and not quality.get('cache_hit')
        screening_only = quality.get('status') == 'not_applicable'
        latencies['judge_ms'].append(seconds * 1000 if measured_evaluation and not screening_only else None)
        latencies['evaluation_screening_ms'].append(seconds * 1000 if measured_evaluation and screening_only else None)
        assessment = _assessment(row)
        outcome = assessment.get('task_outcome', {}).get('status', 'not_assessed')
        if row.get('status') == 'execution_error' and not assessment.get('task_outcome'):
            outcome = 'failed'
        outcomes[outcome if outcome in ('passed', 'failed', 'not_assessed') else 'not_assessed'] += 1
        status_counts[str(row.get('status') or reply.get('status') or 'unknown')] += 1
        route = reply.get('runtime', {}).get('route', {})
        clarification_count += route.get('action') == 'clarify'
        error_count += reply.get('status') == 'error' or row.get('status') == 'execution_error'
        answer_fallback_count += (reply.get('status') == 'ok'
            and reply.get('runtime', {}).get('answer', {}).get('status') in ('unavailable', 'failed'))
        automated_count += (reply.get('status') == 'ok' and route.get('action') != 'clarify'
            and reply.get('runtime', {}).get('workflow', {}).get('status') == 'completed')
        for check in assessment.get('governance', {}).get('checks', []):
            if check.get('status') not in ('passed', 'failed', 'not_applicable'):
                continue
            record = checks.setdefault(check['name'], {'name': check['name'], 'passed': 0, 'failed': 0, 'not_applicable': 0, 'total': 0})
            record[check['status']] += 1
            record['total'] += 1
        for activity in diagnostics.get('activities', []):
            key = (activity.get('actor', ''), activity.get('operation') or activity.get('name', ''))
            record = operations.setdefault(key, {'actor': key[0], 'operation': key[1], 'calls': 0,
                'completed': 0, 'failed': 0, 'reused': 0, 'categories': set(), 'scopes': set()})
            record['calls'] += 1
            if activity.get('status') in ('completed', 'failed', 'reused'):
                record[activity['status']] += 1
            record['categories'].add(str(row.get('category', 'unclassified')))
            scope = reply.get('scope', {})
            # Scope breadth is a count, never exported company/report identifiers.
            if scope.get('company_id') or scope.get('accession'):
                record['scopes'].add((scope.get('company_id'), scope.get('accession')))
            service_times[key].append(activity.get('duration_ms'))
    operation_rows = [{**record, 'categories': sorted(record['categories']), 'scopes': len(record['scopes'])}
                      for _, record in sorted(operations.items())]
    assessed = outcomes['passed'] + outcomes['failed']
    result = {'planned_case_count': case_count, 'attempt_count': len(rows), 'status_counts': dict(status_counts),
        'ragas': ragas, 'latency': {key: {**distribution(values), 'missing_count': sum(not _finite(value) for value in values)}
                                  for key, values in latencies.items()},
        'capabilities': {'operations': operation_rows, 'recorded_reuse_count': sum(row['reused'] for row in operation_rows),
                         'meaning': 'Observed service contracts reused across scripted intents/scopes; no inferred invocation.'},
        'governance': {'checks': sorted(checks.values(), key=lambda row: row['name']), 'limitations': [
            'Deterministic boundary/provenance checks, not complete semantic safety or enterprise access control.',
            'Literal adversarial-marker checks are N/A unless required/forbidden terms are authored for the case; N/A is not a passed safety test.',
            'The standalone synthetic permission-policy demonstration is reported separately.']},
        'task_outcomes': {'passed': outcomes['passed'], 'failed': outcomes['failed'], 'not_assessed': outcomes['not_assessed'],
                          'assessed': assessed, 'completion_rate': _rate(outcomes['passed'], assessed),
                          'meaning': 'Authored scripted contract completion proxy; not real-user task success.'},
        'adoption': {'kind': 'scripted_proxy_only', 'case_count': len(rows),
            'automation_coverage': {'numerator': automated_count, 'denominator': len(rows),
                'value': _rate(automated_count, len(rows)),
                'meaning': 'Recorded workflow completed without clarification; not proof of task quality or unattended enterprise automation.'},
            'clarification_count': clarification_count, 'clarification_rate': _rate(clarification_count, len(rows)),
            'error_count': error_count, 'error_rate': _rate(error_count, len(rows)),
            'answer_fallback_count': answer_fallback_count,
            'answer_fallback_rate': _rate(answer_fallback_count, len(rows)),
            'answer_fallback_meaning': 'Reply remained available but generated language was unavailable/failed; a deterministic or evidence fallback was retained. Rate uses all attempted cases, separately from execution errors.',
            'observed_user_adoption': None, 'retention': None,
            'meaning': 'Scripted workflow outcomes are separate from optional local anonymous user observations.'},
        'limitations': ['Each RAGAS metric has its own scored/N/A/error/missing denominator; metrics are never averaged into one quality score.',
            'Authored numeric_facts are reference metadata, not deterministic numeric-answer checks in this runner; answer correctness is assessed only by the separate reference-based RAGAS metric.',
            'Latency spans are inclusive and may nest. Do not add agent/service/model/judge timing groups together.',
            'N/A evaluation eligibility screening is timed separately from attempted judge execution; raw evaluation durations remain unchanged.',
            'Mean and sample percentiles describe these observed cases only; cold models and fixture composition affect timing.',
            'Authored synthetic cases are development evidence, not independently reviewed production validation.']}
    result['latency']['activities'] = [{'actor': key[0], 'operation': key[1], 'stats': distribution(values)}
                                       for key, values in sorted(service_times.items())]
    if include_categories:
        groups = defaultdict(list)
        for row in rows:
            groups[str(row.get('category', 'unclassified'))].append(row)
        result['per_category'] = {category: _summarize(group, len(group), False) for category, group in sorted(groups.items())}
    return result


def summarize(results, case_count=100):
    """Aggregate metadata only; answers, company identifiers and contexts excluded."""
    rows = [row for row in results if isinstance(row, dict)]
    return _summarize(rows, case_count, True)
