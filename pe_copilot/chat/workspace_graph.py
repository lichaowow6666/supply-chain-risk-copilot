"""LangGraph control flow over the existing conversational workspace services.

Only serializable conversation records enter checkpoints. Live services and
Streamlit widgets stay outside the graph; canvas updates are applied on the UI
thread. A clarification can resume with the same session and report scope.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from time import monotonic
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_stream_writer
from langgraph.types import Command, interrupt
from .activity import timed_activity


class Turn(TypedDict, total=False):
    question: str
    turn_id: str
    scope: dict
    intent: dict
    reply: dict
    prefix: str
    trace: list
    clarification_context: list


def _reply():
    return {'text': '', 'citations': [], 'steps': [], 'section': None,
            'status': 'ok', 'runtime': {}}


def _scope(state):
    return {**state.get('baseline_context', {}),
            'ticker': state.get('active_company', {}).get('ticker')}


def respond(chat, question, state, progress):
    # Import via the existing module so its tested intent/explanation contracts
    # are shared by both UI and evaluation, without a second routing policy.
    from . import workspace as services

    if not isinstance(question, str) or not question.strip() or len(question) > 8000:
        return {**_reply(), 'status': 'error', 'text': 'Enter a request of 1–8000 characters.', 'scope': _scope(state)}
    started = monotonic()
    working = dict(state)
    original_keys = set(working)
    scope = _scope(working)
    thread = chat.session_id + ':' + sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
    config = {'configurable': {'thread_id': thread}, 'recursion_limit': 20}
    call_start = len(chat.language.calls) if isinstance(getattr(chat.language, 'calls', None), list) else 0
    observed = {'activities': []}

    def record(reply, message):
        reply['steps'].append(message)
        get_stream_writer()(message)

    def complete(turn, name, since, reply, **changes):
        return {'reply': reply, 'trace': [*turn.get('trace', []),
                {'node': name, 'status': 'completed', 'duration_ms': round((monotonic()-since)*1000, 2)}], **changes}

    def route(turn):
        since, reply = monotonic(), _reply()
        context = services.workspace_context(working)
        context['conversation'] = [*context['conversation'], *turn.get('clarification_context', [])][-8:]
        record(reply, 'Interpreting your question with the current conversation and workspace.')
        intent = services.parse_workspace_intent(turn['question'], context, chat.language)
        action, section = intent['action'], intent['section']
        kind = intent.get('answer_kind', 'scenario' if section == 'impact' else 'company')
        intent = {**intent, 'answer_kind': kind}
        reply.update(answer_kind=kind, section=section)
        calls = getattr(chat.language, 'calls', [])
        planning_calls = calls[call_start:] if isinstance(calls, list) else []
        reply['runtime']['planning_call_count'] = len(planning_calls)
        reply['runtime']['route'] = {'action': action, 'answer_kind': kind, 'section': section}
        # A failed model attempt falls back to local clarification. A received
        # response still requires validation; it does not prove plan acceptance.
        reply['runtime']['route']['decision_source'] = (
            'model_response_received' if any(call.get('status') == 'completed' for call in planning_calls)
            else 'model_unavailable_local_fallback' if planning_calls else 'validated_local_rule')
        record(reply, f"Request understood as {kind.replace('_', ' ')} {'question' if action == 'explain' else action}.")
        if action == 'clarify':
            reply.update(section=None, text=intent.get('clarification') or 'Which company or part of the analysis would you like to explore?')
            record(reply, 'Asked for the missing context before changing the workspace.')
        return complete(turn, 'route', since, reply, intent=intent, prefix='')

    def prepare(turn):
        since, reply, intent = monotonic(), deepcopy(turn['reply']), turn['intent']
        log = lambda message: record(reply, message)
        log(f"Company preparation service: looking up {intent['company_query']} in SEC records.")
        with timed_activity(observed, 'Prepare company and SEC reports', 'Orchestrator', 'prepare_company',
                            detail='Coordinates the existing SEC preparation service.') as activity:
            prepared = chat.orchestrator.prepare_company(intent['company_query'], log,
                                                        services.is_sec_refresh_request(turn['question']))
            if prepared.get('reused'):
                activity['detail'] = 'Validated and reused local company data; no new SEC download claimed.'
        company = prepared['company']
        accession = prepared['baseline']['accession']
        if company['cik'] == working.get('active_company', {}).get('cik'):
            accession = working.get('selected_report_accession', accession)
        with timed_activity(observed, 'Resolve selected report baseline', 'Orchestrator', 'report_baseline'):
            baseline = chat.orchestrator.preparation_service.report_baseline(company['ticker'], accession)
        log(f"Fundamental agent: scanning {company['ticker']} {baseline['form']} ending {baseline['period_end']} for material exposures.")
        with timed_activity(observed, 'Coordinate exposure discovery', 'Orchestrator', 'discover_target',
                            detail='Delegates selected-report exposure analysis to the Fundamental Agent.'):
            with timed_activity(observed, 'Discover selected-report exposures', 'Fundamental Agent', 'analyze_report',
                                detail='Exposure discovery through the orchestrator; valid extraction caches may be reused.'):
                materials, pools = chat.orchestrator.discover_target(company['ticker'], baseline['accession'])
        services.activate_prepared_company(working, prepared, baseline, materials, pools)
        prefix = (f"**{company['name']} ({company['ticker']}) is ready.** "
                  + ('Existing local data passed integrity checks and was reused. ' if prepared.get('reused') else 'SEC filings and their evidence are prepared. ')
                  + f"I scanned the {baseline['form']} ending {baseline['period_end']}. You can choose another report above the financial overview.\n\n")
        return complete(turn, 'prepare_company', since, reply, prefix=prefix, scope=_scope(working))

    def navigate(turn):
        since, reply, intent = monotonic(), deepcopy(turn['reply']), turn['intent']
        section, prefix = intent['section'], turn.get('prefix', '')
        if not working.get('active_company'):
            reply.update(section=None, text='Tell me a company name, ticker or CIK first, so I can prepare its SEC reports.')
        else:
            if section in ('assumptions', 'forecast') and not working.get('selected_metals'):
                section = 'materials'
                prefix += 'First select at least one material with available market data in Tab 1; then I can open its scenario controls and price charts.\n\n'
            reply['section'] = section
            if section:
                services.focus_canvas(working, section)
                record(reply, f"Canvas: opening the {services.SECTIONS[section]} for the selected company and report.")
            reply['text'] = prefix + chat.navigation_reply(working, section)
            if intent.get('clarification'):
                reply['text'] += '\n\n' + intent['clarification']
        return complete(turn, 'navigate', since, reply, prefix=prefix)

    def answer(turn):
        since, reply, intent = monotonic(), deepcopy(turn['reply']), turn['intent']
        reply['runtime']['activities'] = observed['activities']
        section = reply['section']
        text, citations = chat.explain(turn['question'], working, section,
            lambda message: record(reply, message), intent, reply['runtime'])
        reply.update(text=turn.get('prefix', '') + text, citations=citations)
        return complete(turn, 'answer', since, reply)

    def clarify(turn):
        # No service calls or canvas changes precede this interrupt in the node.
        followup = interrupt({'kind': 'clarification', 'question': turn['reply']['text'], 'scope': turn['scope']})
        return {'question': followup['question'], 'turn_id': followup['turn_id'], 'reply': _reply(), 'trace': [],
                'clarification_context': [
                    {'role': 'user', 'content': turn['question']},
                    {'role': 'assistant', 'content': turn['reply']['text']}], 'prefix': ''}

    def finish(turn):
        since, reply = monotonic(), deepcopy(turn['reply'])
        reply.update(scope=_scope(working), question=turn['question'], turn_id=turn['turn_id'])
        calls = getattr(chat.language, 'calls', [])
        reply['runtime']['language_calls'] = list(calls[call_start:]) if isinstance(calls, list) else []
        return complete(turn, 'finish', since, reply)

    builder = StateGraph(Turn)
    for name, function in [('route', route), ('prepare_company', prepare), ('navigate', navigate),
                           ('answer', answer), ('clarify', clarify), ('finish', finish)]:
        builder.add_node(name, function)
    builder.add_edge(START, 'route')
    builder.add_conditional_edges('route', lambda turn: {
        'prepare': 'prepare_company', 'navigate': 'navigate', 'explain': 'answer', 'clarify': 'clarify'
    }[turn['intent']['action']])
    builder.add_edge('prepare_company', 'navigate')
    builder.add_conditional_edges('navigate', lambda turn:
        'answer' if turn['intent']['action'] == 'prepare' and turn['intent'].get('retrieval_query') else 'finish')
    builder.add_edge('answer', 'finish')
    builder.add_edge('clarify', 'route')
    builder.add_edge('finish', END)

    connection = None
    latest = {'reply': _reply(), 'trace': [], 'turn_id': uuid4().hex, 'question': question.strip()}
    try:
        path = Path(chat.checkpoint_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        graph = builder.compile(checkpointer=SqliteSaver(connection))
        saved = graph.get_state(config)
        pending = any(task.interrupts for task in saved.tasks)
        incoming = Command(resume={'question': question.strip(), 'turn_id': latest['turn_id']}) if pending else {
            **latest, 'scope': scope, 'prefix': '', 'intent': {}, 'clarification_context': []}
        for mode, data in graph.stream(incoming, config, stream_mode=['custom', 'values']):
            if mode == 'custom':
                progress(data)
            else:
                latest = data
        saved = graph.get_state(config)
        waiting = any(task.interrupts for task in saved.tasks)
        reply = deepcopy(latest['reply'])
        calls = getattr(chat.language, 'calls', [])
        reply['runtime']['language_calls'] = list(calls[call_start:]) if isinstance(calls, list) else []
        reply['runtime']['workflow'] = {'engine': 'LangGraph', 'checkpoint': 'SQLite',
            'status': 'waiting_for_clarification' if waiting else 'completed',
            'nodes': latest.get('trace', []) + ([{'node': 'clarify', 'status': 'waiting'}] if waiting else []),
            'checkpoint_id': saved.config['configurable'].get('checkpoint_id'),
            'scope': scope}
    except Exception as error:
        reply = deepcopy(latest.get('reply') or _reply())
        reason = str(error) if isinstance(error, ValueError) else 'The requested service could not finish. Please retry; the current workspace is preserved.'
        if isinstance(error, RuntimeError) and str(error).startswith('Preparation incomplete:'):
            reason = str(error)
        reply.update(status='error', text=reason[:700])
        reply['steps'].append('The requested action did not finish; no completed result was claimed.')
        reply['runtime']['workflow'] = {'engine': 'LangGraph', 'status': 'failed', 'error_type': type(error).__name__,
                                      'nodes': latest.get('trace', [])}
    finally:
        if connection is not None:
            connection.close()
    # Do not serialize, replace or reset working scenario/forecast objects. Apply
    # only keys that changed, on the Streamlit caller's thread.
    for key in original_keys - set(working):
        state.pop(key, None)
    for key, value in working.items():
        if key not in state or value is not state[key]:
            state[key] = value
    reply.update(scope=_scope(working), question=latest.get('question', question), turn_id=latest.get('turn_id'),
                 elapsed_seconds=round(monotonic()-started, 2))
    reply['runtime']['activities'] = deepcopy(observed['activities'])
    reply['runtime']['response_duration_ms'] = round((monotonic() - started) * 1000, 3)
    # Failed service calls are still part of this turn's measured work.
    calls = getattr(chat.language, 'calls', [])
    reply['runtime']['language_calls'] = list(calls[call_start:]) if isinstance(calls, list) else []
    return reply
