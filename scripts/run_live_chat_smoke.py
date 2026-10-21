"""Synthetic live routing/answer smoke. Never reads actual company filings."""
from pathlib import Path
from uuid import uuid4
from dotenv import load_dotenv
from pe_copilot.chat.demo import build_demo,WALKTHROUGH
from pe_copilot.chat.language import LanguageService
from pe_copilot.chat.retrieval import HybridRetriever,BGEEncoder
from pe_copilot.chat.workflow import ChatWorkflow
from pe_copilot.filings.sec import atomic_json
load_dotenv('.env')
app,scope=build_demo('artifacts/interview_demo')
chat=ChatWorkflow(app,HybridRetriever(app.fundamental_agent,BGEEncoder()),LanguageService(),root='artifacts/live_chat_smoke',synthetic=True)
session=uuid4().hex
rows=[]
try:
 for q in WALKTHROUGH:
  state=chat.ask(session,scope,q)
  if state['pending']:
   if state['pending'][0]['kind']!='scenario_confirmation':raise ValueError('Unexpected clarification')
   state=chat.resume(session,scope,{'confirm':True})
  response=state['response']
  rows.append({'question':q,'route':response['route'],'text':response['text'],'mode':response.get('answer_mode'),
               'trace':response['trace'],'citation_count':len(response.get('citations',[])),
               'scenario_status':response.get('scenario',{}).get('status')})
  print(response['route'],response.get('answer_mode','service result'),flush=True)
 atomic_json('artifacts/evaluation/live_chat_smoke.json',{'status':'completed','synthetic':True,'rows':rows})
finally:chat.close()
