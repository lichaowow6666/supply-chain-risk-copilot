"""Reproducibly author synthetic company-disjoint development and audit cases."""
import json,random
from pathlib import Path
rng=random.Random(20260923)
records=[]
for split,names in [('train',['Alder','Birch','Cedar','Elm']),('development',['Maple','Pine']),('audit',['Rowan','Willow'])]:
 for name in names:
  company='Synthetic '+name
  for material,zh in [('Copper','铜'),('Natural gas','天然气'),('Silver','白银')]:
   spend=rng.randint(10,150);unhedged=rng.choice([20,40,60,80]);passthrough=rng.choice([25,50,75])
   entries=[('spend',f'{company} annual {material.lower()} procurement spend was USD {spend} million for fiscal 2025.',
             f'How much did {company} spend buying {material.lower()} during the year?',f'{company}在全年采购{zh}花费了多少钱？'),
            ('hedge',f'{company} disclosed that {unhedged} percent of its {material.lower()} purchases were unhedged at December 31, 2025.',
             f'What share of {company} {material.lower()} buying remains exposed without hedges?',f'{company}的{zh}采购有多少比例没有套期保值？'),
            ('pass',f'{company} passes {passthrough} percent of {material.lower()} cost changes to customers through contractual price adjustments.',
             f'How much of a {material.lower()} cost change can {company} recover from customers?',f'{company}能向客户转嫁多少{zh}成本变动？')]
   for kind,text,en,cn in entries:
    records.append({'id':name.lower()+'-'+material.lower().replace(' ','-')+'-'+kind,
                    'company':company,'report':name.lower()+'-2025','split':split,'text':text,
                    'queries':[{'language':'en','text':en},{'language':'zh','text':cn}]})
root=Path('evaluations/embedding_v1');root.mkdir(parents=True,exist_ok=True)
(root/'dataset.json').write_text(json.dumps({'version':'synthetic-company-holdout-v1','seed':20260923,
 'synthetic':True,'review_status':'authored_not_independently_reviewed',
 'split_policy':'company-disjoint; bilingual paraphrases grouped with their source; templates overlap across splits',
 'records':records},ensure_ascii=False,indent=2)+'\n')
(root/'training_config.json').write_text(json.dumps({'version':1,'model_id':'BAAI/bge-m3',
 'base_revision':'5617a9f61b028005a4858fdac845db406aefb181','seed':20260923,
 'max_length':128,'batch_size':4,'steps':24,'learning_rate':0.00002,'weight_decay':0.01,
 'loss':'MultipleNegativesRankingLoss','scale':20,'trainable':'last encoder block attention query and value weights/biases only',
 'device':'cpu','selection':'fixed configuration; single final checkpoint; no audit-driven selection',
 'evaluation_k':[1,3],'fusion':'BM25 + normalized dense cosine; reciprocal rank fusion constant 60'},indent=2)+'\n')
