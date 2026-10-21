"""Build a local evidence bundle from a reviewed company-specific catalog.

Run: venv/bin/python -m pe_copilot.supply_chain.build --catalog PATH
"""
import argparse
from collections import Counter
import json
from pathlib import Path

from .curation import build_catalog
from .graph_repository import GraphRepository
from .preparation import prepare_document


def build(catalog_path, source_path=None, output='artifacts/evidence_graph'):
    catalog = json.loads(Path(catalog_path).read_text())
    source_path = source_path or catalog['source']['local_path']
    prepared = prepare_document(source_path)
    registry, snapshot, reconciliations = build_catalog(prepared, catalog)
    directory = GraphRepository(output).save(snapshot, registry, {snapshot.document_ids[0]: prepared}, catalog['coverage'], reconciliations)
    # Expanded review ledger contains individual records and their locators.
    ledger = ['# Accepted graph records: ' + catalog['company']['name'], '',
              'Source review: '+catalog['reviewed_by']+'. These records are not human-approved.', '',
              '| Type | Record | Evidence locators |', '|---|---|---|']
    for kind, records, key in [('Entity',snapshot.entities,'entity_id'),('Claim',snapshot.claims,'claim_id'),('Observation',snapshot.observations,'observation_id')]:
        for record in records:
            ledger.append(f'| {kind} | {getattr(record,key)} | '+', '.join(c.locator for c in record.provenance.citations)+' |')
    return {'directory': str(directory), 'company_id': snapshot.company_id, 'snapshot_id': snapshot.snapshot_id,
            'entities':len(snapshot.entities),'claims':len(snapshot.claims),'observations':len(snapshot.observations),
            'blocks':len(prepared.blocks),'chunks':len(prepared.chunks),
            'coverage':dict(Counter(row['disposition'] for row in catalog['coverage'])),
            'reconciliations':reconciliations}, '\n'.join(ledger)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--source')
    parser.add_argument('--output', default='artifacts/evidence_graph')
    args=parser.parse_args()
    summary, ledger = build(args.catalog,args.source,args.output)
    # Review report is regenerated outside the immutable data bundle.
    report=Path(args.output)/f'{summary["snapshot_id"]}-review.md'
    report.write_text(ledger)
    print(json.dumps(summary,indent=2))


if __name__ == '__main__':
    main()
