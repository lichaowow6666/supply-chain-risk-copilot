"""Synthetic fixtures only; no external retrieval or company data required."""
from dataclasses import replace
import unittest
from pe_copilot.evidence import EvidenceReference
from pe_copilot.supply_chain import (
    Registry, Company, SourceDocument, Citation, Provenance, Entity,
    GraphClaim, GraphSnapshot, Observation,
)


class GraphFoundationTests(unittest.TestCase):
    def setUp(self):
        self.company = Company('0000000001', 'Synthetic One', 'TEST1', 'synthetic fixture')
        self.cid = self.company.company_id
        self.doc = SourceDocument(self.cid, '10-K', '2025-12-31', 'a'*64, 'fixture.txt', 'v1')
        self.prov = Provenance('synthetic', 'accepted', rationale='Test only', reviewed_by='test', reviewed_on='2026-01-01')
        self.root = Entity(self.cid, 'company', 'Synthetic One', self.prov, self.cid)
        self.material = Entity('concept:material:copper', 'material', 'Copper', self.prov)
        self.claim = GraphClaim('c1', self.cid, 'uses', self.material.entity_id, self.prov)
        self.snapshot = GraphSnapshot('v1', self.cid, (self.doc.document_id,), (self.root, self.material), (self.claim,))

    def test_registry_multiple_filings_and_round_trip(self):
        reg = Registry(); reg.register_company(self.company)
        first = reg.register_document(self.doc)
        second = reg.register_document(replace(self.doc, content_sha256='b'*64, period_end='2024-12-31'))
        self.assertNotEqual(first, second)
        restored = Registry.from_json(reg.to_json())
        self.assertEqual(restored.get_document(first), self.doc)
        restored.validate_snapshot(self.snapshot)
        self.assertEqual(restored.to_json(), reg.to_json())

    def test_registry_rejects_overwrite_and_unknown_company(self):
        reg = Registry()
        with self.assertRaises(ValueError): reg.register_document(self.doc)
        reg.register_company(self.company); reg.register_document(self.doc)
        with self.assertRaises(ValueError): reg.register_company(replace(self.company, name='Different'))
        with self.assertRaises(ValueError): reg.register_document(replace(self.doc, parsing_version='v2'))
        self.assertEqual(reg.get_company(self.cid), self.company)

    def test_invalid_identity_metadata(self):
        for kwargs in ({'content_sha256':'bad'}, {'period_end':'2025-02-30'}, {'accession':'bad'}, {'filing_date':'2025/01/01'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): replace(self.doc, **kwargs)
        with self.assertRaises(ValueError): replace(self.company, cik='1')

    def test_origin_and_review_are_independent(self):
        citation = Citation(self.doc.document_id, 'Item 1 paragraph 2', EvidenceReference('fixture', 'Synthetic company uses copper'))
        candidate = Provenance('confirmed', 'candidate', (citation,))
        with self.assertRaises(ValueError): replace(candidate, citations=())
        with self.assertRaises(ValueError): replace(candidate, review='accepted')
        accepted = replace(candidate, review='accepted', reviewed_by='test', reviewed_on='2026-01-01')
        claim = replace(self.claim, provenance=accepted)
        snap = replace(self.snapshot, claims=(claim,))
        self.assertEqual(GraphSnapshot.from_json(snap.to_json()), snap)
        with self.assertRaises(ValueError): replace(self.snapshot, claims=(replace(claim, provenance=candidate),))

    def test_scope_and_dangling_links(self):
        with self.assertRaises(ValueError): Entity('segment:other', 'segment', 'Other', self.prov, self.cid)
        with self.assertRaises(ValueError): Entity('concept:facility:plant', 'facility', 'Plant', self.prov)
        other = Entity('company:0000000002:segment:other', 'segment', 'Other', self.prov, 'company:0000000002')
        with self.assertRaises(ValueError): replace(self.snapshot, entities=(*self.snapshot.entities, other))
        with self.assertRaises(ValueError): replace(self.snapshot, claims=(replace(self.claim, object='missing'),))
        with self.assertRaises(ValueError): replace(self.snapshot, entities=(self.root, self.root))

    def test_citations_must_belong_to_snapshot(self):
        citation = Citation('unregistered', 'Item 1', EvidenceReference('fixture', 'Test'))
        prov = replace(self.prov, citations=(citation,))
        with self.assertRaises(ValueError): replace(self.snapshot, claims=(replace(self.claim, provenance=prov),))

    def observation(self, **kwargs):
        data = dict(observation_id='o1', subject=self.cid, metric='revenue', value='-0.800', unit='currency_millions', currency='USD', period_start='2025-01-01', period_end='2025-12-31', provenance=self.prov)
        return Observation(**{**data, **kwargs})

    def test_observation_units_signs_periods_and_roundtrip(self):
        obs = self.observation()
        snap = replace(self.snapshot, observations=(obs,))
        restored = GraphSnapshot.from_json(snap.to_json())
        self.assertEqual(restored, snap)
        self.assertEqual(restored.observations[0].value, '-0.800')
        for kwargs in ({'currency':None}, {'period_end':'2024-01-01'}, {'value':'NaN'}, {'value':1.0}, {'basis':'derived'}, {'basis':'assumed'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): self.observation(**kwargs)

    def test_zero_missing_and_not_applicable_are_distinct(self):
        self.assertEqual(self.observation(value='0').value, '0')
        for state in ('missing', 'not_applicable'):
            self.assertIsNone(self.observation(value=None, availability=state).value)
            with self.assertRaises(ValueError): self.observation(value='0', availability=state)

    def test_derivations_require_resolved_inputs(self):
        obs = self.observation(basis='derived', input_ids=('missing',), derivation='Explicit formula')
        with self.assertRaises(ValueError): replace(self.snapshot, observations=(obs,))
        with self.assertRaises(ValueError): replace(self.snapshot, schema_version=2)

    def test_derivation_cycles_rejected_and_valid_inputs_preserved(self):
        first = self.observation()
        second = self.observation(observation_id='o2', basis='derived', input_ids=('o1',), derivation='Identity for fixture')
        snap = replace(self.snapshot, observations=(first, second))
        self.assertEqual(GraphSnapshot.from_json(snap.to_json()), snap)
        circular = replace(first, basis='derived', input_ids=('o2',), derivation='Circular fixture')
        with self.assertRaisesRegex(ValueError, 'cyclic'):
            replace(self.snapshot, observations=(circular, second))


if __name__ == '__main__':
    unittest.main()
