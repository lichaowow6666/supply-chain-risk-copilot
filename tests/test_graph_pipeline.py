"""Offline tests: synthetic parsing/build fixtures and optional local MTRN integration."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, localcontext
import json
from pathlib import Path
import tempfile
import unittest

from pe_copilot.evidence import EvidenceReference
from pe_copilot.supply_chain import Citation, Company, Entity, GraphClaim, GraphSnapshot, Observation, Provenance, Registry, SourceDocument
from pe_copilot.supply_chain.curation import build_catalog, decimal_cell
from pe_copilot.supply_chain.graph_repository import GraphRepository, GraphView
from pe_copilot.supply_chain.preparation import prepare_document, PreparedDocument

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT/'pe_copilot/supply_chain/catalogs/mtrn_2025.json'


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'filing.html'

    def test_hidden_data_removed_offsets_and_chunks_are_reproducible(self):
        self.path.write_text('<html><body><ix:header><p>HIDDEN</p></ix:header><div style="display: none">SECRET</div><div><p>Item 1. BUSINESS</p><p>'+('Visible material sentence. '*30)+'</p></div><table><tr><td>Heading</td><td>Amount</td></tr><tr><td>Revenue</td><td>—</td></tr></table></body></html>')
        first=prepare_document(self.path,80); second=prepare_document(self.path,80)
        self.assertEqual(first,second)
        self.assertEqual(PreparedDocument.from_json(first.to_json()),first)
        self.assertFalse(any('HIDDEN' in b.text or 'SECRET' in b.text for b in first.blocks))
        for b in first.blocks:
            if b.kind=='text':
                self.assertEqual(''.join(c.text for c in first.chunks if c.block_id==b.block_id),b.text)
                self.assertTrue(all(len(c.text)<=80 for c in first.chunks if c.block_id==b.block_id))
        table=next(b for b in first.blocks if b.kind=='table')
        self.assertEqual(first.resolve(f'{table.block_id}:row:1'),'Revenue | —')
        self.assertTrue(all(c.section=='Item 1' for c in first.chunks))
        with self.assertRaises(ValueError): first.resolve(f'{table.block_id}:row:-1')

    def test_chunk_identity_changes_for_new_filing(self):
        self.path.write_text('<p>First version.</p>'); a=prepare_document(self.path)
        self.path.write_text('<p>Second version.</p>'); b=prepare_document(self.path)
        self.assertNotEqual(a.chunks[0].chunk_id,b.chunks[0].chunk_id)

    def test_submission_selects_primary_document_and_preserves_encoding(self):
        self.path.write_bytes(b'<DOCUMENT>\n<TYPE>10-K\n<TEXT><p>Company\x92s material.</p></TEXT>\n</DOCUMENT>\n<DOCUMENT>\n<TYPE>EX-99\n<TEXT><p>Unrelated exhibit</p></TEXT></DOCUMENT>')
        p=prepare_document(self.path)
        self.assertIn('Company’s',p.blocks[0].text)
        self.assertFalse(any('Unrelated' in b.text for b in p.blocks))

    def test_ambiguous_selector_and_bad_chunk_size_fail(self):
        self.path.write_text('<p>Repeated phrase.</p><p>Repeated phrase.</p>')
        p=prepare_document(self.path)
        with self.assertRaises(ValueError): p.locate('Repeated')
        with self.assertRaises(ValueError): prepare_document(self.path,0)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        path=Path(self.tmp.name)/'source.html';path.write_text('<p>Synthetic evidence for a test graph.</p>')
        self.prepared=prepare_document(path)
        self.company=Company('0000000001','Synthetic Company','TEST','fixture')
        self.cid=self.company.company_id
        self.source=SourceDocument(self.cid,'10-K','2025-12-31',self.prepared.content_sha256,str(path),self.prepared.parsing_version)
        self.registry=Registry();self.registry.register_company(self.company);self.registry.register_document(self.source)
        b=self.prepared.blocks[0];locator=f'{b.block_id}:0:{len(b.text)}'
        cite=Citation(self.source.document_id,locator,EvidenceReference('fixture',b.text))
        self.prov=Provenance('confirmed','accepted',(cite,),reviewed_by='synthetic test fixture',reviewed_on='2026-01-01')
        self.entities=(Entity(self.cid,'company','Synthetic Company',self.prov,self.cid),
                       Entity('concept:material:feedstock','material','Feedstock',self.prov),
                       Entity(self.cid+':product:p','product','Product',self.prov,self.cid),
                       Entity(self.cid+':segment:other','segment','Other',self.prov,self.cid),
                       Entity(self.cid+':end_market:industrial','end_market','Industrial',self.prov,self.cid))
        e=self.entities
        self.claims=(GraphClaim('uses',e[1].entity_id,'used_in',e[2].entity_id,self.prov),
                     GraphClaim('hierarchy',e[2].entity_id,'belongs_to',e[3].entity_id,self.prov),
                     GraphClaim('market',e[3].entity_id,'reports_sales_to',e[4].entity_id,self.prov))
        self.snapshot=GraphSnapshot('test-v1',self.cid,(self.source.document_id,),self.entities,self.claims)
        self.docs={self.source.document_id:self.prepared}
        self.repo=GraphRepository(Path(self.tmp.name)/'graphs')

    def save(self,snapshot=None):return self.repo.save(snapshot or self.snapshot,self.registry,self.docs)

    def test_roundtrip_idempotence_and_immutability(self):
        folder=self.save();self.assertEqual(self.save(),folder)
        graph=self.repo.load(self.cid,'test-v1')
        self.assertEqual(graph.snapshot,self.snapshot)
        self.assertEqual(graph.evidence('uses')[0]['resolved_excerpt'],self.prepared.blocks[0].text)
        with self.assertRaises(FileExistsError): self.save(replace(self.snapshot,claims=self.claims[:1]))
        self.assertEqual(self.repo.list_snapshots(self.cid),('test-v1',))
        self.assertEqual(self.repo.list_snapshots('company:0000000002'),())

    def test_integrity_and_company_scope(self):
        folder=self.save();(folder/'snapshot.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'integrity'):self.repo.load(self.cid,'test-v1')
        with self.assertRaises(FileNotFoundError):self.repo.load('company:0000000002','test-v1')
        with self.assertRaises(ValueError):self.repo.load(self.cid,'../../other')

    def test_citation_mismatch_rejected_before_write(self):
        old=self.prov.citations[0]
        bad=replace(self.prov,citations=(replace(old,reference=EvidenceReference('fixture','Fabricated excerpt')),))
        snap=replace(self.snapshot,claims=(replace(self.claims[0],provenance=bad),))
        with self.assertRaisesRegex(ValueError,'excerpt'):self.save(snap)
        self.assertEqual(self.repo.list_snapshots(self.cid),())

    def test_direction_profiles_and_provenance(self):
        self.save();view=self.repo.load(self.cid,'test-v1');start=self.entities[1].entity_id
        paths=view.paths(start,target=self.entities[-1].entity_id)['paths']
        self.assertEqual(len(paths),1)
        self.assertEqual([c.claim_id for c in paths[0].claims],['uses','hierarchy','market'])
        self.assertTrue(all(c.provenance.citations for c in paths[0].claims))
        self.assertEqual(view.paths(self.entities[-1].entity_id)['paths'],())
        self.assertEqual(view.paths(start,profile='physical_supply')['paths'],())
        self.assertEqual(view.search('product')[0],self.entities[2])
        self.assertEqual(view.neighbors(self.entities[2].entity_id,direction='in'),self.claims[:1])

    def test_confirmed_filter_and_corporate_other_exclusion(self):
        assumed=Provenance('inferred','accepted',rationale='test assumption',reviewed_by='test',reviewed_on='2026-01-01')
        snap=replace(self.snapshot,claims=(replace(self.claims[0],provenance=assumed),*self.claims[1:]))
        view=GraphView(snap,self.registry,self.docs,[])
        self.assertEqual(view.paths(self.entities[1].entity_id)['paths'],())
        self.assertTrue(view.paths(self.entities[1].entity_id,confirmed_only=False)['paths'])
        entities=(*self.entities[:3],replace(self.entities[3],attributes=(('operational','false'),)),self.entities[4])
        view=GraphView(replace(self.snapshot,entities=entities),self.registry,self.docs,[])
        self.assertFalse(view.paths(self.entities[1].entity_id,target=self.entities[-1].entity_id)['paths'])

    def test_cycle_limits_and_bad_endpoints(self):
        cyclic=GraphClaim('cycle',self.entities[2].entity_id,'belongs_to',self.entities[2].entity_id,self.prov)
        view=GraphView(replace(self.snapshot,claims=(*self.claims,cyclic)),self.registry,self.docs,[])
        response=view.paths(self.entities[1].entity_id,limit=1)
        self.assertTrue(response['truncated']);self.assertEqual(len(response['paths']),1)
        with self.assertRaises(ValueError):view.paths(self.entities[1].entity_id,max_hops=99)
        with self.assertRaises(ValueError):view.paths(self.entities[1].entity_id,profile='anything')
        with self.assertRaises(KeyError):view.observations(subject='unregistered')


class LocalMtrnIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog=json.loads(CATALOG.read_text())
        path=ROOT/cls.catalog['source']['local_path']
        if not path.exists():raise unittest.SkipTest('local SEC filing not available; offline integration skipped')
        cls.prepared=prepare_document(path)
        cls.registry,cls.snapshot,cls.reconciliation=build_catalog(cls.prepared,cls.catalog)
        cls.view=GraphView(cls.snapshot,cls.registry,{cls.snapshot.document_ids[0]:cls.prepared},cls.catalog['coverage'])

    def test_all_coverage_disposed_and_all_citations_resolve(self):
        self.assertEqual(len(self.catalog['coverage']),144)
        self.assertEqual(len({r['id'] for r in self.catalog['coverage']}),144)
        self.assertFalse(any(r['disposition']=='deferred' for r in self.catalog['coverage']))
        GraphRepository._validate(self.snapshot,self.registry,{self.snapshot.document_ids[0]:self.prepared})
        self.assertTrue(self.view.gaps())

    def test_revenue_reconciliation_periods_and_zero_are_preserved(self):
        self.assertEqual({r['year']:r['company_total'] for r in self.reconciliation},
                         {'2025':'1786550','2024':'1684739','2023':'1665187'})
        company=self.snapshot.company_id
        observations=self.view.observations(subject=company,metric='net_sales',period_end='2025-12-31')
        total=next(o for o in observations if o.related_entity_id is None)
        self.assertEqual(total.unit,'currency_thousands');self.assertEqual(total.currency,'USD')
        zeros=[o for o in self.snapshot.observations if o.metric=='net_sales' and o.source_value=='—']
        self.assertTrue(zeros);self.assertTrue(all(o.value=='0' for o in zeros))
        other_shares=self.view.observations(subject=company+':segment:other',metric='share_of_segment_net_sales')
        self.assertTrue(all(o.availability=='not_applicable' and o.value is None for o in other_shares))

    def test_copper_paths_and_facility_dependencies(self):
        company=self.snapshot.company_id
        paths=self.view.paths('concept:material:copper',target=company+':segment:performance-materials')['paths']
        self.assertEqual(len(paths),3)
        self.assertTrue(all(len(p.claims)==3 for p in paths))
        elmore=next(e for e in self.view.search('Elmore',kind='facility'))
        paths=self.view.paths(elmore.entity_id,profile='physical_supply')['paths']
        targets={self.view.entity(p.entity_ids[-1]).label for p in paths}
        self.assertEqual(targets,{'Reading, Pennsylvania','Tucson, Arizona'})
        self.assertFalse(self.view.paths('concept:material:copper',profile='physical_supply')['paths'])

    def test_disclosed_sensitivities_stay_company_level_and_annual(self):
        values=[o for o in self.snapshot.observations if o.observation_id.startswith('sensitivity:')]
        self.assertEqual({o.value for o in values},{'0.8','4.5'})
        self.assertTrue(all(o.horizon=='annual' and o.subject==self.snapshot.company_id for o in values))
        self.assertTrue(all(o.as_of=='2025-12-31' and o.assumptions for o in values))
        self.assertEqual({o.related_entity_id for o in values},{'concept:material:copper','concept:material_cohort:precious-metals'})

    def test_all_facilities_and_ambiguous_identities_stay_separate(self):
        facilities=self.view.search('',kind='facility')
        self.assertEqual(len(facilities),33)
        shanghai=[e for e in facilities if e.label=='Shanghai, China']
        self.assertEqual(len(shanghai),2);self.assertNotEqual(shanghai[0].entity_id,shanghai[1].entity_id)
        self.assertEqual(len(self.view.search('Spor Mountain',kind='operation')),2)
        self.assertFalse(any(c.relation=='same_as' for c in self.snapshot.claims))

    def test_changed_source_and_revenue_rejected(self):
        with self.assertRaises(ValueError):build_catalog(replace(self.prepared,content_sha256='0'*64),self.catalog)
        block=self.prepared.block('b00595');rows=list(block.rows);row=list(rows[3]);row[row.index('10,964')]='99,999';rows[3]=tuple(row)
        tampered=replace(self.prepared,blocks=tuple(replace(b,rows=tuple(rows)) if b.block_id==block.block_id else b for b in self.prepared.blocks))
        with self.assertRaisesRegex(ValueError,'reconcile'):build_catalog(tampered,self.catalog)

    def test_rebuild_does_not_depend_on_callers_decimal_precision(self):
        with localcontext() as context:
            context.prec = 6
            _, rebuilt, _ = build_catalog(self.prepared, self.catalog)
        self.assertEqual(rebuilt.to_json(), self.snapshot.to_json())


class SyntheticCurationTests(unittest.TestCase):
    def test_non_mtrn_company_one_segment_and_euro_units(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'sample.html'
            path.write_text('<p>Synthetic Company makes feedstock.</p><table><tr><td>(Millions)</td><td>Only Segment</td><td>Total</td></tr><tr><td>2025</td></tr><tr><td>Energy</td><td>1.5</td><td>1.5</td></tr><tr><td>Total</td><td>1.5</td><td>1.5</td></tr></table>')
            prepared=prepare_document(path);b=prepared.blocks[0];ev=[f'{b.block_id}:0:{len(b.text)}']
            catalog={'catalog_version':1,'snapshot_id':'synthetic-v1','reviewed_by':'test fixture','reviewed_on':'2026-01-01',
                'company':{'cik':'0000000002','name':'Synthetic Company','ticker':'SYN','identity_source':'synthetic'},
                'source':{'content_sha256':prepared.content_sha256,'parsing_version':prepared.parsing_version,'expected_metadata':{},'form':'10-K','period_end':'2025-12-31','local_path':str(path),'accession':None,'filing_date':None,'source_url':'https://example.com/synthetic','title':'Synthetic fixture'},
                'entities':[{'ref':'company','kind':'company','label':'Synthetic Company','evidence':ev,'origin':'synthetic','rationale':'test fixture'},
                            {'ref':'segment:only','kind':'segment','label':'Only Segment','evidence':ev,'origin':'synthetic','rationale':'test fixture'},
                            {'ref':'end_market:energy','kind':'end_market','label':'Energy','evidence':ev,'origin':'synthetic','rationale':'test fixture'}],
                'claims':[],'coverage':[],
                'revenue_tables':[{'block_id':prepared.blocks[1].block_id,'unit_label':'(Millions)','unit':'currency_millions','currency':'EUR','origin':'synthetic','rationale':'test fixture',
                                   'segment_labels':['Only Segment'],'segments':['segment:only'],'markets':{'Energy':'end_market:energy'},'market_order':['Energy'],'years':['2025'],'active_year':'2025','dash_is_zero':False}]}
            registry,snapshot,checks=build_catalog(prepared,catalog)
            self.assertEqual(snapshot.company_id,'company:0000000002')
            totals=[o for o in snapshot.observations if o.subject==snapshot.company_id and o.related_entity_id is None]
            self.assertEqual((totals[0].value,totals[0].unit,totals[0].currency),('1.5','currency_millions','EUR'))
            self.assertTrue(all(o.provenance.origin == 'synthetic' for o in snapshot.observations))
            self.assertTrue(checks[0]['segment_totals_match'])
            catalog['source']['expected_metadata']={'TradingSymbol':['WRONG']}
            with self.assertRaisesRegex(ValueError,'identity mismatch'):build_catalog(prepared,catalog)

    def test_decimal_cells_do_not_guess_missing_values(self):
        self.assertEqual(decimal_cell('(1,250.5)'),Decimal('-1250.5'))
        self.assertEqual(decimal_cell('—',True),Decimal(0))
        for value in ('—','1,2','NaN','(100','100)','(-100)'):
            with self.subTest(value=value),self.assertRaises(ValueError):decimal_cell(value)


if __name__=='__main__':unittest.main()
