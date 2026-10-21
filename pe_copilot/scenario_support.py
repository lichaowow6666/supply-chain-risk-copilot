"""Serialization and input checks for scenario coordination; no financial formulas."""
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import math
import numbers


class ScenarioValidationError(ValueError):
    """Safe, application-authored validation message for the user-facing trace."""


def json_value(value):
    if isinstance(value, dict):
        return {str(k):json_value(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, 'to_dict') and hasattr(value, 'columns'):
        return json_value(value.to_dict(orient='records'))
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, numbers.Real):
        if not math.isfinite(value):
            raise ValueError('Scenario records cannot contain nonfinite numbers')
        return float(value)
    raise TypeError(f'Unsupported scenario value: {type(value).__name__}')


def fingerprint(value):
    return sha256(json.dumps(json_value(value),sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def describe_market_snapshot(histories):
    from pe_copilot.market_data import CATALOG
    series = {}
    for name, frame in histories.items():
        if frame.empty or len(frame) < 2:
            raise ScenarioValidationError(f'{name}: at least two market observations are required')
        if frame['Date'].isna().any() or frame['Date'].duplicated().any() or not frame['Date'].is_monotonic_increasing:
            raise ScenarioValidationError(f'{name}: market dates must be valid, unique and ordered')
        if not all(math.isfinite(float(x)) and float(x)>0 for x in frame['Price']):
            raise ScenarioValidationError(f'{name}: prices must be finite and positive')
        if (frame['Date'].dt.date > datetime.now(timezone.utc).date()).any():
            raise ScenarioValidationError(f'{name}: future market observations cannot be used')
        if any(str(m).casefold()!=name.casefold() for m in frame['Metal']):
            raise ScenarioValidationError('Market observations belong to another material')
        expected_symbol, unit = CATALOG.get(name, (None, 'provider quote units'))
        tickers = sorted(set(frame['Ticker']))
        if len(tickers)!=1 or (expected_symbol and tickers[0]!=expected_symbol):
            raise ScenarioValidationError(f'{name}: inconsistent futures contract identity')
        series[name] = {'ticker':tickers[0], 'unit':unit, 'start':frame['Date'].min().isoformat(),
                        'end':frame['Date'].max().isoformat(), 'observations':len(frame),
                        'fingerprint':fingerprint(frame),
                        'source':'Local futures_prices; Yahoo Finance daily unadjusted Close'}
    return {'captured_at':datetime.now(timezone.utc).isoformat(), 'series':series,
            'fingerprint':fingerprint(series)}


def validate_report(request, report):
    baseline, company = report['baseline'], report['company']
    if company['ticker'].upper()!=request.ticker or 'company:'+company['cik']!=request.context.company_id:
        raise ScenarioValidationError('The selected report belongs to a different company')
    if baseline['accession']!=request.accession or baseline['snapshot_id']!=request.context.snapshot_id:
        raise ScenarioValidationError('The financial baseline and requested filing snapshot do not match')
    if baseline['currency']!=request.currency or baseline['unit']!=request.unit:
        raise ScenarioValidationError('The baseline and scenario financial units do not match')
    expected = 'year-to-date' if baseline['form'].startswith('10-Q') else 'annual'
    from datetime import date as calendar_date
    duration = (calendar_date.fromisoformat(baseline['period_end']) - calendar_date.fromisoformat(baseline['duration_start'])).days if baseline.get('duration_start') else None
    if duration is None or not ((60 <= duration <= 300) if expected=='year-to-date' else (330 <= duration <= 380)):
        raise ScenarioValidationError('A comparable financial duration is required for the selected report')
    if request.spend_basis != expected:
        raise ScenarioValidationError('The spending basis must match the selected annual or quarterly report')
    if request.context.period_end and (request.context.period_end!=baseline['period_end'] or
                                      request.context.period_start!=baseline['duration_start']):
        raise ScenarioValidationError('The requested financial period does not match the selected report')
    for pool in baseline['pools'].values():
        for metric, value in pool.items():
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
                raise ScenarioValidationError('Financial baseline values must be finite numbers')
            row = baseline['evidence'].get(metric)
            if not row or float(row['value']) != value:
                raise ScenarioValidationError(f'{metric}: financial input lacks matching source or derivation evidence')
    for evidence in baseline['evidence'].values():
        if evidence['accession'] != request.accession or evidence['end'] != baseline['period_end']:
            raise ScenarioValidationError('A financial evidence record belongs to another report or period')
    available = {m['material'].casefold():m for m in report['materials']}
    for material in request.materials:
        row = available.get(material.material.casefold())
        if row is None or row.get('source_accession') != request.accession:
            raise ScenarioValidationError(f'{material.material}: no validated exposure in the selected report')
    return baseline
