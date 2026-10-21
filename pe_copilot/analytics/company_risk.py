"""Joint empirical forecast-error cases through the shared financial cascade.

Exact dated, equally weighted cases; never manufacture extra independent samples
by drawing thousands of duplicates from a small error pool.
"""
import math
from dataclasses import asdict
import numpy as np
from pe_copilot.analytics.financial_impact import apply_financial_impact
from pe_copilot.scenario_support import fingerprint, json_value

VERSION = 'company-risk-v1'
MIN_CASES = 8


def unavailable(reason):
    return {'status':'unavailable', 'version':VERSION, 'reason':reason}


def summarize(values):
    a = np.asarray(values, dtype=float)
    return {'p10':float(np.quantile(a,.1)), 'median':float(np.median(a)),
            'p90':float(np.quantile(a,.9)), 'mean':float(a.mean())}


def build_company_risk(request, baselines, forecasts):
    names = [m.material for m in request.materials]
    if set(names) != set(forecasts):
        return unavailable('Prepare a validated forecast for every selected material, then rerun the scenario.')
    errors, models, endpoints, sources = {}, {}, {}, {}
    as_of = set()
    for m in request.materials:
        f = forecasts[m.material]
        if (f.get('forecast_id') != m.forecast_id or f.get('material') != m.material or
                f.get('horizon') != request.shock_horizon_days or
                f.get('reference_price') != m.reference_price or
                m.forecast_model not in f.get('models', {})):
            return unavailable('Forecast identity, horizon, model or reference price does not match the scenario.')
        as_of.add(f['market']['end'])
        row = f['models'][m.forecast_model]
        values = {}
        for record in row.get('development_errors', []):
            key = (record['origin'], record['target_end'])
            e = record['log_errors']
            if key in values or len(e) != request.shock_horizon_days or not np.isfinite(e).all():
                return unavailable('Forecast error records are incomplete or invalid.')
            values[key] = float(e[-1])
        errors[m.material] = values
        endpoints[m.material] = float(row['path'][-1])
        models[m.material] = m.forecast_model
        sources[m.material] = f['forecast_id']
    if len(as_of) != 1:
        return unavailable('Material histories have different latest dates. Align market data before joint risk estimation.')
    common = sorted(set.intersection(*(set(errors[n]) for n in names)))
    if len(common) < MIN_CASES:
        return unavailable(f'Only {len(common)} matched historical forecast windows; at least {MIN_CASES} are required. No independence assumption was substituted.')
    cases = []
    for origin, target in common:
        material_rows = {}
        for m in request.materials:
            try:
                price = endpoints[m.material]*math.exp(errors[m.material][(origin,target)])
            except OverflowError:
                return unavailable('A joint price case is numerically invalid; no case was silently dropped.')
            shock = price/m.reference_price-1
            gross = m.period_spend*m.unhedged_fraction*shock
            absorbed = gross*(1-request.pass_through_fraction)
            if not all(math.isfinite(v) for v in (price,shock,gross,absorbed)) or price <= 0:
                return unavailable('A joint price case is numerically invalid; no case was silently dropped.')
            material_rows[m.material] = {'price':price, 'shock_fraction':shock,
                'gross_cost_change':gross, 'absorbed_cost_change':absorbed}
        cost = round(sum(row['gross_cost_change'] for row in material_rows.values())*(1-request.pass_through_fraction),2)
        try:
            pools = apply_financial_impact(baselines, cost)
        except ValueError:
            return unavailable('At least one historical error case implies negative COGS under these spend assumptions. No tail case was clipped or discarded; revise the spending assumptions.')
        cases.append({'origin':origin, 'target_end':target, 'materials':material_rows,
                      'net_absorbed_cost':cost, 'stressed_pools':pools})
    kpis = []
    for pool, metrics in cases[0]['stressed_pools'].items():
        for metric, row in metrics.items():
            values = [c['stressed_pools'][pool][metric]['stressed'] for c in cases]
            kpis.append({'pool':pool, 'metric':metric, 'baseline':row['base'], **summarize(values)})
    # Fractional boundary weights give exactly the worst 20% of equal-weight cases.
    ordered = sorted(range(len(cases)), key=lambda i:cases[i]['net_absorbed_cost'], reverse=True)
    mass = .2*len(cases)
    weights = np.zeros(len(cases))
    for rank, index in enumerate(ordered):
        weights[index] = max(0., min(1., mass-rank))/mass
    contributions = [{'material':name,
        'mean_absorbed_cost':float(np.mean([c['materials'][name]['absorbed_cost_change'] for c in cases])),
        'worst_20pct_mean_absorbed_cost':float(sum(w*c['materials'][name]['absorbed_cost_change'] for w,c in zip(weights,cases)))}
        for name in names]
    result = {'status':'ok', 'version':VERSION, 'method':'paired development endpoint errors',
        'company':request.ticker, 'accession':request.accession, 'spend_basis':request.spend_basis,
        'currency':request.currency, 'unit':request.unit, 'horizon':request.shock_horizon_days,
        'as_of':next(iter(as_of)), 'forecast_ids':sources, 'models':models,
        'case_count':len(cases), 'cases':cases, 'kpis':kpis, 'material_contributions':contributions,
        'net_absorbed_cost':summarize([c['net_absorbed_cost'] for c in cases]),
        'worst_20pct_mean_cost':float(sum(w*c['net_absorbed_cost'] for w,c in zip(weights,cases))),
        'warnings':[
            'Conditional empirical sensitivity, not a calibrated probability forecast for company earnings.',
            'Historical development windows can overlap and also selected the model; this distribution has no independent company-level coverage validation.',
            f'Only {len(cases)} matched historical cases. Percentiles interpolate this small sample; tail estimates are fragile.',
            'Whole dated material-error vectors are kept together to preserve observed cross-material dependence; future dependence may change.',
            'Distribution is anchored to the selected model endpoints, not your manually chosen stress prices. The manual scenario is shown separately.',
            'Spend, hedging and pass-through are held fixed. Endpoint shocks apply to the full entered annual/YTD spend; no time prorating or path averaging.',
            'Only selected materials are included. Other business risks and unsupported product allocations are excluded.']}
    result['risk_id'] = fingerprint({'request':asdict(request), 'baselines':baselines, 'forecasts':sources, 'version':VERSION})
    return json_value(result)
