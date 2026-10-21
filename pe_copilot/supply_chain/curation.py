"""Build accepted snapshots from a hash-pinned, explicitly reviewed catalog."""
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import re

from pe_copilot.evidence import EvidenceReference
from .registry import Registry
from .schema import Company, SourceDocument, Citation, Provenance, Entity, GraphClaim, GraphSnapshot, Observation


def compact_row(row):
    return [cell for cell in row if cell and cell != '$']


def decimal_cell(token, dash_is_zero=False):
    if token in ('—', '–', '-'):
        if not dash_is_zero:
            raise ValueError('dash requires an explicit table-specific interpretation')
        return Decimal(0)
    numeric = token
    if token.startswith('(') and token.endswith(')') and '-' not in token:
        numeric = '-' + token[1:-1]
    if not re.fullmatch(r'-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?', numeric):
        raise ValueError(f'unsupported numerical cell: {token}')
    return Decimal(numeric.replace(',', ''))


def build_catalog(prepared, catalog):
    """Compile using a fixed decimal context, independent of caller settings."""
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        return _build_catalog(prepared, catalog)


def _build_catalog(prepared, catalog):
    """Catalog attestations are curated offline, not inferred from word co-occurrence."""
    if catalog['catalog_version'] != 1:
        raise ValueError('unsupported catalog version')
    source = catalog['source']
    if prepared.content_sha256 != source['content_sha256'] or prepared.parsing_version != source['parsing_version']:
        raise ValueError('source hash/parser differs from reviewed catalog; recuration required')
    for key, expected in source['expected_metadata'].items():
        if prepared.metadata.get(key) != expected:
            raise ValueError(f'filing identity mismatch: {key}')
    registry = Registry()
    company = Company(**catalog['company'])
    registry.register_company(company)
    document = SourceDocument(company.company_id, source['form'], source['period_end'], prepared.content_sha256,
                              source['local_path'], prepared.parsing_version, source['accession'], source['filing_date'])
    registry.register_document(document)

    def provenance(spec):
        citations = []
        for locator in spec['evidence']:
            excerpt = prepared.resolve(locator)
            block = prepared.block(locator.split(':')[0])
            url = source['source_url'] + (f'#{block.anchor}' if block.anchor else '')
            citations.append(Citation(document.document_id, locator, EvidenceReference(
                source_document=source['title'], evidence_excerpt=excerpt, source_url=url,
                filing_date=document.filing_date, evidence_section=block.section, effective_date=document.period_end)))
        return Provenance(spec.get('origin', 'confirmed'), 'accepted', tuple(citations),
                          spec.get('rationale'), catalog['reviewed_by'], catalog['reviewed_on'])

    entities, mapping = [], {}
    for spec in catalog['entities']:
        ref = spec['ref']
        if ref in mapping:
            raise ValueError(f'duplicate catalog ref: {ref}')
        entity_id = company.company_id if spec['kind'] == 'company' else f'{"concept" if spec.get("shared") else company.company_id}:{ref}'
        mapping[ref] = entity_id
        entities.append(Entity(entity_id, spec['kind'], spec['label'], provenance(spec),
                               None if spec.get('shared') else company.company_id,
                               tuple(sorted(spec.get('attributes', {}).items()))))

    claims = []
    for spec in catalog['claims']:
        claims.append(GraphClaim(spec['id'], mapping[spec['subject']], spec['relation'], mapping[spec['object']],
                                 provenance(spec), document.period_end))
    observations = []
    for spec in catalog.get('observations', []):
        value_spec = {k: v for k, v in spec.items() if k not in ('evidence', 'origin', 'rationale')}
        value_spec['subject'] = mapping[value_spec['subject']]
        if value_spec.get('related_entity_id'):
            value_spec['related_entity_id'] = mapping[value_spec['related_entity_id']]
        observations.append(Observation(**value_spec, provenance=provenance(spec)))

    reconciliations = []
    for table_spec in catalog.get('revenue_tables', []):
        block = prepared.block(table_spec['block_id'])
        segment_count = len(table_spec['segments'])
        if segment_count < 1 or segment_count != len(table_spec['segment_labels']):
            raise ValueError('invalid segment column configuration')
        header = compact_row(block.rows[0])
        if header != [table_spec['unit_label'], *table_spec['segment_labels'], 'Total']:
            raise ValueError('revenue table headings changed')
        segment_refs = table_spec['segments'] + ['company']
        sections = {}
        year_rows = {}
        year = None
        for row_index, row in enumerate(block.rows):
            cells = compact_row(row)
            if len(cells) == 1 and cells[0] in table_spec['years']:
                year = cells[0]
                if year in sections:
                    raise ValueError('duplicate fiscal year heading')
                sections[year] = []
                year_rows[year] = row_index
            elif year is not None and cells and cells[0] in [*table_spec['market_order'], 'Total']:
                if len(cells) != segment_count + 2:
                    raise ValueError('revenue row has unexpected column count')
                values = [decimal_cell(v, table_spec['dash_is_zero']) for v in cells[1:]]
                if sum(values[:-1]) != values[-1]:
                    raise ValueError('revenue row does not reconcile')
                sections[year].append((row_index, cells[0], cells[1:], values))
        if set(sections) != set(table_spec['years']):
            raise ValueError('missing fiscal year')
        for year, rows in sections.items():
            if [r[1] for r in rows] != [*table_spec['market_order'], 'Total']:
                raise ValueError('missing/duplicate/reordered revenue rows')
            totals = rows[-1][3]
            if any(sum(row[3][col] for row in rows[:-1]) != totals[col] for col in range(segment_count + 1)):
                raise ValueError('segment revenue totals do not reconcile')
            for row_index, market, raw_values, values in rows:
                p = provenance({'evidence': [f'{block.block_id}:row:0', f'{block.block_id}:row:{year_rows[year]}', f'{block.block_id}:row:{row_index}'],
                                'origin': table_spec.get('origin', 'confirmed'), 'rationale': table_spec.get('rationale')})
                for col, segment_ref in enumerate(segment_refs):
                    related = None if market == 'Total' else mapping[table_spec['markets'][market]]
                    oid = f'revenue:{year}:{segment_ref}:{market}'
                    observations.append(Observation(oid, mapping[segment_ref], 'net_sales', str(values[col]),
                        table_spec['unit'], f'{year}-01-01', f'{year}-12-31', p, currency=table_spec['currency'],
                        related_entity_id=related, source_value=raw_values[col],
                        assumptions=('A dash denotes nil in this audited revenue table; original token retained.',) if raw_values[col] in ('—','–','-') else ()))
                    if market != 'Total' and col < segment_count:
                        denominator_id = f'revenue:{year}:{segment_ref}:Total'
                        observations.append(Observation(f'share:{year}:{segment_ref}:{market}', mapping[segment_ref],
                            'share_of_segment_net_sales', str(values[col] / totals[col]) if totals[col] else None,
                            'ratio', f'{year}-01-01', f'{year}-12-31', p,
                            availability='reported' if totals[col] else 'not_applicable', basis='derived',
                            input_ids=(oid, denominator_id), derivation='market net sales / segment total net sales; no physical dependency or loss weight implied',
                            related_entity_id=related))
                    # Only current-year, positive revenue establishes this snapshot's commercial exposure edge.
                    if year == table_spec['active_year'] and market != 'Total' and col < segment_count and values[col] > 0:
                        claims.append(GraphClaim(f'commercial:{segment_ref}:{market}', mapping[segment_ref], 'reports_sales_to', related, p, document.period_end))
            reconciliations.append({'year': year, 'unit': table_spec['unit'], 'currency': table_spec['currency'], 'company_total': str(totals[-1]),
                                    'row_totals_match': True, 'segment_totals_match': True})

    snapshot = GraphSnapshot(catalog['snapshot_id'], company.company_id, (document.document_id,), tuple(entities), tuple(claims), tuple(observations))
    registry.validate_snapshot(snapshot)
    ids = {r.entity_id for r in entities} | {r.claim_id for r in claims} | {r.observation_id for r in observations}
    if len({gap['id'] for gap in catalog['coverage']}) != len(catalog['coverage']):
        raise ValueError('duplicate coverage IDs')
    for gap in catalog['coverage']:
        if gap['disposition'] not in ('included', 'unsupported', 'ambiguous', 'deferred') or not gap['reason']:
            raise ValueError('invalid coverage disposition')
        resolved = [mapping.get(ref, ref) for ref in gap['records']]
        if gap['disposition'] == 'included' and not resolved:
            raise ValueError('included coverage requires records')
        if any(ref not in ids for ref in resolved):
            raise ValueError(f'coverage points to missing record: {gap["id"]}')
        for locator in gap.get('evidence', []):
            prepared.resolve(locator)
    return registry, snapshot, reconciliations
