"""Conservative scenario annotations. Never allocate company costs to segments.

Signs describe the company's cost/profit perspective, not commodity returns.
Only input-to-product/operation and product-to-segment links transmit a cost
pressure hypothesis. Commercial markets, ownership and ambiguous mechanisms
remain unquantified and unsigned. Explanations are deterministic, source-linked
templates over the saved calculation; no additional LLM call is necessary.
"""
from copy import deepcopy


COST_LINKS = {
    'used_in': ({'material', 'material_cohort'}, {'product'}),
    'may_be_used_in': ({'material', 'material_cohort'}, {'product'}),
    'is_material_in': ({'material'}, {'product'}),
    'input_to': ({'material'}, {'operation'}),
    'belongs_to': ({'product'}, {'product', 'segment'}),
}


def cost_direction(cost):
    return 'adverse' if cost > 0 else 'favorable' if cost < 0 else 'neutral'


def annotate_scenario(payload, request, simulation, exposures):
    """Attach annotations to a fresh payload from the same validated scenario."""
    if (payload['summary']['company_id'] != request.context.company_id or
            payload['summary']['snapshot_id'] != request.context.snapshot_id):
        raise ValueError('Scenario annotation scope does not match graph')
    payload = deepcopy(payload)
    nodes = {n['id']: n for n in payload['nodes']}
    edges = {e['id']: e for e in payload['edges']}
    for row in (*nodes.values(), *edges.values()):
        row.pop('impact', None)
    overlay = payload['overlay']
    node_impacts, edge_impacts, explanations = {}, {}, []
    for key in overlay['nodes']:
        node_impacts[key] = []
    for key in overlay['edges']:
        edge_impacts[key] = []
    for material in request.materials:
        result = simulation['materials'][material.material]
        # Use the saved cost result rather than recomputing from rounded shocks.
        components = result.get('cost_contribution')
        gross = components['gross'] if components else result['gross_cost_increase']
        absorbed = round(components['absorbed'] if components else gross * (1 - request.pass_through_fraction), 2)
        passed = round(components['passed'] if components else gross * request.pass_through_fraction, 2)
        direction = cost_direction(absorbed)
        roots = [n['id'] for n in nodes.values() if n['kind'] in ('material', 'material_cohort')
                 and n['label'].casefold() == material.material.casefold()]
        evidence = [chunk for exposure in exposures if exposure['material'].casefold() == material.material.casefold()
                    for chunk in exposure.get('raw_chunks', [])]
        explanation = {
            'material': material.material, 'direction': direction,
            'basis': 'deterministic calculation + user assumptions + filing evidence',
            'text': (f"{material.material}: the saved {request.shock_horizon_days}-day price shock is {result['shock_pct']:+.2f}%. "
                     f"Using assumed {request.spend_basis} spend of ${material.period_spend:,.2f}M and "
                     f"{material.unhedged_fraction:.0%} unhedged exposure gives a gross cost change of ${gross:+,.2f}M. "
                     f"At {request.pass_through_fraction:.0%} pass-through, approximately ${passed:+,.2f}M is passed to customers "
                     f"and ${absorbed:+,.2f}M is absorbed by the company. "
                     "These are scenario estimates, not reported material spending or predicted losses."),
            'formula': 'Gross cost change = period spend × shock fraction × unhedged fraction; absorbed cost = gross change × (1 − pass-through).',
            'amount': absorbed, 'gross_cost_change': gross, 'passed_to_customers': passed,
            'unit': 'USD millions', 'spend_basis': request.spend_basis,
            'accession': request.accession, 'snapshot_id': request.context.snapshot_id,
            'period_start': request.context.period_start, 'period_end': request.context.period_end,
            'horizon': f'{request.shock_horizon_days}-day shock applied to {request.spend_basis} spend',
            'evidence': evidence, 'root_ids': roots,
            'limits': ['Displayed material amounts are rounded; see the contribution table for reconciliation to the company total.',
                       'No entity-level spend weights or elasticities are disclosed here; downstream amounts are not allocated.',
                       'A cost-pressure connection does not predict output, demand or market share.']}
        if not roots:
            explanation['limits'].append('No matching graph entity exists; no supply-chain connection was invented.')
        if not evidence:
            explanation['limits'].append('No material source excerpts are available in this run; consult graph evidence where present.')
        explanations.append(explanation)
        for root in roots:
            root_note = {'material': material.material, 'direction': direction,
                         'label': f'Absorbed cost {absorbed:+,.2f}M USD (company material scope)',
                         'amount': absorbed, 'basis': 'derived from saved cost; rounded',
                         'reason': explanation['text'], 'evidence': nodes[root]['evidence'], 'path': []}
            node_impacts.setdefault(root, []).append(root_note)
            # Paths are constrained by both endpoint types and relation semantics.
            frontier, visited = [root], {root: []}
            while frontier:
                current = frontier.pop(0)
                if len(visited[current]) >= 12:
                    continue
                for edge in edges.values():
                    rule = COST_LINKS.get(edge['relation'])
                    if (edge['id'] not in overlay['edges'] or edge['source'] != current or not rule or
                            nodes[edge['source']]['kind'] not in rule[0] or nodes[edge['target']]['kind'] not in rule[1]):
                        continue
                    path = visited[current] + [edge['id']]
                    note = {'material': material.material, 'direction': direction,
                            'label': 'Potential cost pressure' if direction == 'adverse' else 'Potential cost relief' if direction == 'favorable' else 'No modeled residual cost',
                            'amount': None, 'basis': 'conditional cost transmission assumption; not quantified',
                            'reason': f"{material.material} reaches this relationship through disclosed input/product links. "
                                      'Direction assumes the selected company-level spend, hedge and pass-through apply to this chain; local contracts may differ. No node or edge dollar allocation is available.',
                            'path': path,
                            'path_labels': [nodes[root]['label']] + [nodes[edges[eid]['target']]['label'] for eid in path],
                            'evidence': [c for eid in path for c in edges[eid]['evidence']]}
                    edge_impacts.setdefault(edge['id'], []).append(note)
                    node_impacts.setdefault(edge['target'], []).append(note)
                    if edge['target'] not in visited:
                        visited[edge['target']] = path
                        frontier.append(edge['target'])
    def package(notes):
        signs = {n['direction'] for n in notes}
        direction = next(iter(signs)) if len(signs) == 1 else 'uncertain'
        return {'direction': direction, 'notes': notes,
                'label': notes[0]['label'] if len(notes) == 1 else 'Mixed / multiple scenario paths' if notes else 'Connected exposure; direction unknown'}
    for nid, notes in node_impacts.items():
        if not notes:
            notes.append({'material':'Connected exposure', 'direction':'uncertain', 'amount':None,
                          'label':'Direction unknown; no dollar allocation', 'basis':'disclosed connectivity only',
                          'reason':'This entity is connected to a selected exposure, but the relationship does not establish how its costs, sales or profit change.',
                          'path':[], 'evidence':nodes[nid]['evidence']})
        nodes[nid]['impact'] = package(notes)
    for eid, notes in edge_impacts.items():
        if not notes:
            notes.append({'material':'Connected exposure', 'direction':'uncertain', 'amount':None,
                          'label':'Direction unknown; no dollar allocation', 'basis':'disclosed connectivity only',
                          'reason':'This disclosed relationship has no supported cost-transmission sign. A sales-market link, mitigation mechanism or general association cannot be treated as a loss propagation rule.',
                          'path':[eid], 'evidence':edges[eid]['evidence']})
        edges[eid]['impact'] = package(notes)
    total = simulation['waterfall_summary']['Net_COGS_Absorbed']
    kpis = [{'metric': metric, 'base': row['base'], 'stressed': row['stressed'],
             'change': round(row['stressed'] - row['base'], 2), 'unit': 'USD millions'}
            for pool in simulation['stressed_pools'].values() for metric, row in pool.items()]
    for node in nodes.values():
        if node['kind'] == 'company':
            node['impact'] = package([{'material': 'All selected materials', 'direction': cost_direction(total),
                'label': f'Company absorbed cost {total:+,.2f}M USD', 'amount': total,
                'basis': 'saved deterministic scenario total', 'path': [], 'evidence': [],
                'reason': 'Company total from the saved waterfall. Shown once at company scope; downstream values must not be added to this total.'}])
            node['impact']['kpis'] = kpis
    payload['scenario_explanations'] = explanations
    payload['scenario_financials'] = {'absorbed_cost': total, 'unit': 'USD millions', 'kpis': kpis,
                                     'run_id': overlay['run_id'], 'spend_basis': request.spend_basis}
    payload['warnings'] = [w for w in payload['warnings'] if 'graph losses have not been calculated' not in w]
    payload['warnings'].append('Red/green shows company cost direction or conditional cost pressure, not a quantified downstream loss. Amber means unsigned connectivity or mixed effects. No segment or product dollar allocation was made.')
    return payload


def apply_saved_annotations(payload, execution):
    """Apply saved annotations to filtered UI views only for the identical run."""
    if (not execution or not execution.graph or payload['overlay']['run_id'] != execution.run_id or
            payload['summary']['company_id'] != execution.request.context.company_id or
            payload['summary']['snapshot_id'] != execution.request.context.snapshot_id):
        return payload
    payload = deepcopy(payload)
    for kind in ('nodes', 'edges'):
        saved = {row['id']: row.get('impact') for row in execution.graph[kind]}
        for row in payload[kind]:
            # Preserve evidence filters: paths containing hidden edges are omitted.
            impact = deepcopy(saved.get(row['id']))
            if impact:
                visible_edges = {e['id'] for e in payload['edges']}
                impact['notes'] = [n for n in impact['notes'] if set(n['path']) <= visible_edges]
                if not impact['notes']:
                    continue
                signs = {n['direction'] for n in impact['notes']}
                impact['direction'] = next(iter(signs)) if len(signs) == 1 else 'uncertain'
                impact['label'] = impact['notes'][0]['label'] if len(impact['notes']) == 1 else 'Mixed / multiple scenario paths'
                row['impact'] = impact
    for key in ('scenario_explanations', 'scenario_financials'):
        payload[key] = deepcopy(execution.graph.get(key, []))
    return payload
