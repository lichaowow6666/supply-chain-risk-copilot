"""Synthetic disclosures, never company data."""
from types import SimpleNamespace
import unittest
from pe_copilot.filings.exposures import validate_audit, audit_blocks


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.blocks=[SimpleNamespace(block_id='b1',section='Item 1',text='Platinum is used in manufacturing equipment. These metals are repeatedly reclaimed and reused.'),
                     SimpleNamespace(block_id='b2',section='Item 1A',text='Precious metals supply interruptions may delay production and increase costs.'),
                     SimpleNamespace(block_id='b3',section='Item 8',text='Leased precious metals are subject to fair value hedges. Purchase coverage is not quantified.')]
        self.filing={'accessionNumber':'synthetic','form':'10-K','reportDate':'2025-12-31','filingDate':'2026-02-01','url':'https://example.invalid/fixture'}

    def claim(self,i,summary):
        return {'summary':summary,'citations':[{'block_id':self.blocks[i].block_id,'quote':self.blocks[i].text}]}

    def item(self):
        return {'material':'Platinum','level':'High','role':self.claim(0,'Used in equipment'),
                'risk':self.claim(1,'Group supply risk; no metal-specific allocation'),
                'hedge':self.claim(2,'Leased pool hedge; no operating coverage percentage')}

    def test_separate_field_citations_preserve_full_context(self):
        rows=validate_audit({'materials':[self.item()]},self.blocks,self.filing)
        self.assertEqual(len(rows),1)
        self.assertEqual(len(rows[0]['raw_chunks']),3)
        self.assertEqual(rows[0]['raw_chunks'][0]['text'],self.blocks[0].text)
        self.assertEqual(rows[0]['evidence_fields']['hedge']['block_ids'],['b3'])
        self.assertTrue(rows[0]['evidence_gaps'])

    def test_generic_or_hallucinated_materials_rejected(self):
        for name in ['Precious Metals','Energy','Raw Materials','Gold']:
            item=self.item();item['material']=name
            self.assertEqual(validate_audit({'materials':[item]},self.blocks,self.filing),[])

    def test_missing_or_fabricated_quotes_cannot_support_fields(self):
        item=self.item(); item['risk']['citations'][0]['quote']='Invented material shortage and risk'
        item['hedge']['citations'][0]['block_id']='does-not-exist'
        row=validate_audit({'materials':[item]},self.blocks,self.filing)[0]
        self.assertEqual(row['inherent_exposure_level'],'Unknown')
        self.assertIn('does not mean unhedged',row['hedge_level'])
        self.assertEqual(len(row['raw_chunks']),1)

    def test_adjacent_context_retained(self):
        blocks=audit_blocks(SimpleNamespace(blocks=self.blocks))
        self.assertEqual(len(blocks),3)
