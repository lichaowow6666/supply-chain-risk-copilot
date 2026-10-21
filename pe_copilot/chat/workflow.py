"""Durable LangGraph routing, clarification, permission gates and domain-service calls."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import TypedDict
from uuid import uuid4

from pe_copilot.contracts import ScenarioMaterial, ScenarioRequest, WorkspaceContext
from pe_copilot.chat.language import LanguageService, ROUTES, fallback_plan, validate_answer
from pe_copilot.chat.retrieval import graph_evidence, evidence_key


class ChatState(TypedDict, total=False):
    question: str
    scope: dict
    plan: dict
    response: dict
    history: list
    trace: list
    last_material: str
    last_scenario: dict
    proposal: dict
    evidence: list
    graph: dict
    retrieval: dict
    turn_id: str
    clarification_count: int


def step(state, name, detail, **updates):
    return {**updates,'trace':[*state.get('trace',[]),{'step':name,'detail':detail,
            'at':datetime.now(timezone.utc).isoformat()}]}


class ChatWorkflow:
    def __init__(self, orchestrator, retriever, language=None, root='artifacts/chat', synthetic=False):
        from langgraph.graph import StateGraph, START, END
        from langgraph.checkpoint.sqlite import SqliteSaver
        self.orchestrator, self.retriever = orchestrator, retriever
        self.language = language or LanguageService()
        self.synthetic = synthetic
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.lock=RLock()
        self.connection=sqlite3.connect(self.root/'checkpoints.sqlite',check_same_thread=False)
        builder=StateGraph(ChatState)
        for name in ('route','clarify','retrieve','answer','propose','confirm','calculate','respond','finish'):
            builder.add_node(name,getattr(self,'_'+name))
        builder.add_edge(START,'route')
        builder.add_conditional_edges('route',self._next,{'clarify':'clarify','retrieve':'retrieve','propose':'propose','respond':'respond'})
        builder.add_edge('clarify','route')
        builder.add_edge('retrieve','answer');builder.add_edge('answer','finish')
        builder.add_conditional_edges('propose',lambda s:'clarify' if s['plan'].get('missing') else 'confirm',{'clarify':'clarify','confirm':'confirm'})
        builder.add_conditional_edges('confirm',lambda s:'calculate' if s['proposal'].get('confirmed') else 'finish',{'calculate':'calculate','finish':'finish'})
        builder.add_edge('calculate','finish');builder.add_edge('respond','finish');builder.add_edge('finish',END)
        self.graph=builder.compile(checkpointer=SqliteSaver(self.connection))

    def close(self):
        self.connection.close()

    def _config(self, session_id, scope):
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',session_id):
            raise ValueError('Invalid local session ID')
        digest=sha256(json.dumps(scope,sort_keys=True).encode()).hexdigest()
        return {'configurable':{'thread_id':session_id+':'+digest},'recursion_limit':30}

    def inspect(self, session_id, scope):
        with self.lock:
            snapshot=self.graph.get_state(self._config(session_id,scope))
            interrupts=[i.value for task in snapshot.tasks for i in task.interrupts]
            return {**snapshot.values,'pending':interrupts}

    def ask(self, session_id, scope, question):
        if not isinstance(question,str) or not question.strip() or len(question)>8000:
            raise ValueError('Enter a question of 1–8000 characters.')
        with self.lock:
            current=self.inspect(session_id,scope)
            if current.get('pending'):
                raise ValueError('Resolve or cancel the pending action before starting another question.')
            self.graph.invoke({'question':question.strip(),'scope':scope,'turn_id':uuid4().hex,
                'trace':[],'response':{},'evidence':[],'graph':{},'retrieval':{},'proposal':{},
                'plan':{},'clarification_count':0},self._config(session_id,scope))
            return self.inspect(session_id,scope)

    def resume(self, session_id, scope, answer):
        from langgraph.types import Command
        with self.lock:
            state=self.inspect(session_id,scope)
            if not state.get('pending'):
                raise ValueError('No action is waiting for confirmation.')
            self.graph.invoke(Command(resume=answer),self._config(session_id,scope))
            return self.inspect(session_id,scope)

    def _route(self,state):
        if state['question']=='cancel pending request':
            return step(state,'Request cancelled','No service called.',plan={'route':'clarify'})
        scope=state['scope']
        if not all(scope.get(k) for k in ('ticker','accession','company_id','snapshot_id')):
            return step(state,'Scope validation','No default filing substituted.',plan={'route':'clarify','clarification':'Choose a company and report in the workbench first.'})
        # Validate before retrieval or language calls. A selected graph is not a permission to another filing.
        try:
            service=self.orchestrator.preparation_service
            baseline=service.report_baseline(scope['ticker'],scope['accession'])
            prepared=service.load_preparation(scope['ticker'])
            if baseline['snapshot_id']!=scope['snapshot_id'] or 'company:'+prepared['company']['cik']!=scope['company_id']:
                raise ValueError('Scope mismatch')
            materials=[row['material'] for row in self.orchestrator.fundamental_agent.analyze_report(scope['ticker'],scope['accession'])['materials']]
        except Exception as error:
            return step(state,'Scope validation',type(error).__name__,plan={'route':'clarify','clarification':'The selected report is unavailable or changed. Prepare/select it again.'})
        plan=fallback_plan(state['question'],materials,state.get('last_material'))
        basis='deterministic routing'
        # Authorization, evidence-gap and saved calculation routes are never overridden by a model.
        if plan['route'] not in ('permission','allocation_gap','evaluation','contribution'):
            try:
                candidate=self.language.plan(state['question'],materials,state.get('last_material'))
                if candidate.get('route') not in ROUTES or not isinstance(candidate.get('assumptions',{}),dict):
                    raise ValueError('Invalid route')
                if candidate.get('material') is not None and candidate['material'] not in materials:
                    raise ValueError('Unknown material')
                plan=candidate;basis='constrained LLM routing; server validates scope and actions'
            except Exception as error:
                basis+=f'; language service unavailable ({type(error).__name__})'
        if plan['route']=='scenario' and sum(m.casefold() in state['question'].casefold() for m in materials)>1:
            plan={'route':'clarify','clarification':'The chat currently handles one material per proposal. Start a new question for one material, or use Scenario Analysis for a combined multi-material scenario.'}
        return step(state,'Route question',basis,plan=plan,last_material=plan.get('material') or state.get('last_material',''))

    def _next(self,state):
        route=state['plan']['route']
        if route=='clarify':
            return 'respond' if state.get('clarification_count',0)>=2 else 'clarify'
        if route=='scenario': return 'propose'
        if route in ('exposure','graph'): return 'retrieve'
        return 'respond'

    def _clarify(self,state):
        from langgraph.types import interrupt
        plan=state['plan']
        answer=interrupt({'kind':'clarification','question':plan.get('clarification') or
            'Provide explicit scenario assumptions: '+', '.join(plan.get('missing',[])),
            'scope':state['scope'],'missing':plan.get('missing',[])})
        if isinstance(answer,dict) and answer.get('cancel'):
            # Route to a plain response and keep the cancelled proposal out of calculation.
            return step(state,'Clarification cancelled','No calculation run.',question='cancel pending request',
                        clarification_count=2,plan={'route':'clarify'})
        if not isinstance(answer,str) or not answer.strip() or len(answer)>4000:
            raise ValueError('Clarification must be nonempty text.')
        return step(state,'Clarification received','User supplied additional context.',
                    question=state['question']+'\nClarification: '+answer,
                    clarification_count=state.get('clarification_count',0)+1)

    def _retrieve(self,state):
        scope=state['scope']
        try:
            retrieval=self.retriever.search(state['question'],scope['ticker'],scope['accession'])
            hits=retrieval['hits'];graph={}
            try:
                graph=graph_evidence(self.orchestrator,scope,state['question'],hits,state.get('last_material'))
                hits=[*hits,*graph['citations']]
            except Exception as error:
                retrieval['warnings'].append(f'Graph unavailable ({type(error).__name__}); filing evidence remains available.')
            unique={}
            for hit in hits:
                if hit['ticker']!=scope['ticker'] or hit['accession']!=scope['accession']:
                    raise ValueError('Evidence scope violation')
                unique.setdefault(evidence_key(hit),hit)
            evidence=[dict(row,citation_id=i) for i,row in enumerate(list(unique.values())[:16],1)]
            return step(state,'Retrieve evidence',retrieval['backend']+'; graph paths retain edge citations.',
                        retrieval=retrieval,evidence=evidence,graph=graph)
        except Exception as error:
            return step(state,'Retrieval unavailable',type(error).__name__,evidence=[],graph={},
                        retrieval={'warnings':['Selected report retrieval failed; no other report was used.']})

    def _answer(self,state):
        evidence=state['evidence'];graph=state['graph']
        mode='source excerpts'
        if not evidence:
            text='I could not find evidence for that question in the selected report. I will not invent an answer or substitute another filing.'
        else:
            try:
                result=self.language.answer(state['question'],evidence,graph.get('paths',[]))
                text=validate_answer(result,evidence);mode='LLM explanation with validated citation IDs'
            except Exception as error:
                mode=f'source excerpts; language answer unavailable ({type(error).__name__})'
                text='Retrieved evidence from the selected report (quoted excerpts; relevance still needs review):\n\n'+'\n\n'.join(
                    f'[{e["citation_id"]}] {e["text"]}' for e in evidence[:4])
                if graph.get('paths'):
                    text+='\n\nDisclosed graph path: '+' → '.join(max(graph['paths'],key=lambda p:len(p['labels']))['labels'])+'. Connectivity does not allocate a financial loss.'
        return step(state,'Compose grounded answer',mode,response={'text':text,'citations':evidence,
            'graph':graph.get('payload',{}),'highlight':graph.get('highlight'),
            'warnings':state.get('retrieval',{}).get('warnings',[]),'route':state['plan']['route'],'answer_mode':mode})

    def _propose(self,state):
        plan=state['plan'];values=plan.get('assumptions',{})
        keys=('period_spend','unhedged_fraction','pass_through_fraction','shock_fraction','shock_horizon_days','spend_basis')
        missing=[k for k in keys if k not in values or values[k] is None]
        if not plan.get('material'):missing.append('material')
        if missing:
            if state.get('clarification_count',0)>=2:
                # Limit repeated interrupts; the user can start a new explicit request.
                return step(state,'Assumptions incomplete','Nothing calculated.',plan={**plan,'missing':[]},
                    proposal={'invalid':True},response={'text':'Scenario inputs remain incomplete: '+', '.join(missing)+'. Start a new request with explicit values.','route':'clarify'})
            return step(state,'Request explicit assumptions','No financial defaults supplied.',plan={**plan,'missing':missing})
        try:
            request=ScenarioRequest(WorkspaceContext(state['scope']['company_id'],state['scope']['snapshot_id']),
                state['scope']['ticker'],state['scope']['accession'],
                (ScenarioMaterial(plan['material'],values['period_spend'],values['unhedged_fraction'],values['shock_fraction']),),
                values['pass_through_fraction'],spend_basis=values['spend_basis'],source='chatbot',shock_horizon_days=values['shock_horizon_days'])
            from dataclasses import asdict
            proposal=asdict(request)
            return step(state,'Validate scenario proposal','All numeric inputs are explicit user assumptions in USD millions.',proposal=proposal)
        except Exception:
            if state.get('clarification_count',0)>=2:
                return step(state,'Invalid assumptions','Nothing calculated.',plan={**plan,'missing':[]},
                    proposal={'invalid':True},response={'text':'The scenario inputs are invalid. Start a new request using USD millions, valid fractions and a supported horizon.','route':'clarify'})
            return step(state,'Invalid assumptions','No calculation run.',plan={**plan,'missing':['valid USD-million spend, fractions, spending period and 5/20/30/60-session horizon']})

    def _confirm(self,state):
        from langgraph.types import interrupt
        if state['proposal'].get('invalid'):
            return {'proposal':{**state['proposal'],'confirmed':False}}
        approved=interrupt({'kind':'scenario_confirmation','proposal':state['proposal'],
            'question':'Run this scenario using these explicit assumptions? This does not alter the manual workbench controls.'})
        confirmed=isinstance(approved,dict) and approved.get('confirm') is True
        return step(state,'Scenario confirmation','Approved' if confirmed else 'Cancelled',
                    proposal={**state['proposal'],'confirmed':confirmed},
                    response={} if confirmed else {'text':'Scenario cancelled. No calculation was run.','route':'scenario'})

    def _calculate(self,state):
        raw={k:v for k,v in state['proposal'].items() if k!='confirmed'}
        request=ScenarioRequest(**{**raw,'context':WorkspaceContext(**raw['context']),
                                  'materials':tuple(ScenarioMaterial(**m) for m in raw['materials'])})
        receipt=self.root/('scenario-'+state['turn_id']+'.json')
        if receipt.exists():
            result=json.loads(receipt.read_text())
        else:
            result=self.orchestrator.invoke(request).to_dict()
            if self.synthetic:
                result['synthetic']=True
                for row in result.get('market_snapshot',{}).get('series',{}).values():
                    row['source']='SYNTHETIC interview prices, not Yahoo observations'
            from pe_copilot.filings.sec import atomic_json
            atomic_json(receipt,result)
        response=self._scenario_response(result)
        return step(state,'Run existing scenario service',f"{result['status']} · run {result['run_id']}; deterministic arithmetic",last_scenario=result,response=response)

    @staticmethod
    def _scenario_response(result):
        if result['status']=='failed':
            return {'text':'The scenario could not complete. No valid calculation is available.','route':'scenario','warnings':result['warnings'],'scenario':result}
        simulation=result['simulation'];total=simulation['waterfall_summary']['Net_COGS_Absorbed']
        lines=[f'Calculated COGS change: USD {total:+,.2f} million. Operating income change: USD {-total:+,.2f} million.']
        for name,item in simulation['materials'].items():
            c=item['cost_contribution']
            lines.append(f"{name}: gross cost USD {c['gross']:+,.2f}M; passed to customers USD {c['passed']:+,.2f}M; absorbed cost USD {c['absorbed']:+,.2f}M.")
        lines.append('Calculation: entered period spend × unhedged fraction × price shock × (1 − pass-through). These spending and coverage inputs are user assumptions; graph relationships do not provide product-level allocations.')
        return {'text':'\n\n'.join(lines),'route':'scenario','scenario':result,'graph':result.get('graph',{}),
                'highlight':result.get('highlight'),'warnings':result['warnings'],'citations':[]}

    def _respond(self,state):
        route=state['plan']['route'];response={'route':route}
        if route=='contribution':
            result=state.get('last_scenario')
            response=self._scenario_response(result) if result else {'text':'Run a scenario for this selected report first; there is no saved calculation to explain.','route':route}
        elif route=='allocation_gap':
            response['text']='The current evidence and calculation do not establish product-level allocation weights. I can explain material contributions, but cannot split the company impact between products or segments. Such a split requires cited purchase/consumption data or explicit user-defined allocation weights. No product figures have been invented.'
            response['evidence_gap']=True
        elif route=='permission':
            from pe_copilot.chat.permissions import DemoPermissionPolicy
            decision=DemoPermissionPolicy().authorize('synthetic:treasury-memo')
            response.update(text='Synthetic permission demonstration: this session can read public filing evidence but cannot read the restricted Treasury memo. Access is denied before document retrieval. Changing the prompt cannot grant access. No real confidential document is present.',permission='denied',synthetic=True)
            response['permission_decision']=decision
        elif route=='evaluation':
            response.update(text='Open the Evaluation comparison panel below for measured retrieval, embedding-training and RAGAS results. Missing or failed runs are displayed explicitly.',open_evaluation=True)
        elif route=='forecast':
            material=state['plan'].get('material')
            if not material:
                response['text']='Choose a material and price horizon in Scenario Analysis to inspect a validated forecast. Future prices cannot be guaranteed.'
            else:
                try:
                    forecasts=self.orchestrator.prepare_forecasts([material],horizon=30)
                    response.update(text='The existing Forecasting Agent prepared a 30-session reference forecast. Inspect model comparison in Scenario Analysis; this is not a guarantee or an instruction to set a shock.',forecast=forecasts)
                except Exception as error:
                    response['text']=f'Forecast service unavailable ({type(error).__name__}). No price forecast was invented.'
        else:
            response['text']='The request was cancelled or remains ambiguous. Select a valid report and supply explicit assumptions before starting a new request.'
        return step(state,'Resolve request',route,response=response)

    def _finish(self,state):
        response={**state['response'],'trace':state['trace'],'scope':state['scope'],'turn_id':state['turn_id']}
        history=[*state.get('history',[]),{'question':state['question'],'response':response}][-30:]
        return {'response':response,'history':history}
