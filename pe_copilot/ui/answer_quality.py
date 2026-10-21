"""Opt-in checks of a saved answer; never modify the analysis canvas."""
import re
import streamlit as st


def render_answer_checks(message, current_scope, orchestrator):
    if not message.get('turn_id') or message.get('answer_kind') in ('general', 'workflow'):
        return
    if message.get('runtime', {}).get('answer', {}).get('status') != 'generated':
        return
    from pe_copilot.chat.quality import build_evaluation_packet, evaluate_response
    identifier = message['turn_id']
    with st.expander('Answer checks'):
        packet = build_evaluation_packet(message.get('question', ''), message)
        st.caption('Checks this saved answer against its sources. Model judgments can be wrong.')
        if st.button('Check grounding', key='quality_check_' + identifier):
            with st.spinner('Checking claims against the supplied evidence…'):
                st.session_state['quality_result_' + identifier] = evaluate_response(packet)
        result = st.session_state.get('quality_result_' + identifier)
        if result:
            scores = result.get('scores', {})
            if 'faithfulness' in scores:
                st.metric('Evidence support · RAGAS', f"{scores['faithfulness']:.2f}")
                st.caption('Support in supplied evidence; this is not an overall accuracy score.')
                if result.get('context_scope') == 'sources_and_structured_context':
                    st.caption('Includes cited sources and the saved calculations and workspace facts supplied to this answer.')
                else:
                    st.caption('This earlier answer retained source texts only; other supplied context may be flagged as missing.')
            else:
                st.write('Grounding check ' + result.get('status', 'unavailable').replace('_', ' '))
                if result.get('reason'):
                    st.write(result['reason'])
            for claim in result.get('claim_checks', []):
                st.markdown(('**Supported** · ' if claim.get('verdict') == 1 else '**Needs review** · ')
                            + re.sub(r'(?<!\\)\$', r'\\$', claim.get('statement', '')))
                if claim.get('reason'):
                    st.write(re.sub(r'(?<!\\)\$', r'\\$', claim['reason']))
            st.caption(f"Judge: {result.get('judge_model', 'unavailable')} · {result.get('status', 'unavailable')}")

        scope = message.get('scope', {})
        same_scope = (scope.get('company_id'), scope.get('accession')) == (
            current_scope.get('company_id'), current_scope.get('accession'))
        if message.get('answer_kind') == 'company' and scope.get('ticker') and scope.get('accession'):
            if st.button('Compare base and fine-tuned retrieval', key='quality_compare_' + identifier,
                         disabled=not same_scope):
                from pe_copilot.chat.model_registry import compare_saved_question
                with st.spinner('Comparing evidence rankings for this question…'):
                    st.session_state['quality_comparison_' + identifier] = compare_saved_question(
                        orchestrator.fundamental_agent, message.get('question', ''),
                        scope['ticker'], scope['accession'], k=6)
            comparison = st.session_state.get('quality_comparison_' + identifier)
            if comparison:
                st.caption('Same question, report, candidate budget and reranker. Rank changes do not establish better answers.')
                for run in comparison['runs']:
                    st.markdown('**' + ('Base' if run['profile'] == 'base' else 'Fine-tuned · experimental') + '**')
                    st.caption(f"{run['status']} · {run['elapsed_ms']/1000:.1f}s")
                    for rank, hit in enumerate(run.get('hits', []), 1):
                        st.write(f"{rank}. " + re.sub(r'(?<!\\)\$', r'\\$', hit['text']))
                st.caption('The frozen synthetic audit found no improvement; base remains the default.')
