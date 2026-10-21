"""Selected-filing financial context and deterministic, comparable-period ratios.

This read-only helper supplies numbers to conversational explanations. It does
not annualize flows, infer missing figures, benchmark companies, or calculate
scenario assumptions. Amounts follow the existing USD-million baseline contract.
"""
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
import math

from pe_copilot.filings.financials import INSTANT, POOLS


_METRICS = tuple(dict.fromkeys((*[name for names in POOLS.values() for name in names],
                               'Current Assets', 'Current Liabilities')))
_BALANCES = INSTANT | set(POOLS['balance_sheet'])
_RATIOS = (
    ('Gross margin', 'Gross Profit', 'Revenue', '%'),
    ('Operating margin', 'Operating Income', 'Revenue', '%'),
    ('Net margin', 'Net Income', 'Revenue', '%'),
    ('Current ratio', 'Current Assets', 'Current Liabilities', 'times'),
    ('Debt-to-equity', 'Total Debt', 'Total Equity', 'times'),
    ('Operating cash flow margin', 'Operating Cash Flow', 'Revenue', '%'),
)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and math.isfinite(float(number)) else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _date(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def build_financial_context(baseline):
    """Return JSON-safe facts and ratios, withholding unsupported calculations.

    ``financial_figures`` maps metric names to value/status/provenance records;
    ``ratios`` is a list with explicit inputs and formulas. Unknown provenance
    remains labelled and is never used to calculate a ratio. Evidence-only
    current assets/liabilities are usable even though the stress pools omit them.
    """
    if not isinstance(baseline, Mapping):
        baseline = {}
    report = {key: baseline.get(key) if isinstance(baseline.get(key), str) else None
              for key in ('accession', 'snapshot_id', 'form', 'period_end', 'duration_start',
                          'duration_basis', 'currency', 'unit', 'source_url')}
    report['annualized'] = False
    warnings = [value for value in baseline.get('warnings', []) if isinstance(value, str)] if isinstance(baseline.get('warnings'), (list, tuple)) else []
    common_errors = []
    if not report['accession']:
        common_errors.append('Selected report accession is unavailable.')
    if report['currency'] != 'USD' or report['unit'] != 'millions':
        common_errors.append('Financial context requires the selected baseline in USD millions; no unit or currency conversion was applied.')
    end, start = _date(report['period_end']), _date(report['duration_start'])
    if end is None:
        common_errors.append('Selected report period end is unavailable or invalid.')
    form = report['form'] or ''
    quarterly, annual = form.startswith('10-Q'), form.startswith('10-K')
    days = (end - start).days if end and start else None
    flow_error = None
    if not (quarterly or annual) or days is None or not ((60 <= days <= 300) if quarterly else (330 <= days <= 380)):
        flow_error = 'Comparable annual or year-to-date flow dates are unavailable; no annualization or period substitution was applied.'
    elif report['duration_basis'] and report['duration_basis'].casefold() not in (
            ('year to date', 'year-to-date') if quarterly else ('annual',)):
        flow_error = 'The stated flow basis conflicts with the selected report form.'
    report['flow_basis'] = 'year-to-date' if quarterly else 'annual' if annual else 'unknown'
    warnings.extend(common_errors)
    if flow_error:
        warnings.append(flow_error)

    pools = baseline.get('pools') if isinstance(baseline.get('pools'), Mapping) else {}
    evidence = baseline.get('evidence') if isinstance(baseline.get('evidence'), Mapping) else {}
    figures = {}
    for name in _METRICS:
        row = evidence.get(name) if isinstance(evidence.get(name), Mapping) else {}
        candidates = [pool[name] for pool in pools.values() if isinstance(pool, Mapping) and name in pool]
        value = _number(candidates[0] if candidates else row.get('value'))
        basis = row.get('basis') if row.get('basis') in ('reported', 'derived') else 'unknown'
        provenance = {'basis': basis, 'accession': row.get('accession') if isinstance(row.get('accession'), str) else None,
                      'source_url': report['source_url'],
                      'start': row.get('start') if isinstance(row.get('start'), str) else None,
                      'end': row.get('end') if isinstance(row.get('end'), str) else None}
        for key in ('tag', 'context_id', 'definition'):
            if isinstance(row.get(key), str):
                provenance[key] = row[key]
        if isinstance(row.get('inputs'), (list, tuple)):
            provenance['inputs'] = [key for key in row['inputs'] if isinstance(key, str)]
        record = {'name': name, 'value': None, 'unit': 'USD millions', 'status': 'unavailable',
                  'reason': None, 'provenance': provenance}
        reason = '; '.join(common_errors) if common_errors else None
        if reason is None and value is None:
            reason = 'No finite financial figure is available in the selected baseline.'
        if reason is None and any(_number(candidate) != value for candidate in candidates):
            reason = 'Conflicting values appear in the selected financial pools.'
        if reason is None and not row:
            record.update(value=float(value), status='unverified')
            reason = 'Financial figure has no source or derivation evidence; provenance is unknown.'
        if reason is None and (row.get('unit', 'USD millions') != 'USD millions' or row.get('currency', 'USD') != 'USD'):
            reason = 'Evidence units do not match the USD-million baseline.'
        if reason is None and (row.get('accession') != report['accession'] or row.get('end') != report['period_end']):
            reason = 'Financial evidence belongs to another report or period.'
        if reason is None and (name in _BALANCES and row.get('start') != report['period_end']):
            reason = 'A period-end balance is required for this financial figure.'
        if reason is None and name not in _BALANCES and (flow_error or row.get('start') != report['duration_start']):
            reason = flow_error or 'Financial flow evidence has a different start date; annual, quarterly and year-to-date flows cannot be mixed.'
        source_value = _number(row.get('value'))
        if reason is None and (source_value is None or float(source_value) != float(value)):
            reason = 'Financial figure does not match its source or derivation evidence.'
        if reason is None and basis == 'unknown':
            record.update(value=float(value), status='unverified')
            reason = 'Evidence does not identify the figure as reported or deterministically derived.'
        if reason is None:
            record.update(value=float(value), status='available')
        record['reason'] = reason
        figures[name] = record

    ratios = []
    for name, numerator, denominator, unit in _RATIOS:
        inputs = {key: figures[key] for key in (numerator, denominator)}
        row = {'name': name, 'value': None, 'unit': unit,
               'formula': f'{numerator} / {denominator}' + (' × 100' if unit == '%' else ''),
               'inputs': inputs, 'status': 'unavailable', 'reason': None,
               'provenance': {'basis': 'deterministic derivation', 'accession': report['accession'],
                              'period_start': report['period_end'] if numerator in _BALANCES else report['duration_start'],
                              'period_end': report['period_end'], 'annualized': False}}
        unavailable = [key for key, item in inputs.items() if item['status'] != 'available']
        if unavailable:
            row['reason'] = 'Comparable evidenced inputs unavailable: ' + '; '.join(f'{key}: {inputs[key]["reason"]}' for key in unavailable)
        elif figures[denominator]['value'] <= 0:
            row['reason'] = ('Debt-to-equity is not meaningful with zero or negative equity.' if denominator == 'Total Equity'
                             else f'{denominator} must be positive; no ratio was calculated with a zero or negative denominator.')
        elif numerator in _BALANCES and figures[numerator]['value'] < 0:
            row['reason'] = f'{numerator} is negative; the balance-based ratio was withheld.'
        else:
            result = Decimal(str(figures[numerator]['value'])) / Decimal(str(figures[denominator]['value']))
            if unit == '%':
                result *= 100
            if math.isfinite(float(result)):
                row.update(value=float(result), status='available')
            else:
                row['reason'] = 'Calculated ratio is outside the supported finite numeric range.'
        ratios.append(row)
    return {'report': report, 'financial_figures': figures, 'ratios': ratios,
            'warnings': list(dict.fromkeys(warnings))}
