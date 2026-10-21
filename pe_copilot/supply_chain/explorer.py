"""Build a frontend-neutral graph view using explicit filing and observation context."""
from dataclasses import asdict
from decimal import Decimal
from .graph_repository import PROFILE_RULES


LENSES = {
    'material_exposure': 'Materials → products → segments → markets',
    'physical_supply': 'Operations & facility dependencies',
    'financial_mechanisms': 'Financial transmission mechanisms',
    'all_relationships': 'All disclosed relationships',
}


def snapshot_summary(view):
    company = view.registry.get_company(view.snapshot.company_id)
    documents = [view.registry.get_document(d) for d in view.snapshot.document_ids]
    periods = sorted({(o.period_start, o.period_end) for o in view.snapshot.observations if not o.horizon and o.period_start != o.period_end}, key=lambda p:(p[1],p[0]), reverse=True)
    if not periods:
        periods = sorted({(o.period_start,o.period_end) for o in view.snapshot.observations if not o.horizon}, reverse=True)
    return {'company_id': company.company_id, 'ticker': company.ticker, 'company_name': company.name,
            'snapshot_id': view.snapshot.snapshot_id, 'documents': [asdict(d) for d in documents],
            'period_end': max(d.period_end for d in documents),
            'forms': sorted({d.form for d in documents if d.form != 'XBRL-extract'}), 'periods': periods,
            'automated': any(k == 'extraction_method' for e in view.snapshot.entities for k,v in e.attributes),
            'filing_date': max((d.filing_date or '') for d in documents)}


def eligible_claim(view, claim, lens, confirmed_only):
    left, right = view.entity(claim.subject), view.entity(claim.object)
    if confirmed_only and any(p.origin != 'confirmed' for p in (claim.provenance, left.provenance, right.provenance)):
        return False
    if lens == 'all_relationships':
        return True
    rule = PROFILE_RULES[lens].get(claim.relation)
    if not rule or left.kind not in rule[0] or right.kind not in rule[1]:
        return False
    return all(dict(e.attributes).get('operational') != 'false' for e in (left, right))


def directed_chain(focus, claims, node_ids, max_depth=12):
    """Upstream and downstream independently; never flood through sibling branches.

    All-relations mode is descriptive connectivity only, not causal propagation.
    The bounded traversal preserves the edges that actually connect each chain.
    """
    included_nodes, included_edges = {focus}, set()
    truncated = False
    for reverse in (False, True):
        adjacency = {}
        for claim in claims:
            a, b = (claim.object, claim.subject) if reverse else (claim.subject, claim.object)
            adjacency.setdefault(a, []).append((b, claim.claim_id))
        frontier, visited = {focus}, {focus}
        for _ in range(max_depth):
            following = set()
            for current in frontier:
                for neighbor, edge_id in adjacency.get(current, []):
                    if neighbor not in node_ids:
                        continue
                    included_edges.add(edge_id); included_nodes.add(neighbor)
                    if neighbor not in visited:
                        following.add(neighbor); visited.add(neighbor)
            frontier = following
            if not frontier:
                break
        if any(neighbor not in visited for current in frontier for neighbor, _ in adjacency.get(current, [])):
            truncated = True
    return {'nodes': sorted(included_nodes), 'edges': sorted(included_edges), 'truncated': truncated}


def format_observation(observation, labels):
    value = observation.value
    if value is None:
        formatted = observation.availability.replace('_', ' ')
    else:
        number = Decimal(value)
        if observation.unit == 'ratio':
            formatted = f'{number * 100:.1f}%'
        else:
            units = {'currency_thousands': 'thousand', 'currency_millions': 'million'}
            formatted = f'{number:,.3f}'.rstrip('0').rstrip('.') if '.' in str(number) else f'{number:,}'
            formatted += ' ' + ' '.join(x for x in (observation.currency, units.get(observation.unit, observation.unit)) if x)
    metric = observation.metric.replace('_', ' ')
    related = labels.get(observation.related_entity_id)
    if related:
        metric += ' · ' + related
    period = f'{observation.period_start} to {observation.period_end}' if observation.period_start != observation.period_end else f'As of {observation.period_end} (balance sheet instant)'
    if observation.horizon:
        period = f'{observation.horizon} hypothetical sensitivity · as of {observation.as_of or observation.period_end}'
    return {'id': observation.observation_id, 'metric': metric, 'value': formatted,
            'period': period, 'basis': observation.basis, 'availability': observation.availability,
            'assumptions': list(observation.assumptions), 'raw': asdict(observation)}


def build_explorer_payload(view, request):
    summary = snapshot_summary(view)
    if request.focus_entity_id is not None:
        view.entity(request.focus_entity_id)
    selected_period = (request.context.period_start, request.context.period_end)
    if selected_period[0] is not None and selected_period not in summary['periods']:
        raise ValueError('observation period is not available in this filing snapshot')
    warnings = ['Highlights indicate evidenced exposure or disclosed connectivity; graph losses have not been calculated.']
    labels = {e.entity_id: e.label for e in view.snapshot.entities}
    claims = [c for c in view.snapshot.claims if eligible_claim(view, c, request.lens, request.confirmed_only)]
    node_ids = {i for c in claims for i in (c.subject, c.object)}
    if request.lens == 'all_relationships':
        node_ids.update(e.entity_id for e in view.snapshot.entities if not request.confirmed_only or e.provenance.origin == 'confirmed')
    if request.focus_entity_id is not None:
        focus = view.entity(request.focus_entity_id)
        if not request.confirmed_only or focus.provenance.origin == 'confirmed':
            node_ids.add(focus.entity_id)
    chains = {n: directed_chain(n, claims, node_ids) for n in node_ids}
    if any(c['truncated'] for c in chains.values()):
        warnings.append('Some displayed chains exceed the 12-hop limit and are truncated.')

    overlay = {'source': None, 'run_id': None, 'nodes': [], 'edges': [], 'roots': [], 'shocks': [], 'warnings': []}
    signal = request.highlight
    if signal is not None:
        valid = (signal.ticker.upper() == summary['ticker'].upper()
                 and (signal.company_id is None or signal.company_id == summary['company_id'])
                 and (signal.snapshot_id is None or signal.snapshot_id == summary['snapshot_id']))
        if not valid:
            warnings.append('The highlight request belongs to a different company or filing snapshot; it was not applied.')
        else:
            roots = set()
            for entity_id in signal.entity_ids:
                if entity_id in labels:
                    roots.add(entity_id)
                else:
                    warnings.append('An entity in the highlight request is not present in this snapshot.')
            for shock in signal.material_shocks:
                matches = [e for e in view.snapshot.entities if e.kind in ('material', 'material_cohort') and e.label.casefold() == shock.material.casefold()]
                if not matches:
                    warnings.append(f'No evidenced material matches {shock.material}; no chain was invented.')
                elif shock.shock_fraction != 0:
                    roots.update(e.entity_id for e in matches)
                overlay['shocks'].append({'material': shock.material, 'shock_fraction': shock.shock_fraction})
            if signal.source == 'tab2' and signal.snapshot_id is None:
                warnings.append('Tab 2 does not identify its financial-baseline filing period. This overlay maps material exposure onto the selected filing; it does not reconcile or allocate Tab 2 dollar impacts.')
            highlighted_nodes, highlighted_edges = set(), set()
            # Overlay uses permitted downstream exposure paths, even in all-relations mode.
            # It never follows company ownership/hierarchy in reverse as a shock mechanism.
            profiles = tuple(PROFILE_RULES) if request.lens == 'all_relationships' else (request.lens,)
            for root in sorted(roots):
                if request.confirmed_only and view.entity(root).provenance.origin != 'confirmed':
                    continue
                highlighted_nodes.add(root)
                for profile in profiles:
                    paths = view.paths(root, profile=profile, max_hops=12, limit=1000, confirmed_only=request.confirmed_only)
                    for path in paths['paths']:
                        highlighted_nodes.update(path.entity_ids)
                        highlighted_edges.update(c.claim_id for c in path.claims)
                    if paths['truncated']:
                        warnings.append('Highlight paths were truncated at the traversal limit.')
            overlay.update(source=signal.source, run_id=signal.run_id, created_at=signal.created_at, horizon=signal.horizon,
                           nodes=sorted(highlighted_nodes & node_ids), edges=sorted(highlighted_edges & {c.claim_id for c in claims}),
                           roots=sorted(roots & node_ids))
            if roots and not overlay['nodes']:
                warnings.append('Highlighted entities are outside this lens. Choose All disclosed relationships to inspect them.')

    if request.focus_only and request.focus_entity_id in chains:
        visible = set(chains[request.focus_entity_id]['nodes'])
        node_ids &= visible
        claims = [c for c in claims if c.subject in node_ids and c.object in node_ids]
        overlay['nodes'] = sorted(set(overlay['nodes']) & node_ids)
        overlay['edges'] = sorted(set(overlay['edges']) & {c.claim_id for c in claims})
    all_observations = [o for o in view.snapshot.observations
                        if (not request.confirmed_only or o.provenance.origin == 'confirmed')
                        and (o.horizon or selected_period[0] is None or (o.period_start, o.period_end) == selected_period or (o.period_start == o.period_end == selected_period[1]))]
    observation_rows = [format_observation(o, labels) for o in all_observations]
    nodes = []
    for entity in view.snapshot.entities:
        if entity.entity_id not in node_ids:
            continue
        # A related material can show company-level sensitivity without pretending
        # it is a material- or segment-level dollar allocation.
        related = [row for row in observation_rows if row['raw']['subject'] == entity.entity_id or
                   (row['raw']['related_entity_id'] == entity.entity_id and row['raw']['horizon'])]
        related.sort(key=lambda r: (bool(r['raw']['related_entity_id']), r['raw']['metric']))
        nodes.append({'id': entity.entity_id, 'label': entity.label, 'kind': entity.kind.value,
                      'origin': entity.provenance.origin.value, 'attributes': dict(entity.attributes),
                      'evidence': [asdict(c) for c in entity.provenance.citations], 'observations': related})
    edge_rows = [{'id': c.claim_id, 'source': c.subject, 'target': c.object,
                  'label': c.relation.replace('_', ' '), 'relation': c.relation,
                  'origin': c.provenance.origin.value, 'effective_date': c.effective_date,
                  'evidence': [asdict(citation) for citation in c.provenance.citations]} for c in claims]
    visible_edges = {c.claim_id for c in claims}
    chains = {n: {'nodes': sorted(set(chain['nodes']) & node_ids), 'edges': sorted(set(chain['edges']) & visible_edges), 'truncated': chain['truncated']}
              for n, chain in chains.items() if n in node_ids}
    return {'summary': summary, 'nodes': nodes, 'edges': edge_rows, 'chains': chains,
            'lens': request.lens, 'lens_label': LENSES[request.lens], 'focus': request.focus_entity_id,
            'overlay': overlay, 'observations': observation_rows, 'gaps': list(view.gaps()),
            'warnings': list(dict.fromkeys(warnings)), 'observation_period': selected_period}
