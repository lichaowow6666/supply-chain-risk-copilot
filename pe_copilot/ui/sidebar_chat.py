"""Persistent conversation entry point; the existing tabs remain the result canvas."""
from copy import deepcopy
from urllib.parse import urlparse
import re
import json
from pathlib import Path
from uuid import uuid4

import streamlit as st

from pe_copilot.chat.workspace import WorkspaceChat, focus_canvas


@st.cache_resource
def workspace_retriever(_fundamental, service_generation, profile='base'):
    from pe_copilot.chat.retrieval import make_workspace_retriever
    return make_workspace_retriever(_fundamental, profile=profile)


def _render_reply(message, index, current_scope, orchestrator=None):
    scope = message.get('scope', {})
    heading, inspection = st.columns([0.63, 0.37], vertical_alignment='center')
    with heading:
        if scope.get('ticker'):
            st.caption(f"{scope['ticker']} · {scope.get('form', 'Report')} · {scope.get('period_end', '')}")
    with inspection:
        if st.button('Inspect answer', key=f"inspect_answer_{message.get('turn_id', index)}_{index}",
                     type='tertiary', width='content'):
            from pe_copilot.ui.interview_panel import show_answer_panel
            show_answer_panel(deepcopy(message))
    # Dollar-denominated prose must not turn into a paired LaTeX expression.
    st.markdown(re.sub(r'(?<!\\)\$', r'\\$', message['text']))
    from pe_copilot.ui.feedback import render_answer_feedback
    render_answer_feedback(message)
    cited_ids = {int(value) for value in re.findall(r'\[(\d+)\]', message['text'])}
    sources = [(number, source) for number, source in enumerate(message.get('citations', []), 1)
               if number in cited_ids or message.get('runtime', {}).get('answer', {}).get('status') == 'unavailable']
    if sources:
        with st.expander(f"Sources and calculations ({len(sources)})"):
            for number, source in sources:
                st.markdown(f"**[{number}] {source.get('material', source.get('section', 'SEC report'))}**")
                if source.get('kind'):
                    st.caption(source['kind'])
                st.caption(f"{source.get('document_type', scope.get('form', ''))} · {source.get('period_end', scope.get('period_end', ''))} · {source.get('chunk_id', source.get('locator', ''))}")
                st.markdown(re.sub(r'(?<!\\)\$', r'\\$', source['text']))
                url = source.get('source_url') or ''
                parsed = urlparse(url)
                if parsed.scheme == 'https' and parsed.hostname in ('www.sec.gov', 'sec.gov', 'data.sec.gov'):
                    st.link_button(f'Open source [{number}]', url)
    if message.get('steps'):
        with st.expander('What I did'):
            for step in message['steps']:
                st.write('• ' + step)
            runtime = message.get('runtime', {})
            workflow = runtime.get('workflow', {})
            if workflow:
                st.caption(f"{workflow.get('engine', 'Workflow')} · {workflow.get('status', '').replace('_', ' ')} · {workflow.get('checkpoint', 'checkpoint unavailable')}")
                st.write(' → '.join(node['node'].replace('_', ' ') for node in workflow.get('nodes', [])))
            for call_index, call in enumerate(runtime.get('language_calls', [])):
                purpose = 'Understanding the request' if call_index < runtime.get('planning_call_count', 0) else 'Writing the answer'
                st.caption(f"{purpose}: {call.get('provider') or 'unavailable'} · {call.get('model') or 'not loaded'} · {call['status']} · {call.get('duration_ms', 0)/1000:.1f}s")
            retrieval = runtime.get('retrieval', {})
            if retrieval.get('backend'):
                st.caption('Evidence search: ' + retrieval['backend'])
            for stage in retrieval.get('stages', []):
                st.caption(f"{stage['stage'].title()}: {stage['status']} · {stage.get('candidate_count', 0)} passages · {stage['duration_ms']/1000:.2f}s")
            st.caption(f"This response took {message.get('elapsed_seconds', 0):.1f}s.")
    if orchestrator is not None:
        from pe_copilot.ui.answer_quality import render_answer_checks
        render_answer_checks(message, current_scope, orchestrator)


def render_sidebar_chat(orchestrator):
    """Render before canvas widgets so authorized state changes happen before UI binding."""
    with st.sidebar:
        st.title('💬 Supply Chain Risk Copilot')
        company = st.session_state.get('active_company')
        scope = st.session_state.get('baseline_context', {})
        if company:
            st.markdown(f"**{company['name']} ({company['ticker']})**")
            st.caption(f"{scope.get('form', 'Report')} · {scope.get('period_end', '')}")

        with st.expander('Evidence settings'):
            from pe_copilot.chat.model_registry import ModelRegistry
            profiles = {item['profile']: item for item in ModelRegistry().profiles()}
            options = ['base'] + (['tuned'] if profiles['tuned']['status'] == 'available' else [])
            if st.session_state.get('copilot_embedding_profile', 'base') not in options:
                st.session_state['copilot_embedding_profile'] = 'base'
            profile = st.selectbox('Evidence embedding', options, key='copilot_embedding_profile',
                format_func=lambda item: 'Base BGE-M3' if item == 'base' else 'Fine-tuned BGE-M3 · experimental')
            if profile == 'tuned':
                st.caption('Uses the trained checkpoint for new answers. The synthetic audit did not establish an improvement.')
            latest = Path('artifacts/evaluation/workspace_quality_latest.json')
            if latest.is_file():
                try:
                    comparison = json.loads(latest.read_text())
                    st.markdown('**Latest answer comparison**')
                    st.caption('Synthetic development cases · current chat workflow · evidence-support scores, not overall accuracy.')
                    rows = {}
                    for row in comparison.get('summary', []):
                        label = {'R01': 'Annual revenue', 'R06': 'Quarterly exposure', 'R13': 'Undisclosed spend'}.get(row['case_id'], row['case_id'])
                        rows.setdefault(label, {'Question': label})[row['profile'].title()] = round(row['faithfulness'], 2) if 'faithfulness' in row else row['status']
                    st.dataframe(list(rows.values()), hide_index=True)
                except (OSError, ValueError, KeyError, TypeError):
                    st.caption('The saved evaluation comparison is unavailable.')

        history = st.session_state.setdefault('copilot_messages', [])
        conversation = st.container(height=500, border=False)
        with conversation:
            if not history:
                with st.chat_message('assistant'):
                    st.write('Which company would you like to explore?')
            for index, message in enumerate(history):
                with st.chat_message(message['role']):
                    if message['role'] == 'user':
                        st.markdown(message['text'])
                    else:
                        _render_reply(message, index, scope, orchestrator)

        prompt = st.chat_input('Ask about a company, exposure or result…', key='copilot_prompt', max_chars=1500)
        if company:
            if st.button('View scenario', key='view_scenario', width='stretch'):
                focus_canvas(st.session_state, 'assumptions')
                st.rerun()
        if prompt:
            history.append({'role': 'user', 'text': prompt})
            with conversation:
                with st.chat_message('user'):
                    st.markdown(prompt)
                with st.chat_message('assistant'):
                    with st.status('Working on your request…', expanded=False) as status:
                        retriever = workspace_retriever(orchestrator.fundamental_agent, id(orchestrator), profile)
                        session_id = st.session_state.setdefault('copilot_conversation_id', uuid4().hex)
                        reply = WorkspaceChat(orchestrator, retriever=retriever, session_id=session_id).respond(prompt, st.session_state, st.write)
                        status.update(label='Ready' if reply['status'] == 'ok' else 'Could not finish',
                                      state='complete' if reply['status'] == 'ok' else 'error', expanded=False)
            history.append({'role': 'assistant', **reply})
            if st.session_state.get('pilot_measurement_enabled') is True:
                from pe_copilot.ui.interview_dashboard import record_interview_usage
                record_interview_usage('answer_rendered', reply.get('turn_id'))
            # Rerender the header, transcript and tabs from the same updated scope.
            st.rerun()
    return st.session_state.get('active_company')
