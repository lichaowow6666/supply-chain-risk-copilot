"""Conversational evidence, scenario confirmation and measured evaluation results."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import streamlit as st
from .graph_visualization import render_graph_html


@st.cache_resource
def chat_service(_orchestrator, synthetic=False, use_llm=True, use_dense=True):
    from pe_copilot.chat.workflow import ChatWorkflow
    from pe_copilot.chat.retrieval import HybridRetriever, BGEEncoder
    from pe_copilot.chat.language import LanguageService
    retriever=HybridRetriever(_orchestrator.fundamental_agent,BGEEncoder() if use_dense else None)
    return ChatWorkflow(_orchestrator,retriever,LanguageService(use_llm),
                        'artifacts/chat_demo' if synthetic else 'artifacts/chat',synthetic)


def citation_focus(payload, citation):
    """Link chunk citations to graph citations by content hash and overlapping locator."""
    result=deepcopy(payload)
    nodes,edges=set(),set()
    def matches(ref):
        if ref['document_id'].split(':')[-1]!=citation['document_id'].split(':')[-1]:return False
        a,b=ref['locator'].split(':'),citation['locator'].split(':')
        if a[0]!=b[0]:return False
        if 'row' in a or 'row' in b:return a==b
        return len(a)==len(b)==3 and max(int(a[1]),int(b[1]))<min(int(a[2]),int(b[2]))
    for node in result.get('nodes',[]):
        if any(matches(e) for e in node['evidence']):nodes.add(node['id'])
    for edge in result.get('edges',[]):
        if any(matches(e) for e in edge['evidence']):
            edges.add(edge['id']);nodes.update((edge['source'],edge['target']))
    for node in list(nodes):
        chain=result.get('chains',{}).get(node,{})
        nodes.update(chain.get('nodes',[]));edges.update(chain.get('edges',[]))
    result['overlay']={**result.get('overlay',{}),'source':'chatbot','nodes':sorted(nodes),'edges':sorted(edges),'roots':sorted(nodes),'shocks':[]}
    return result


def render_evaluations():
    st.subheader('Evaluation comparison')
    st.caption('Synthetic development and company-disjoint audit results. A missing measurement is never shown as a passing score.')
    for label,path in [('Embedding comparison','artifacts/evaluation/embedding_comparison.json'),
                       ('Business walkthrough','artifacts/evaluation/chat_walkthrough.json'),
                       ('RAGAS judging','artifacts/evaluation/ragas_report.json')]:
        with st.expander(label,expanded=True):
            file=Path(path)
            if not file.exists():
                file=Path('evaluations/results')/(file.stem+'_v1.json')
            if not file.exists():
                st.info('Not run yet. See the evaluation guide for the reproducible command.');continue
            result=json.loads(file.read_text())
            st.write('Execution status: '+result.get('status','measured'))
            if result.get('promotion_decision'):
                st.info(result['promotion_decision'])
            if result.get('summary'):st.dataframe(result['summary'],hide_index=True,width='stretch')
            for warning in result.get('warnings',[]):st.warning(warning)
            st.json({k:v for k,v in result.items() if k not in ('results','rows','summary')},expanded=False)
            st.download_button('Download '+label,result_json(result),file.name,'application/json',key='download_'+file.stem)
    st.caption('Retrieval scores test evidence ranking; RAGAS checks generated answers using a model judge. Numeric accuracy, permission denial and report isolation use deterministic checks.')


def result_json(value):
    return json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)


def render_chat(orchestrator,scope,synthetic=False):
    from pe_copilot.chat.demo import WALKTHROUGH
    st.subheader('Ask the evidence copilot')
    if synthetic:
        st.warning('SYNTHETIC EVALUATION WORKSPACE — all companies, disclosures and market prices here are fictional. The permission case is a demonstration, not production authentication.')
    st.caption(f"{scope['ticker']} · report {scope['accession']} · evidence and calculations stay within this report")
    a,b=st.columns(2)
    use_llm=a.toggle('Use configured language model',value=not synthetic,key='chat_use_llm_'+str(synthetic),
        help='When disabled or unavailable, routing uses explicit rules and answers show source excerpts. Financial arithmetic always stays in Python.')
    use_dense=b.toggle('Use BGE-M3 hybrid retrieval',value=True,key='chat_use_dense_'+str(synthetic),
        help='BM25 plus local BGE-M3 dense embeddings. Missing model files cause a visible BM25 fallback.')
    try:
        service=chat_service(orchestrator,synthetic,use_llm,use_dense)
    except ImportError:
        st.error('Chat dependencies are not installed. Install requirements-chat.txt in the existing environment.');return
    suffix='demo' if synthetic else 'company'
    key='chat_session_'+suffix
    if key not in st.session_state:st.session_state[key]=uuid4().hex
    with st.expander('Conversation and local checkpoint'):
        st.caption('Checkpoints are saved locally. This prototype has no multi-user login. Keep the conversation code private on shared machines.')
        resume_id=st.text_input('Conversation code',value=st.session_state[key],key='chat_resume_'+suffix)
        if st.button('Resume conversation',key='resume_'+suffix):
            if len(resume_id)<8 or not resume_id.replace('-','').replace('_','').isalnum():st.error('Invalid conversation code.')
            else:st.session_state[key]=resume_id;st.rerun()
        if st.button('New conversation',key='new_'+suffix):
            st.session_state[key]=uuid4().hex
            st.session_state.pop('chat_resume_'+suffix,None);st.rerun()
    session=st.session_state[key]
    state=service.inspect(session,scope)
    with st.expander('Business walkthrough',expanded=synthetic):
        st.caption('Exposure → evidence and graph → explicit price shock → contribution → evidence gap → evaluation. The example dollar assumptions are synthetic and remain proposals until confirmed.')
        selected=st.selectbox('Example question',WALKTHROUGH,key='chat_example_'+suffix)
        if st.button('Ask example',disabled=bool(state.get('pending')),key='ask_example_'+suffix):
            with st.spinner('Inspecting the selected report…'):
                service.ask(session,scope,selected)
            st.rerun()
    history=state.get('history',[])
    for item in history:
        with st.chat_message('user'):st.write(item['question'])
        with st.chat_message('assistant'):
            response=item['response'];st.write(response['text'])
            for warning in response.get('warnings',[]):st.caption(warning)
            with st.expander('Evidence and visible execution steps'):
                st.dataframe(response.get('trace',[]),hide_index=True,width='stretch')
                if response.get('scenario'):
                    from pe_copilot.analytics.scenario_attribution import contribution_rows
                    st.caption('Domain-service execution: these are recorded actions and data sources, not private model reasoning.')
                    st.dataframe(response['scenario'].get('trace',[]),hide_index=True,width='stretch')
                    if response['scenario'].get('simulation'):
                        st.dataframe(contribution_rows(response['scenario']['simulation']),hide_index=True,width='stretch')
                    st.download_button('Download calculation record',result_json(response['scenario']),
                        response['scenario']['run_id']+'.json','application/json',key='run_'+response['turn_id'])
                for cite in response.get('citations',[]):
                    st.caption(f"[{cite['citation_id']}] {cite.get('document_type','')} · {cite.get('period_end','')} · {cite['accession']} · {cite['locator']}")
                    st.text(cite['text'])
                    url=cite.get('source_url') or ''
                    if url.startswith('https://') and 'example.invalid' not in url:st.link_button('Open filing',url)
                    if response.get('graph') and st.button('Highlight linked graph evidence',key=response['turn_id']+'_'+str(cite['citation_id'])):
                        st.session_state['chatview_graph']={'scope':scope,'payload':citation_focus(response['graph'],cite)}
    pending=state.get('pending',[])
    if pending:
        item=pending[0]
        st.info(item['question'])
        if item['kind']=='scenario_confirmation':
            proposal=item['proposal']
            st.dataframe([{'Material':m['material'],'Period spend (USD M)':m['period_spend'],
                'Unhedged (%)':100*m['unhedged_fraction'],'Price shock (%)':100*m['shock_fraction'],
                'Pass-through (%)':100*proposal['pass_through_fraction'],'Spend basis':proposal['spend_basis'],
                'Price horizon (sessions)':proposal['shock_horizon_days']} for m in proposal['materials']],hide_index=True)
            st.caption('The shock is applied to the full entered spending period; it is not a time-weighted earnings forecast.')
            yes,no=st.columns(2)
            if yes.button('Confirm and calculate',type='primary'):
                with st.spinner('Running the existing scenario services…'):service.resume(session,scope,{'confirm':True})
                st.rerun()
            if no.button('Cancel scenario'):
                service.resume(session,scope,{'confirm':False});st.rerun()
        else:
            with st.form('chat_clarification'):
                answer=st.text_input('Additional details')
                submit=st.form_submit_button('Continue')
            if submit and answer.strip():
                with st.spinner('Validating the details…'):service.resume(session,scope,answer)
                st.rerun()
            if st.button('Cancel request'):
                service.resume(session,scope,{'cancel':True});st.rerun()
    question=st.chat_input('Ask about the selected report, graph or a price shock',disabled=bool(pending))
    if question:
        with st.spinner('Following the evidence and service workflow…'):service.ask(session,scope,question)
        st.session_state.pop('chatview_graph',None)
        st.rerun()
    chosen=st.session_state.get('chatview_graph')
    payload=chosen['payload'] if chosen and chosen['scope']==scope else next((r['response']['graph'] for r in reversed(history) if r['response'].get('graph')),None)
    if payload:
        st.subheader('Linked evidence and scenario graph')
        st.caption('Most recent graph in this report-scoped conversation. Hover to inspect the chain; quoted evidence can focus its linked nodes and edges.')
        st.iframe(render_graph_html(payload),height=620)
    with st.expander('Open evaluation comparison',expanded=bool(history and history[-1]['response'].get('open_evaluation'))):
        render_evaluations()
