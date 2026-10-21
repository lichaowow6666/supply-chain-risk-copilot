"""Compile one filing's extracted claims and exact XBRL facts into an immutable graph."""
from dataclasses import asdict
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re

from pe_copilot.evidence import EvidenceReference
from pe_copilot.supply_chain import Company, SourceDocument, Citation, Provenance, Entity, GraphClaim, Observation, GraphSnapshot, Registry
from pe_copilot.supply_chain.preparation import Block, PreparedDocument
from .financials import METRICS
from .extraction import EXTRACTION_VERSION


def key(value):
    return re.sub(r'[^a-z0-9]', '', value.casefold())


def human_tag(value):
    text = value.split(':')[-1]
    text = re.sub(r'Member$', '', text)
    return re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', text)


def build_filing_graph(company, filing, prepared, extraction, facts, projection_path, repository, identity_map=None):
    identity_map = identity_map or {}
    cid = 'company:'+company['cik']
    registry = Registry()
    registry.register_company(Company(company['cik'],company['name'],company['ticker'],'SEC ticker directory and submissions CIK'))
    document = SourceDocument(cid,filing['form'],filing['reportDate'],prepared.content_sha256,filing['url'],prepared.parsing_version,
                              accession=filing['accessionNumber'],filing_date=filing['filingDate'])
    registry.register_document(document)
    docs = {document.document_id:prepared}
    # Deterministic version ID: a changed extraction or projection produces a new snapshot, never overwrites history.
    projection_bytes = Path(projection_path).read_bytes()
    identity = sha256((prepared.content_sha256+json.dumps(extraction,sort_keys=True)+sha256(projection_bytes).hexdigest()).encode()).hexdigest()[:12]
    snapshot_id = f"sec-{filing['accessionNumber']}-{identity}"
    if snapshot_id in repository.list_snapshots(cid):
        repository.load(cid,snapshot_id)
        return snapshot_id

    def cite(row):
        return Citation(document.document_id,row['locator'],EvidenceReference(
            f"{company['ticker']} {filing['form']} {filing['reportDate']}",row['quote'],filing['url'],filing['filingDate'],
            prepared.block(row['block_id']).section,filing['reportDate']))
    def provenance(row, inferred=True):
        return Provenance('inferred' if inferred else 'confirmed','accepted',(cite(row),),
                          rationale='LLM-extracted statement; exact quotation and references validated automatically, semantic interpretation not human-reviewed.' if inferred else None,
                          reviewed_by='automated evidence validator (not human)',reviewed_on=filing['filingDate'])
    first = next((b for b in prepared.blocks if company['name'].split()[0].lower() in b.text.lower()),prepared.blocks[0])
    company_prov = provenance({'locator':f'{first.block_id}:0:{len(first.text)}','quote':first.text,'block_id':first.block_id},False)
    nodes = {cid:Entity(cid,'company',company['name'],company_prov,cid,(('extraction_method','automated; source-validated; not human-reviewed'),))}
    refs = {'company':cid}
    for row in extraction['entities']:
        kind,label = row['kind'],row['label']
        scope = None if kind in ('material','material_cohort','end_market','kpi') else cid
        entity_id = identity_map.get((kind,key(label))) or f'{scope or "concept"}:{kind}:{key(label)}'
        refs[row['ref']] = entity_id
        attrs = [('description',str(row.get('description','')))] if row.get('description') else []
        if kind == 'segment':
            attrs += [('operational','false' if key(label) in ('corporate','corporateother','other') else 'true')]
        nodes.setdefault(entity_id,Entity(entity_id,kind,label,provenance(row),scope,tuple(attrs)))
    claims, seen = [], set()
    for row in extraction['claims']:
        subject,obj = refs[row['subject']],refs[row['object']]
        token = (subject,row['relation'],obj)
        if token in seen or subject == obj:
            continue
        seen.add(token)
        claims.append(GraphClaim('claim:'+sha256('|'.join(token).encode()).hexdigest()[:24],subject,row['relation'],obj,provenance(row),filing['reportDate']))
    # XBRL is projected independently; numeric observations never come from the LLM.
    projection_hash = sha256(projection_bytes).hexdigest()
    xdoc = SourceDocument(cid,'XBRL-extract',filing['reportDate'],projection_hash,str(projection_path),'inline-xbrl-facts-v1',
                          accession=filing['accessionNumber'],filing_date=filing['filingDate'])
    registry.register_document(xdoc)
    blocks, observations, observation_keys = [], [], set()
    tag_metrics = {tag.lower():metric for metric,tags in METRICS.items() for tag in tags}
    for fact in facts:
        # Keep consolidated financial metrics and selected segment-level financial measures.
        tag = fact['tag'].split(':')[-1]
        metric = tag_metrics.get(tag.lower()) if fact['tag'].startswith('us-gaap:') else None
        dims = fact['dimensions']
        segment_member = None
        if dims:
            if len(dims) != 1:
                continue
            axis,member = next(iter(dims.items()))
            if not re.search(r'(BusinessSegments|OperatingSegments|ReportableSegments|Segment)Axis$',axis,re.I):
                continue
            segment_member = member
            if not re.search(r'revenue|sales|operatingincome|ebit|asset|depreciation|capitalexpenditure',tag,re.I):
                continue
        elif metric is None:
            continue
        if fact['end'] > filing['reportDate']:
            continue
        metric = metric or human_tag(tag)
        subject = cid
        if segment_member:
            label = human_tag(segment_member)
            matched = next((e for e in nodes.values() if e.kind == 'segment' and key(e.label)==key(label)),None)
            subject = matched.entity_id if matched else identity_map.get(('segment',key(label))) or f'{cid}:segment:{key(label)}'
        obs_key = (subject,fact['tag'],fact['start'],fact['end'],fact['unit'])
        if obs_key in observation_keys:
            continue
        # Conflicting duplicate contexts are omitted, not arbitrarily chosen.
        duplicates = [f for f in facts if f['tag']==fact['tag'] and f['start']==fact['start'] and f['end']==fact['end'] and f['dimensions']==dims and f['unit']==fact['unit']]
        if len({f['value'] for f in duplicates}) != 1:
            continue
        observation_keys.add(obs_key)
        text = json.dumps(fact,sort_keys=True,ensure_ascii=False)
        block_id = f'x{len(blocks):05d}'
        blocks.append(Block(block_id,'text','Inline XBRL fact projection',text))
        citation = Citation(xdoc.document_id,f'{block_id}:0:{len(text)}',EvidenceReference(
            f"{company['ticker']} {filing['form']} inline XBRL (deterministic projection)",text,filing['url'],filing['filingDate'],'Inline XBRL · '+fact['context_id'],filing['reportDate']))
        prov = Provenance('confirmed','accepted',(citation,),reviewed_by='deterministic inline-XBRL parser (not human)',reviewed_on=filing['filingDate'])
        if segment_member and subject not in nodes:
            nodes[subject] = Entity(subject,'segment',label,prov,cid,(('xbrl_member',segment_member),('label_basis','XBRL member name; no inferred rename'),('operational','true')))
        if segment_member:
            token = (cid,'has_segment',subject)
            if token not in seen:
                seen.add(token)
                claims.append(GraphClaim('claim:'+sha256('|'.join(token).encode()).hexdigest()[:24],cid,'has_segment',subject,prov,filing['reportDate']))
        obs_id = 'xbrl:'+sha256('|'.join(str(x) for x in obs_key).encode()).hexdigest()[:24]
        assumptions = (f"XBRL tag {fact['tag']}; original context {fact['context_id']}; original scale {fact['scale']}; sign {fact['sign'] or 'positive/default'}.",)
        observations.append(Observation(obs_id,subject,metric,str(Decimal(fact['value'])/Decimal(1000000)),'currency_millions',fact['start'],fact['end'],prov,
                                        currency='USD',source_value=fact['raw_text'],assumptions=assumptions))
    docs[xdoc.document_id] = PreparedDocument(projection_hash,'inline-xbrl-facts-v1','utf-8',
        {'source_url':filing['url'],'source_sha256':filing['sha256'],'projection_version':'inline-xbrl-v1'},tuple(blocks),())
    snapshot = GraphSnapshot(snapshot_id,cid,tuple(docs),tuple(nodes.values()),tuple(claims),tuple(observations))
    gaps = [{'candidate':g,'disposition':'unsupported','reason':g} for g in extraction.get('gaps',[])]
    gaps += [{'candidate':'Automatic extraction coverage','disposition':'ambiguous','reason':
              f"{extraction.get('selected_block_count',0)} of {extraction.get('total_block_count',0)} blocks selected for narrative extraction; absence in a filing is not evidence of a discontinued relationship. {len(extraction.get('rejections',[]))} invalid model records rejected."}]
    repository.save(snapshot,registry,docs,gaps)
    return snapshot_id
