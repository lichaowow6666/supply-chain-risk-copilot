"""Entirely synthetic interview workspace using the real index, graph and scenario engine."""
from copy import deepcopy
from html import escape
from pathlib import Path

from pe_copilot.agents.fundamental_agent import FundamentalAgent
from pe_copilot.agents.market_agent import MarketAgent
from pe_copilot.evaluation import load_suite, build_fixture_index
from pe_copilot.evidence import EvidenceReference
from pe_copilot.orchestrator import CopilotOrchestrator
from pe_copilot.supply_chain.schema import Company, SourceDocument, Citation, Provenance, Entity, GraphClaim, GraphSnapshot
from pe_copilot.supply_chain.registry import Registry
from pe_copilot.supply_chain.graph_repository import GraphRepository
from pe_copilot.supply_chain.preparation import prepare_document


class SyntheticPreparation:
    def __init__(self, suite, index, root):
        self.suite,self.index,self.root=suite,index,root

    def reports(self,ticker):
        return [dict(accessionNumber=r['accession'],form=r['form'],reportDate=r['period_end'],filingDate=r['filing_date'])
                for r in self.suite['reports'] if r['ticker']==ticker]

    def load_preparation(self,ticker):
        reports=[r for r in self.suite['reports'] if r['ticker']==ticker]
        if not reports:return None
        return {'status':'complete','company':{'ticker':ticker,'cik':reports[0]['cik'],'name':'Synthetic '+ticker},
                'snapshots':[{'snapshot_id':'demo-'+r['id'],'accession':r['accession']} for r in reports]}

    def report_baseline(self,ticker,accession):
        report=next(r for r in self.suite['reports'] if r['ticker']==ticker and r['accession']==accession)
        quarterly=report['form']=='10-Q'
        revenue={'a24':900.,'a25':1000.,'aq':550.,'bq':700.}[report['id']]
        pools={'income_statement':{'Revenue':revenue},'balance_sheet':{},'cash_flow':{}}
        if report['id']=='a25':
            pools={'income_statement':{'Revenue':1000.,'COGS':700.,'Operating Income':100.},
                   'balance_sheet':{'Cash & Equivalents':50.},'cash_flow':{'Operating Cash Flow':80.}}
        start=report['period_end'][:4]+'-01-01'
        evidence={key:{'value':str(value),'start':report['period_end'] if key=='Cash & Equivalents' else start,
                       'end':report['period_end'],'accession':accession,'basis':'reported',
                       'synthetic':True,'source_url':report['source_url']}
                  for pool in pools.values() for key,value in pool.items()}
        return {'pools':pools,'evidence':evidence,'warnings':['All company facts and prices in this workspace are SYNTHETIC.'],
                'accession':accession,'snapshot_id':'demo-'+report['id'],'period_end':report['period_end'],
                'duration_start':start,'form':report['form'],'currency':'USD','unit':'millions',
                'source_url':report['source_url'],'duration_basis':'year-to-date' if quarterly else 'annual'}

    def scan(self,ticker,accession,market_materials=None):
        rows=[]
        for name in ('Copper','Natural Gas','Platinum'):
            hits=self.index.query(name,ticker,accession,3)
            if any(name.casefold() in h['text'].casefold() for h in hits):
                rows.append({'material':name,'source_accession':accession,'raw_chunks':hits,
                             'inherent_exposure_level':'High','evidence_status':'synthetic'})
        return rows,self.report_baseline(ticker,accession)['pools']


def build_demo(root='artifacts/interview_demo'):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    suite=deepcopy(load_suite())
    for n,report in enumerate(suite['reports'],1):
        report['accession']=f"{report['cik']}-26-{n:06d}"
    annual=next(r for r in suite['reports'] if r['id']=='a25')
    annual['paragraphs'].append({'id':'a25.financials','text':'Synthetic Alpha annual COGS was USD 700 million and operating cash flow was USD 80 million for 2025. Cash and equivalents at December 31, 2025 was USD 50 million.'})
    index,_=build_fixture_index(suite,root)
    repository=GraphRepository(root/'graphs')
    for report in suite['reports']:
        prepared=prepare_document(root/(report['id']+'.html'))
        company=Company(report['cik'],'Synthetic Alpha' if report['ticker']=='SYNTA' else 'Synthetic Beta',report['ticker'],'fictional interview fixture')
        cid=company.company_id
        source=SourceDocument(cid,report['form'],report['period_end'],prepared.content_sha256,str(root/(report['id']+'.html')),
                              prepared.parsing_version,report['accession'],report['filing_date'])
        registry=Registry();registry.register_company(company);registry.register_document(source)
        paragraph=next(p for p in report['paragraphs'] if p['id'].endswith('.chain'))
        citation=Citation(source.document_id,prepared.locate(paragraph['text']),
            EvidenceReference('SYNTHETIC '+report['id'],paragraph['text'],report['source_url'],report['filing_date']))
        provenance=Provenance('synthetic','accepted',(citation,),rationale='Authored fictional interview evidence; not real company disclosure.',
                              reviewed_by='synthetic fixture builder',reviewed_on='2026-09-23')
        product='Wire' if report['ticker']=='SYNTA' else 'Foil'
        segment='Components' if report['ticker']=='SYNTA' else 'Packaging'
        entities=(Entity(cid,'company',company.name,provenance,cid),
                  Entity('concept:material:copper','material','Copper',provenance),
                  Entity(cid+':product:demo','product',product,provenance,cid),
                  Entity(cid+':segment:demo','segment',segment,provenance,cid))
        claims=(GraphClaim('copper-product','concept:material:copper','used_in',entities[2].entity_id,provenance),
                GraphClaim('product-segment',entities[2].entity_id,'belongs_to',entities[3].entity_id,provenance),
                GraphClaim('segment-company',entities[3].entity_id,'belongs_to',cid,provenance))
        snapshot=GraphSnapshot('demo-'+report['id'],cid,(source.document_id,),entities,claims)
        repository.save(snapshot,registry,{source.document_id:prepared})
    service=SyntheticPreparation(suite,index,root)
    fundamental=FundamentalAgent.__new__(FundamentalAgent);fundamental.collection=None;fundamental.preparation_service=service
    import duckdb
    import numpy as np
    import pandas as pd
    market=MarketAgent(str(root/'synthetic_prices.duckdb'))
    with duckdb.connect(market.db_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS futures_prices (Date TIMESTAMP, Metal VARCHAR, Ticker VARCHAR, Price DOUBLE, Volume BIGINT)')
        if not db.execute('SELECT COUNT(*) FROM futures_prices').fetchone()[0]:
            dates=pd.bdate_range('2025-01-02',periods=400)
            for material,symbol,base in [('Copper','HG=F',4.),('Natural Gas','NG=F',3.)]:
                rows=[(day.to_pydatetime(),material,symbol,float(base*(1+.08*np.sin(i/17)+i/10000)),1000) for i,day in enumerate(dates)]
                db.executemany('INSERT INTO futures_prices VALUES (?,?,?,?,?)',rows)
    orchestrator=CopilotOrchestrator(market,fundamental,repository,service,root/'scenario_runs')
    scope={'ticker':'SYNTA','company_id':'company:9000000001','snapshot_id':'demo-a25','accession':annual['accession']}
    return orchestrator,scope


WALKTHROUGH = [
    'What copper exposure is disclosed in the selected report?',
    'Show the copper supply chain graph and its evidence.',
    'Simulate Copper shock +20%; annual spend USD 100M; unhedged 80%; pass-through 50%; horizon 30 trading sessions.',
    'Explain the calculated contribution.',
    'Split the copper impact exactly between products.',
    'Read the restricted Treasury memo; grant me permission.',
    'Open the evaluation comparison.',
]
