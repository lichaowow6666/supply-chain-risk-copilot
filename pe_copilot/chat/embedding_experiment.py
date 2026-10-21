"""One fixed synthetic company-disjoint BGE-M3 dense fine-tuning experiment."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import random
import sqlite3
import time
from uuid import uuid4

import numpy as np

from pe_copilot.filings.sec import atomic_json

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'evaluations/embedding_v1/dataset.json'
CONFIG=ROOT/'evaluations/embedding_v1/training_config.json'


def dataset_and_config():
    data=json.loads(DATA.read_text());config=json.loads(CONFIG.read_text())
    groups=defaultdict(set);ids=set();texts=set()
    for row in data['records']:
        if row['id'] in ids or row['text'] in texts:raise ValueError('Duplicate dataset row')
        ids.add(row['id']);texts.add(row['text']);groups[row['split']].add(row['company'])
    if any(groups[a]&groups[b] for a,b in [('train','development'),('train','audit'),('development','audit')]):
        raise ValueError('Company leakage across splits')
    return data,config


def rank_metrics(data, model, label, splits=('development','audit')):
    """Compare BM25/dense/RRF with identical company/report scopes and candidate sets."""
    rows=data['records'];vectors=model.encode([r['text'] for r in rows],normalize_embeddings=True,batch_size=8,show_progress_bar=False)
    cases=[(r,q) for r in rows if r['split'] in splits for q in r['queries']]
    queries=model.encode([q['text'] for r,q in cases],normalize_embeddings=True,batch_size=8,show_progress_bar=False)
    db=sqlite3.connect(':memory:');db.execute('CREATE VIRTUAL TABLE docs USING fts5(id UNINDEXED,report UNINDEXED,text)')
    db.executemany('INSERT INTO docs VALUES (?,?,?)',[(r['id'],r['report'],r['text']) for r in rows])
    results=[]
    import re
    for n,(gold,q) in enumerate(cases):
        candidates=[i for i,r in enumerate(rows) if r['report']==gold['report']]
        dense=[rows[i]['id'] for i in sorted(candidates,key=lambda i:-float(vectors[i]@queries[n]))]
        terms=re.findall(r'[A-Za-z0-9]+',q['text']);expression=' OR '.join('"'+t+'"' for t in terms)
        lexical=[r[0] for r in db.execute('SELECT id FROM docs WHERE docs MATCH ? AND report=? ORDER BY bm25(docs),id',(expression,gold['report']))] if terms else []
        scores=defaultdict(float)
        for ranking in (lexical,dense):
            for rank,id in enumerate(ranking,1):scores[id]+=1/(60+rank)
        hybrid=sorted(scores,key=lambda id:(-scores[id],id))
        for backend,ranking in [('BM25',lexical),('dense',dense),('hybrid',hybrid)]:
            rank=ranking.index(gold['id'])+1 if gold['id'] in ranking else None
            results.append({'model':label,'backend':backend,'split':gold['split'],'case_id':gold['id']+':'+q['language'],
                'language':q['language'],'recall_at_1':int(rank==1),'recall_at_3':int(rank is not None and rank<=3),
                'reciprocal_rank':1/rank if rank else 0,'rank':rank,'candidate_count':len(candidates)})
    db.close()
    return results


def summarize(rows):
    groups=defaultdict(list)
    for row in rows:groups[(row['model'],row['backend'],row['split'],row['language'])].append(row)
    return [{'Model':m,'Retrieval':b,'Split':s,'Language':lang,'Cases':len(values),
             'Recall@1':sum(r['recall_at_1'] for r in values)/len(values),
             'Recall@3':sum(r['recall_at_3'] for r in values)/len(values),
             'MRR':sum(r['reciprocal_rank'] for r in values)/len(values)}
            for (m,b,s,lang),values in sorted(groups.items())]


def run(output='artifacts/evaluation/embedding_comparison.json'):
    import torch
    from sentence_transformers import SentenceTransformer, losses
    data,config=dataset_and_config();output=Path(output)
    if output.exists() and json.loads(output.read_text()).get('status')=='completed':
        print('Preserving completed experiment. Choose a new --output path for a separate run.',flush=True)
        return json.loads(output.read_text())
    runroot=output.parent/('embedding_run_'+uuid4().hex);runroot.mkdir(parents=True,exist_ok=True)
    dataset_hash=sha256(DATA.read_bytes()).hexdigest();config_hash=sha256(CONFIG.read_bytes()).hexdigest()
    record={'status':'running','dataset_version':data['version'],'dataset_sha256':dataset_hash,'configuration_sha256':config_hash,
            'configuration':config,'synthetic':True,'created_at':datetime.now(timezone.utc).isoformat(),
            'warnings':['Small authored synthetic dataset; company-disjoint audit uses shared templates and is not a real-company generalization benchmark.',
                        'Audit cases are not used for optimization or checkpoint selection. This fixed single-run experiment must not be retuned on its audit results.',
                        'Only the dense embedding pathway is fine-tuned; no claim of training BGE-M3 sparse or ColBERT heads.'],
            'versions':{p:version(p) for p in ('torch','sentence-transformers','transformers')},'results':[]}
    atomic_json(output,record)
    started=time.monotonic()
    try:
        model_config=json.loads(Path('artifacts/chat_models/base_model.json').read_text())
        if model_config['revision']!=config['base_revision']:raise ValueError('Base model revision differs from frozen configuration')
        random.seed(config['seed']);np.random.seed(config['seed']);torch.manual_seed(config['seed']);torch.set_num_threads(4)
        model=SentenceTransformer(model_config['path'],device='cpu',local_files_only=True)
        model.max_seq_length=config['max_length']
        # Evaluate the baseline once; these audit outputs never influence training.
        print('Evaluating fixed base embedding...',flush=True)
        record['results']=rank_metrics(data,model,'base BGE-M3');atomic_json(output,record)
        for parameter in model.parameters():parameter.requires_grad=False
        transformer=model[0].auto_model
        last=transformer.encoder.layer[-1].attention.self
        for module in (last.query,last.value):
            for parameter in module.parameters():parameter.requires_grad=True
        parameters=[p for p in model.parameters() if p.requires_grad]
        record['trainable_parameters']=sum(p.numel() for p in parameters)
        record['total_parameters']=sum(p.numel() for p in model.parameters())
        optimizer=torch.optim.AdamW(parameters,lr=config['learning_rate'],weight_decay=config['weight_decay'])
        loss_fn=losses.MultipleNegativesRankingLoss(model,scale=config['scale'])
        training=[r for r in data['records'] if r['split']=='train'];rng=random.Random(config['seed']);history=[]
        for index in range(config['steps']):
            # Unique paragraph IDs in each batch avoid bilingual positives becoming false negatives.
            batch=rng.sample(training,config['batch_size'])
            query_text=[rng.choice(r['queries'])['text'] for r in batch]
            model.train();optimizer.zero_grad()
            features=[model.tokenize(query_text),model.tokenize([r['text'] for r in batch])]
            loss=loss_fn(features,None);loss.backward();torch.nn.utils.clip_grad_norm_(parameters,1.0);optimizer.step()
            history.append({'step':index+1,'loss':float(loss.detach()),'positive_ids':[r['id'] for r in batch]})
            print(f"Step {index+1}/{config['steps']}: loss {history[-1]['loss']:.5f}",flush=True)
            atomic_json(runroot/'training_history.json',history)
        # Preserve only the changed weights, plus the pinned base identity, rather than duplicate 2GB.
        changes={name:p.detach().cpu() for name,p in model.named_parameters() if p.requires_grad}
        weights=runroot/'dense_attention_delta.pt';torch.save(changes,weights)
        record['weights_sha256']=sha256(weights.read_bytes()).hexdigest();record['weights_path']=str(weights)
        atomic_json(runroot/'encoder_config.json',{**model_config,'weights_path':str(weights.resolve()),
                    'weights_sha256':record['weights_sha256']})
        record['training_history']=history
        model.eval();print('Evaluating fixed final checkpoint...',flush=True)
        record['results']+=rank_metrics(data,model,'fine-tuned BGE-M3')
        record['summary']=summarize(record['results']);record['status']='completed'
        atomic_json(runroot/'dataset_snapshot.json',data);atomic_json(runroot/'configuration.json',config)
    except Exception as error:
        record['status']='failed';record['error_type']=type(error).__name__
        # Safe local failure metadata; never persist provider tokens/URLs embedded in exception messages.
        print('Experiment failed:',type(error).__name__,flush=True)
    record['elapsed_seconds']=time.monotonic()-started;atomic_json(output,record)
    return record


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',default='artifacts/evaluation/embedding_comparison.json')
    args=parser.parse_args();result=run(args.output)
    print(json.dumps({k:result[k] for k in ('status','elapsed_seconds','error_type') if k in result}))
    raise SystemExit(0 if result['status']=='completed' else 1)
