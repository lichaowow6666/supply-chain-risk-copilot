"""Longitudinal filing evidence; no interpolation, silent restatement or entity merging."""
from datetime import date
from decimal import Decimal


def period_kind(start, end):
    days = (date.fromisoformat(end)-date.fromisoformat(start)).days + 1
    if days == 1:
        return 'Instant / balance sheet'
    if 70 <= days <= 110:
        return 'Quarter (reported)'
    if 160 <= days <= 200:
        return 'Six months year-to-date'
    if 250 <= days <= 290:
        return 'Nine months year-to-date'
    if 330 <= days <= 380:
        return 'Annual'
    return f'Other duration ({days} days)'


def observation_history(views, entity_id, policy='latest'):
    if policy not in ('latest','original'):
        raise ValueError('Unknown disclosure policy')
    rows = []
    for view in views:
        docs = [view.registry.get_document(d) for d in view.snapshot.document_ids]
        filing = next((d for d in docs if d.form.startswith(('10-K','10-Q'))),docs[0])
        for obs in view.snapshot.observations:
            if obs.subject != entity_id or obs.horizon:
                continue
            rows.append({'entity_id':entity_id,'metric':obs.metric,'value':float(Decimal(obs.value)) if obs.value is not None else None,
                'decimal_value':obs.value,'unit':obs.unit,'currency':obs.currency,'period_start':obs.period_start,'period_end':obs.period_end,
                'period_kind':period_kind(obs.period_start,obs.period_end),'related_entity_id':obs.related_entity_id,
                'availability':obs.availability,'basis':obs.basis,'form':filing.form,'filing_date':filing.filing_date or '',
                'accession':filing.accession,'snapshot_id':view.snapshot.snapshot_id,
                'source_url':next((c.reference.source_url for c in obs.provenance.citations if c.reference.source_url),None),
                'evidence':[{'document':c.reference.source_document,'locator':c.locator,'excerpt':c.reference.evidence_excerpt} for c in obs.provenance.citations]})
    selected, first_values = {}, {}
    for row in sorted(rows,key=lambda r:(r['filing_date'],r['accession'] or '',r['snapshot_id'])):
        key = tuple(row[k] for k in ('entity_id','metric','period_start','period_end','unit','currency','related_entity_id','basis'))
        first_values.setdefault(key,row['decimal_value'])
        if policy == 'latest' or key not in selected:
            selected[key] = {**row,'disclosure_changed':first_values[key] != row['decimal_value']}
    return sorted(selected.values(),key=lambda r:(r['period_end'],r['metric'],r['period_start']))


def relationship_history(views, entity_id):
    rows = []
    for view in views:
        if not any(e.entity_id == entity_id for e in view.snapshot.entities):
            continue
        docs = [view.registry.get_document(d) for d in view.snapshot.document_ids]
        filing = next(d for d in docs if d.form.startswith(('10-K','10-Q')))
        labels = {e.entity_id:e.label for e in view.snapshot.entities}
        for claim in view.snapshot.claims:
            if entity_id not in (claim.subject,claim.object):
                continue
            rows.append({'period_end':filing.period_end,'filing_date':filing.filing_date,'form':filing.form,
                         'subject':labels[claim.subject],'relation':claim.relation,'object':labels[claim.object],
                         'origin':claim.provenance.origin.value,'snapshot_id':view.snapshot.snapshot_id,
                         'source_url':claim.provenance.citations[0].reference.source_url if claim.provenance.citations else None})
    return sorted(rows,key=lambda r:(r['period_end'],r['filing_date'] or '',r['relation']))
