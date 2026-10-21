"""Additive material attribution and like-for-like saved-scenario comparison."""


def contribution_rows(simulation):
    """Reconcile displayed material rows to the saved company waterfall.

    Product weights are not present in this model. Never manufacture them from
    connectivity, sales mix or an LLM answer. Older runs lack exact components;
    their approximation is explicitly marked and reconciled too.
    """
    summary = simulation['waterfall_summary']
    pass_through = summary['Pass_Through_Rate_Pct'] / 100
    rows = []
    for material, value in simulation['materials'].items():
        exact = value.get('cost_contribution')
        gross = exact['gross'] if exact else value['gross_cost_increase']
        passed = exact['passed'] if exact else gross * pass_through
        absorbed = exact['absorbed'] if exact else gross * (1-pass_through)
        rows.append({'Material': material, 'Gross cost change ($M)': round(gross,2),
                     'Passed to customers ($M)': round(passed,2), 'COGS contribution ($M)': round(absorbed,2),
                     'Operating income contribution ($M)': -round(absorbed,2),
                     'Basis': 'Scenario calculation' if exact else 'Older run: approximate from rounded costs'})
    total = {'Material':'Company total', 'Gross cost change ($M)':summary['Total_Gross_COGS_Surge'],
             'Passed to customers ($M)':summary['Passed_To_Clients'],
             'COGS contribution ($M)':summary['Net_COGS_Absorbed'],
             'Operating income contribution ($M)':-summary['Net_COGS_Absorbed'],
             'Basis':'Saved model total; not a product allocation'}
    residual = {key:round(value-sum(row[key] for row in rows),2) for key,value in total.items() if isinstance(value,(int,float))}
    if any(residual.values()):
        rows.append({'Material':'Display rounding reconciliation', **residual, 'Basis':'Rounding only; not another exposure'})
    return rows + [total]


def compare_scenarios(reference, current):
    """Require the same filing, market inputs, model and material set."""
    a, b = reference.request, current.request
    if reference.status not in ('ok','partial') or current.status not in ('ok','partial'):
        raise ValueError('Both comparison runs must have valid financial results.')
    if (a.ticker, a.context, a.accession, a.currency, a.unit, a.spend_basis, a.shock_horizon_days) != (
            b.ticker, b.context, b.accession, b.currency, b.unit, b.spend_basis, b.shock_horizon_days):
        raise ValueError('Comparison requires the same company, report, period, units and horizon.')
    if {m.material for m in a.materials} != {m.material for m in b.materials}:
        raise ValueError('Comparison requires the same selected materials.')
    if reference.model_version != current.model_version or reference.baseline['pools'] != current.baseline['pools']:
        raise ValueError('Financial baseline or model changed; save a new reference scenario.')
    fingerprint = reference.market_snapshot.get('fingerprint')
    if not fingerprint or fingerprint != current.market_snapshot.get('fingerprint'):
        raise ValueError('Market data changed. Save a new reference using current market data before isolating assumption changes.')
    old = {m.material:m for m in a.materials}
    assumptions = [{'Material':m.material, 'Reference spend ($M)':old[m.material].period_spend,
                    'Current spend ($M)':m.period_spend, 'Reference unhedged (%)':old[m.material].unhedged_fraction*100,
                    'Current unhedged (%)':m.unhedged_fraction*100,
                    'Reference shock (%)':reference.simulation['materials'][m.material]['shock_pct'],
                    'Current shock (%)':current.simulation['materials'][m.material]['shock_pct'],
                    'Reference pass-through (%)':a.pass_through_fraction*100,
                    'Current pass-through (%)':b.pass_through_fraction*100} for m in b.materials]
    kpis = []
    for pool, values in current.simulation['stressed_pools'].items():
        for metric, row in values.items():
            prior = reference.simulation['stressed_pools'][pool][metric]
            kpis.append({'Metric':metric, 'Reported / derived baseline ($M)':row['base'],
                         'Reference scenario ($M)':prior['stressed'], 'Current scenario ($M)':row['stressed'],
                         'Change vs reference ($M)':round(row['stressed']-prior['stressed'],2)})
    prior = {row['Material']:row for row in contribution_rows(reference.simulation)}
    materials = []
    for row in contribution_rows(current.simulation):
        name = row['Material']
        old_cost = prior.get(name,{}).get('COGS contribution ($M)',0)
        materials.append({'Material':name, 'Reference COGS contribution ($M)':old_cost,
                          'Current COGS contribution ($M)':row['COGS contribution ($M)'],
                          'Change vs reference ($M)':round(row['COGS contribution ($M)']-old_cost,2)})
    if 'Display rounding reconciliation' in prior and not any(r['Material']=='Display rounding reconciliation' for r in materials):
        old_cost=prior['Display rounding reconciliation']['COGS contribution ($M)']
        materials.insert(-1,{'Material':'Display rounding reconciliation','Reference COGS contribution ($M)':old_cost,
                            'Current COGS contribution ($M)':0,'Change vs reference ($M)':-old_cost})
    return {'assumptions':assumptions, 'kpis':kpis, 'materials':materials}
