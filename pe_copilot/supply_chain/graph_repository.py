"""Versioned local graph bundles and bounded, evidence-preserving queries."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Tuple

import networkx as nx

from pe_copilot.evidence import EvidenceStatus
from .preparation import PreparedDocument
from .registry import Registry
from .schema import GraphClaim, GraphSnapshot


@dataclass(frozen=True)
class ExposurePath:
    entity_ids: Tuple[str, ...]
    claims: Tuple[GraphClaim, ...]
    conditional: bool
    quantification: str = 'unquantified exposure; no loss allocation'


# Direction and endpoint types are both checked. Ownership/hierarchy cannot
# accidentally act as a supply dependency; commercial links terminate at markets.
PROFILE_RULES = {
    'material_exposure': {
        'used_in': ({'material', 'material_cohort'}, {'product'}),
        'may_be_used_in': ({'material', 'material_cohort'}, {'product'}),
        'is_material_in': ({'material'}, {'product'}),
        'belongs_to': ({'product'}, {'product', 'segment'}),
        'serves': ({'product', 'segment'}, {'end_market'}),
        'reports_sales_to': ({'segment'}, {'end_market'}),
    },
    'physical_supply': {
        'produces': ({'operation'}, {'material', 'product'}),
        'input_to': ({'material'}, {'operation'}),
        'supplies': ({'supplier_pool'}, {'operation'}),
        'supplies_materials_to': ({'facility'}, {'facility'}),
        'supplies_beryllium_hydroxide_to': ({'operation'}, {'facility'}),
    },
    'financial_mechanisms': {
        'price_affects': ({'material', 'material_cohort'}, {'mechanism', 'exposure', 'kpi'}),
        'price_transmitted_via': ({'material', 'material_cohort'}, {'mechanism'}),
        'affects': ({'mechanism'}, {'exposure', 'kpi'}),
        'may_affect': ({'mechanism'}, {'kpi'}),
        'may_constrain': ({'mechanism'}, {'exposure'}),
        'may_create': ({'mechanism'}, {'exposure'}),
        'may_reduce_business_opportunities': ({'exposure'}, {'exposure'}),
        'may_increase': ({'exposure'}, {'exposure', 'kpi'}),
        'may_reduce': ({'exposure'}, {'kpi'}),
        'may_cause': ({'exposure'}, {'exposure'}),
        'increases_pre_tax_cost_affecting': ({'exposure'}, {'kpi'}),
        'disruption_may_cause': ({'facility', 'operation'}, {'exposure'}),
        'enables': ({'mechanism'}, {'mechanism'}),
        'excludes_metal_value_from': ({'mechanism'}, {'kpi'}),
        'reduces_investment_in': ({'mechanism'}, {'exposure'}),
        'relates_to': ({'exposure'}, {'kpi'}),
    },
}


class GraphView:
    def __init__(self, snapshot, registry, prepared_documents, coverage):
        self.snapshot = snapshot
        self.registry = registry
        self._documents = prepared_documents
        self._coverage = json.loads(json.dumps(coverage))
        self._entities = {e.entity_id: e for e in snapshot.entities}
        graph = nx.MultiDiGraph()
        graph.add_nodes_from(self._entities)
        for claim in snapshot.claims:
            graph.add_edge(claim.subject, claim.object, key=claim.claim_id, claim=claim)
        self._graph = nx.freeze(graph)

    def entity(self, entity_id):
        return self._entities[entity_id]

    def search(self, query, kind=None, confirmed_only=True):
        needle = query.strip().casefold()
        return tuple(e for e in self.snapshot.entities
                     if (kind is None or e.kind == kind)
                     and (not confirmed_only or e.provenance.origin == EvidenceStatus.CONFIRMED)
                     and (needle in e.label.casefold() or needle in e.entity_id.casefold()))

    def neighbors(self, entity_id, relations=None, direction='out', confirmed_only=True):
        self.entity(entity_id)
        if direction not in ('in', 'out'):
            raise ValueError('direction must be in or out')
        edges = self._graph.out_edges(entity_id, data=True) if direction == 'out' else self._graph.in_edges(entity_id, data=True)
        return tuple(sorted((d['claim'] for _, _, d in edges
                             if (relations is None or d['claim'].relation in relations)
                             and (not confirmed_only or all(
                                 p.origin == EvidenceStatus.CONFIRMED for p in (
                                     d['claim'].provenance, self.entity(d['claim'].subject).provenance,
                                     self.entity(d['claim'].object).provenance)))), key=lambda c: c.claim_id))

    def paths(self, start, profile='material_exposure', max_hops=6, limit=100, target=None, confirmed_only=True):
        """Return bounded simple paths; each edge carries its original citations."""
        self.entity(start)
        if target is not None:
            self.entity(target)
        if profile not in PROFILE_RULES:
            raise ValueError('unknown traversal profile')
        if not isinstance(max_hops, int) or isinstance(max_hops, bool) or not 1 <= max_hops <= 12:
            raise ValueError('max_hops must be 1..12')
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError('limit must be 1..1000')
        rules = PROFILE_RULES[profile]
        found = []
        stack = [((start,), ())]
        # Independent exploration budget bounds dense graphs even with no matches.
        examined = 0
        while stack and len(found) < limit and examined < 10000:
            nodes, claims = stack.pop(); examined += 1
            if claims and (target is None or nodes[-1] == target):
                found.append(ExposurePath(nodes, claims, any('may_' in c.relation or c.relation == 'may_be_used_in' for c in claims)))
            if len(claims) >= max_hops or (target is not None and nodes[-1] == target):
                continue
            for claim in reversed(self.neighbors(nodes[-1], rules, confirmed_only=confirmed_only)):
                left, right = self.entity(claim.subject), self.entity(claim.object)
                source_kinds, target_kinds = rules[claim.relation]
                if left.kind not in source_kinds or right.kind not in target_kinds or right.entity_id in nodes:
                    continue
                if dict(left.attributes).get('operational') == 'false' or dict(right.attributes).get('operational') == 'false':
                    continue
                stack.append(((*nodes, right.entity_id), (*claims, claim)))
        return {'snapshot_id': self.snapshot.snapshot_id, 'company_id': self.snapshot.company_id,
                'document_ids': self.snapshot.document_ids, 'profile': profile,
                'paths': tuple(found), 'truncated': bool(stack),
                'warning': 'Paths describe exposure, not quantified losses or predicted contagion.'}

    def observations(self, subject=None, related_entity_id=None, period_end=None, metric=None, confirmed_only=True):
        if subject is not None:
            self.entity(subject)
        if related_entity_id is not None:
            self.entity(related_entity_id)
        return tuple(o for o in self.snapshot.observations
                     if (subject is None or o.subject == subject)
                     and (related_entity_id is None or o.related_entity_id == related_entity_id)
                     and (period_end is None or o.period_end == period_end)
                     and (metric is None or o.metric == metric)
                     and (not confirmed_only or o.provenance.origin == EvidenceStatus.CONFIRMED))

    def evidence(self, record_id):
        for record in (*self.snapshot.entities, *self.snapshot.claims, *self.snapshot.observations):
            if record_id in (getattr(record, 'entity_id', None), getattr(record, 'claim_id', None), getattr(record, 'observation_id', None)):
                return tuple({'citation': asdict(c), 'resolved_excerpt': self._documents[c.document_id].resolve(c.locator)} for c in record.provenance.citations)
        raise KeyError(record_id)

    def gaps(self):
        return tuple(json.loads(json.dumps(row)) for row in self._coverage if row['disposition'] != 'included')


class GraphRepository:
    def __init__(self, root='artifacts/evidence_graph'):
        self.root = Path(root)

    def _directory(self, company_id, snapshot_id):
        if not re.fullmatch(r'company:\d{10}', company_id):
            raise ValueError('invalid company_id')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', snapshot_id):
            raise ValueError('invalid snapshot_id')
        return self.root / company_id.split(':')[1] / snapshot_id

    @staticmethod
    def _validate(snapshot, registry, documents):
        registry.validate_snapshot(snapshot)
        if set(documents) != set(snapshot.document_ids):
            raise ValueError('prepared documents do not match snapshot')
        for doc_id, prepared in documents.items():
            source = registry.get_document(doc_id)
            if source.content_sha256 != prepared.content_sha256 or source.parsing_version != prepared.parsing_version:
                raise ValueError('prepared source identity mismatch')
        for record in (*snapshot.entities, *snapshot.claims, *snapshot.observations):
            for citation in record.provenance.citations:
                if documents[citation.document_id].resolve(citation.locator) != citation.reference.evidence_excerpt:
                    raise ValueError('citation excerpt does not match prepared source')

    def save(self, snapshot, registry, prepared_documents, coverage=(), reconciliations=()):
        self._validate(snapshot, registry, prepared_documents)
        destination = self._directory(snapshot.company_id, snapshot.snapshot_id)
        files = {'snapshot.json': snapshot.to_json(), 'registry.json': registry.to_json(),
                 'coverage.json': json.dumps(coverage, sort_keys=True, indent=2),
                 'reconciliation.json': json.dumps(reconciliations, sort_keys=True, indent=2)}
        source_files = {}
        for doc_id, prepared in prepared_documents.items():
            filename = f'source-{sha256(doc_id.encode()).hexdigest()}.json'
            source_files[doc_id] = filename
            files[filename] = prepared.to_json()
        manifest = {'bundle_version': 1, 'company_id': snapshot.company_id, 'snapshot_id': snapshot.snapshot_id,
                    'sources': source_files, 'sha256': {name: sha256(value.encode()).hexdigest() for name, value in files.items()}}
        files['manifest.json'] = json.dumps(manifest, sort_keys=True, indent=2)
        if destination.exists():
            if all((destination/name).is_file() and (destination/name).read_text() == value for name, value in files.items()):
                return destination
            raise FileExistsError('snapshot is immutable; use a new snapshot_id')
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.graph-', dir=destination.parent))
        try:
            for name, value in files.items():
                (staging/name).write_text(value, encoding='utf-8')
            os.rename(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return destination

    def load(self, company_id, snapshot_id):
        directory = self._directory(company_id, snapshot_id)
        manifest = json.loads((directory/'manifest.json').read_text())
        if manifest.get('bundle_version') != 1 or manifest.get('company_id') != company_id or manifest.get('snapshot_id') != snapshot_id:
            raise ValueError('bundle identity/version mismatch')
        payloads = {}
        for name, expected in manifest['sha256'].items():
            if not re.fullmatch(r'[A-Za-z0-9._-]+\.json', name):
                raise ValueError('invalid bundle filename')
            payload = (directory/name).read_bytes()
            if sha256(payload).hexdigest() != expected:
                raise ValueError(f'bundle integrity check failed: {name}')
            payloads[name] = payload.decode('utf-8')
        snapshot = GraphSnapshot.from_json(payloads['snapshot.json'])
        if snapshot.company_id != company_id or snapshot.snapshot_id != snapshot_id:
            raise ValueError('snapshot identity mismatch')
        registry = Registry.from_json(payloads['registry.json'])
        documents = {doc_id: PreparedDocument.from_json(payloads[name]) for doc_id, name in manifest['sources'].items()}
        self._validate(snapshot, registry, documents)
        return GraphView(snapshot, registry, documents, json.loads(payloads['coverage.json']))

    def list_snapshots(self, company_id):
        directory = self._directory(company_id, 'placeholder').parent
        if not directory.exists():
            return ()
        return tuple(sorted(p.name for p in directory.iterdir() if p.is_dir() and (p/'manifest.json').is_file()))

    def list_company_ids(self):
        if not self.root.exists():
            return ()
        return tuple('company:' + p.name for p in sorted(self.root.iterdir())
                     if p.is_dir() and re.fullmatch(r'\d{10}', p.name))
