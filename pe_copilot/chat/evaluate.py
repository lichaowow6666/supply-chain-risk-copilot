"""Executed synthetic business walkthrough and optional live RAGAS judging."""
import argparse
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from pe_copilot.chat.demo import build_demo,WALKTHROUGH
from pe_copilot.chat.language import LanguageService,validate_answer
from pe_copilot.chat.retrieval import HybridRetriever,BGEEncoder
from pe_copilot.chat.workflow import ChatWorkflow
from pe_copilot.evaluation import load_suite,build_fixture_index,fixture_agent,DEFAULT_SUITE
from pe_copilot.filings.sec import atomic_json


def walkthrough(output='artifacts/evaluation/chat_walkthrough.json'):
    from unittest.mock import patch
    checks=[];responses=[]
    def check(name,value):checks.append({'Check':name,'Passed':bool(value)})
    with TemporaryDirectory(prefix='copilot-walkthrough-') as root:
        app,scope=build_demo(root)
        chat=ChatWorkflow(app,HybridRetriever(app.fundamental_agent),LanguageService(False),root+'/chat',True)
        session=uuid4().hex
        try:
            for i,q in enumerate(WALKTHROUGH):
                state=chat.ask(session,scope,q)
                if i==2:
                    check('Scenario pauses for explicit confirmation',state['pending'][0]['kind']=='scenario_confirmation')
                    state=chat.resume(session,scope,{'confirm':True})
                response=state['response'];responses.append({'question':q,'response':response})
                if i==0:check('Exposure answer has selected-report citations',response['citations'] and all(c['accession']==scope['accession'] for c in response['citations']))
                if i==1:check('Graph paths and highlights exist',state['graph']['paths'] and response['graph']['overlay']['edges'])
                if i==2:
                    result=state['last_scenario'];run=result['run_id'];values=result['simulation']['materials']['Copper']['cost_contribution']
                    check('Gross 16M / recovered 8M / absorbed 8M',values['gross']==16 and values['passed']==8 and values['absorbed']==8)
                    check('Operating income decreases 8M','USD -8.00' in response['text'])
                if i==3:check('Contribution explains the same saved run',response['scenario']['run_id']==run)
                if i==4:check('Unsupported allocation exposes evidence gap',response.get('evidence_gap') is True)
                if i==5:check('Synthetic restricted resource denied',response.get('permission')=='denied')
                if i==6:check('Evaluation panel is linked',response.get('open_evaluation') is True)
            pending=chat.ask(session,scope,'Simulate Copper shock +20%')
            check('Missing assumptions trigger clarification',pending['pending'][0]['kind']=='clarification')
            chat.resume(session,scope,{'cancel':True})
            changed={**scope,'accession':'another-accession'}
            check('Changed report cannot see previous history',not chat.inspect(session,changed).get('history'))
            with patch.object(chat.language,'answer',side_effect=RuntimeError('SYNTHETIC_SECRET')):
                response=chat.ask(session,scope,WALKTHROUGH[0])['response']
                check('Provider outage has source fallback and redacted error',response['citations'] and 'SYNTHETIC_SECRET' not in json.dumps(response))
            check('Prompt injection does not switch company',all(c['ticker']=='SYNTA' for c in response['citations']))
        finally:chat.close()
    record={'status':'completed' if all(c['Passed'] for c in checks) else 'failed','synthetic':True,
            'created_at':datetime.now(timezone.utc).isoformat(),'summary':checks,'results':responses,
            'warnings':['Offline walkthrough uses deterministic routing and quoted excerpts, not live LLM answer quality.',
                        'Synthetic permission denial is a policy demonstration; multi-user authentication is not implemented.']}
    atomic_json(output,record);return record


async def ragas_report(output='artifacts/evaluation/ragas_report.json'):
    # This runner sends only the explicitly synthetic evaluation fixtures to the configured provider.
    os.environ['RAGAS_DO_NOT_TRACK']='true'
    from dotenv import load_dotenv
    load_dotenv('.env')
    record={'status':'running','synthetic':True,'created_at':datetime.now(timezone.utc).isoformat(),
        'suite_sha256':sha256(DEFAULT_SUITE.read_bytes()).hexdigest(),'results':[],
        'target':'Generated evidence answers; routing/permissions/numeric correctness are tested separately.',
        'warnings':['Small authored synthetic sample; scores are model judgments, not proof of financial correctness.',
                    'Generator and judge use the same provider/model in this run; correlated judge bias is possible.']}
    atomic_json(output,record)
    try:
        from openai import AsyncOpenAI
        from ragas.llms import llm_factory
        from ragas.metrics.collections import Faithfulness,ContextPrecision,ContextRecall,FactualCorrectness
        from importlib.metadata import version
        if os.getenv('DEEPSEEK_API_KEY'):
            client=AsyncOpenAI(api_key=os.environ['DEEPSEEK_API_KEY'],base_url='https://api.deepseek.com',timeout=45,max_retries=1)
            model='deepseek-chat'
        elif os.getenv('OPENAI_API_KEY'):
            client=AsyncOpenAI(timeout=45,max_retries=1);model=os.getenv('COPILOT_CHAT_MODEL','gpt-4o-mini')
        else:
            raise RuntimeError('No configured judge')
        llm=llm_factory(model,client=client,temperature=0,max_tokens=2048,max_retries=1)
        record.update(judge_model=model,generator_model=model,ragas_version=version('ragas'),temperature=0)
        scorers={'faithfulness':Faithfulness(llm=llm),'context_precision':ContextPrecision(llm=llm),
                 'context_recall':ContextRecall(llm=llm),'factual_correctness':FactualCorrectness(llm=llm)}
        suite=load_suite();by_report={r['id']:r for r in suite['reports']}
        cases=[c for c in suite['cases'] if c['id'] in ('R01','R06','R13','R16','R18')]
        with TemporaryDirectory(prefix='copilot-ragas-') as root:
            index,_=build_fixture_index(suite,root)
            retriever=HybridRetriever(fixture_agent(suite,index),BGEEncoder(),root+'/vectors')
            language=LanguageService()
            for case in cases:
                report=by_report[case['scope']]
                retrieval=retriever.search(case['user_input'],report['ticker'],report['accession'],k=3)
                evidence=[dict(h,citation_id=i) for i,h in enumerate(retrieval['hits'],1)]
                row={'case_id':case['id'],'question':case['user_input'],'reference':case['reference'],
                     'backend':retrieval['backend'],'warnings':retrieval['warnings'],'scores':{},'errors':{}}
                try:
                    answer=language.answer(case['user_input'],evidence,[])
                    row['response']=validate_answer(answer,evidence)
                    row['retrieved_contexts']=[h['text'] for h in evidence]
                    async def score(name,metric):
                        arguments={'response':row['response'],'reference':case['reference']} if name=='factual_correctness' else {
                            'user_input':case['user_input'],'retrieved_contexts':row['retrieved_contexts'],
                            ('response' if name=='faithfulness' else 'reference'):row['response'] if name=='faithfulness' else case['reference']}
                        try:
                            result=await asyncio.wait_for(metric.ascore(**arguments),timeout=120)
                            value=float(result.value)
                            import math
                            if not math.isfinite(value):raise ValueError('Nonfinite judge score')
                            row['scores'][name]=value
                        except Exception as error:row['errors'][name]=type(error).__name__
                    # Bound concurrent judge calls; each metric can itself require more than one completion.
                    for names in [('faithfulness','context_precision'),('context_recall','factual_correctness')]:
                        await asyncio.gather(*(score(name,scorers[name]) for name in names))
                except Exception as error:row['errors']['answer_generation']=type(error).__name__
                row['status']='completed' if len(row['scores'])==len(scorers) else 'partial' if row['scores'] else 'failed'
                record['results'].append(row);atomic_json(output,record)
                print(case['id'],row['status'],row['scores'],row['errors'],flush=True)
        record['summary']=[{'Case':r['case_id'],'Status':r['status'],'Retrieval':r['backend'],**r['scores']} for r in record['results']]
        record['status']='completed' if all(r['status']=='completed' for r in record['results']) else 'partial'
        await client.close()
    except Exception as error:
        record['status']='failed';record['error_type']=type(error).__name__
        print('RAGAS setup failed:',type(error).__name__,flush=True)
    atomic_json(output,record);return record


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--ragas',action='store_true')
    args=parser.parse_args()
    result=asyncio.run(ragas_report()) if args.ragas else walkthrough()
    print(result['status'])
    raise SystemExit(0 if result['status']=='completed' else 1)
