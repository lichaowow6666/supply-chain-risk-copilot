"""Language-first interpretation with a small, validated action boundary.

Normal conversation is classified by the supplied language service. Local fast
paths are limited to explicit identifiers and exact application commands; words
inside a sentence are never extracted as a company search by local rules.
"""
import re


ACTIONS = frozenset({'prepare', 'navigate', 'explain', 'clarify'})
ANSWER_KINDS = frozenset({'general', 'company', 'scenario', 'forecast', 'workflow'})
SECTIONS = frozenset({'overview', 'exposures', 'materials', 'assumptions', 'graph', 'impact', 'forecast'})
_REQUIRED = frozenset({'action', 'company_query', 'section', 'question', 'clarification', 'answer_kind'})
_OPTIONAL = frozenset({'retrieval_query', 'company_search_requested', 'company_mentions'})
_NAVIGATION = {
    'show exposures': 'exposures', 'open exposures': 'exposures',
    'show material exposures': 'exposures', 'open material exposure ranking': 'exposures',
    'show materials': 'materials', 'open materials': 'materials',
    'show scenario assumptions': 'assumptions', 'open scenario assumptions': 'assumptions',
    'view scenario': 'assumptions',
    'show supply chain graph': 'graph', 'open supply chain graph': 'graph',
    'show supply-chain graph': 'graph', 'open supply-chain graph': 'graph',
    'show financial overview': 'overview', 'open financial overview': 'overview',
    'show financial impact': 'impact', 'open financial impact': 'impact',
    'show price forecasts': 'forecast', 'open forecasts': 'forecast',
}
# These bare acronyms are ambiguous financial/technical concepts. Let the model
# interpret them instead of treating every uppercase token as a ticker.
_AMBIGUOUS_ACRONYMS = frozenset({'SEC', 'USD', 'EUR', 'COGS', 'EBIT', 'EPS', 'ROE', 'ROA',
                                'WACC', 'NAV', 'KPI', 'LLM', 'RAG', 'LSTM', 'GBM', 'AI'})
_SCENARIO_NOTICE = 'Review the scenario controls to set assumptions and run a calculation. This navigation request has not changed any inputs.'
_PLANNER_INSTRUCTIONS = '''Interpret this conversational request in the existing financial workspace. Plan only; do not execute actions.
Return JSON with these fields:
{"action":"prepare|navigate|explain|clarify", "company_query":string|null,
 "section":"overview|exposures|materials|assumptions|graph|impact|forecast"|null,
 "question":string, "clarification":string|null,
 "answer_kind":"general|company|scenario|forecast|workflow", "retrieval_query":string|null,
 "company_search_requested":bool, "company_mentions":[string]}.

Understand meaning and the supplied recent conversation, not keyword matching:
- General education such as 'explain to me the financial ratios' is explain/general,
  section null, no company_query and no retrieval_query. It needs no selected company or filing.
- 'What does this mean?' or 'So is that good?' refers to the recent scoped exchange.
  Continue its subject; use general/company/scenario/forecast according to what the user means.
  If there is no referent, ask a short clarification. Do not interpret those words as an issuer.
- 'How do I use this?' is explain/workflow; explain the next useful step, without requiring company evidence.
- 'Explain reported margins' means explain/company/overview with a filing retrieval query.
  'Explain my simulated margin change' means explain/scenario/impact using the saved run.
  Check scenario_ready before referring to a saved result; if false, clarify that there
  is no run to explain and guide the user to the next step. scenario_inputs_current=false
  means a saved run may be stale. selected_materials and forecast_materials describe
  what the existing workspace has actually prepared; do not invent missing outputs.
- A question can sound conceptual while asking about this company's disclosed inputs or
  accounting. Use explain/company with a targeted retrieval_query when it concerns a
  material in the active workspace and a specific treatment, balance, equipment holding,
  operating use, or proposed interpretation of that disclosure. Explain the concept using
  the current report's actual treatment; generic industry practice must not override it.
  For example, distinguishing a selected material's equipment inventory from annual
  procurement spend requires its disclosed accounting/use evidence, not an assumption
  that equipment is depreciated or that its carrying value is consumed each year.
  A standalone definition such as 'What does hedging mean?' is explain/general with no
  retrieval, even when a company is active. Explicit requests for a general concept stay
  general; merely having materials selected does not make every question company-specific.
- 'Show top exposures' opens exposures for the current company. Top, ratios, financial,
  details and similar words are not company names.
- Business comparisons are questions about evidence or scenarios, never evaluation-panel requests.
- Merely mentioning a company in an educational example does not request company preparation.

Company lookup is a separate action. Set prepare and company_search_requested=true
ONLY when the user explicitly requests preparing, looking up, or analyzing a particular
named company in THIS message and that company is not already active. A request such as
'show me what TEVA most exposure materials' qualifies: company_query='TEVA', section exposures.
Copy the issuer name/ticker/CIK literally from the current message; never infer its identity
from a product, industry or the conversation. company_mentions lists distinct explicitly
named issuers in THIS message only. Multiple requested companies require clarification.
For an already active company, answer/navigate within its selected report; do not prepare it again.
For all non-prepare actions company_query=null and company_search_requested=false.

A question alone never authorizes changing assumptions or running a scenario. Requests to
apply a shock or change assumptions navigate to assumptions with answer_kind=scenario;
explain that the controls are the current execution interface. Never output amounts to apply,
tool names, code or arbitrary actions. Never silently switch reports. If a company-specific
answer lacks a selected company/report, ask for that missing context. General education and
workflow guidance remain available without either. Retrieval queries may resolve references
from the bounded conversation but remain within the selected company/report. Do not claim
that navigation, downloads, retrieval, calculations or quality checks have already happened.
'''


def _active(context):
    value = context.get('active_company') or {}
    return {'ticker': value} if isinstance(value, str) else value if isinstance(value, dict) else {}


def _same_company(query, company):
    if not isinstance(query, str):
        return False
    query = query.strip().lstrip('$')
    if not query:
        return False
    for key in ('ticker', 'name', 'title', 'cik'):
        alias = str(company.get(key) or '').strip()
        if not alias:
            continue
        if query.casefold() == alias.casefold():
            return True
        if query.isdigit() and alias.isdigit() and int(query) == int(alias):
            return True
    return False


def _report(context):
    value = context.get('report') or {}
    if isinstance(value, str):
        return {'accession': value}
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in ('accession', 'accessionNumber', 'form', 'period_end', 'reportDate', 'currency') if value.get(key) is not None}


def _has_report(context):
    report = _report(context)
    return bool(report.get('accession') or report.get('accessionNumber'))


def _bounded_context(context):
    """Keep recent dialogue, without mixing explicitly different filing scopes."""
    company = _active(context)
    report = _report(context)
    ticker = company.get('ticker')
    accession = report.get('accession', report.get('accessionNumber'))
    history = context.get('conversation', [])
    conversation = []
    if isinstance(history, list):
        for item in history[-12:]:
            if not isinstance(item, dict) or item.get('role') not in ('user', 'assistant'):
                continue
            scope = item.get('scope') or {}
            if not isinstance(scope, dict):
                continue
            if scope.get('ticker') and str(scope['ticker']).casefold() != str(ticker).casefold():
                continue
            if scope.get('accession') and scope['accession'] != accession:
                continue
            content = item.get('text', item.get('content'))
            if isinstance(content, str) and content.strip():
                conversation.append({'role': item['role'], 'content': content[:1800]})
    rows = context.get('materials', context.get('material_names', [])) or []
    materials = [row.get('material') if isinstance(row, dict) else row for row in rows[:30]] if isinstance(rows, list) else []
    def names(key):
        values = context.get(key, [])
        return [value[:120] for value in values[:30] if isinstance(value, str)] if isinstance(values, (list, tuple)) else []
    previous = context.get('previous_section', context.get('section'))
    return {'active_company': {key: company[key] for key in ('ticker', 'name', 'title', 'cik') if company.get(key)},
            'report': report, 'materials': [name[:120] for name in materials if isinstance(name, str)],
            'selected_materials': names('selected_materials'), 'forecast_materials': names('forecast_materials'),
            'scenario_ready': context.get('scenario_ready') is True,
            'scenario_inputs_current': context.get('scenario_inputs_current') is True,
            'previous_section': previous if previous in SECTIONS else None,
            'conversation': conversation[-8:]}


def _result(question, action='clarify', section=None, company=None, clarification=None,
            answer_kind='workflow', retrieval_query=None):
    return {'action': action, 'company_query': company, 'section': section,
            'question': question, 'clarification': clarification,
            'answer_kind': answer_kind, 'retrieval_query': retrieval_query}


def is_sec_refresh_request(question):
    """Only this exact existing-company command permits the controller to refresh."""
    return isinstance(question, str) and bool(re.fullmatch(
        r'(?:please\s+)?(?:refresh\s+(?:the\s+)?sec\s+filings|check\s+sec\s+for\s+new\s+filings)[.!?]?',
        question.strip(), re.I))


def _direct_identifier(question):
    # Unpadded numeric CIKs require the explicit CIK label, so an isolated number
    # in a conversation about assumptions does not become a company lookup.
    cik = re.fullmatch(r'(?:CIK\s*[:#=]?\s*(\d{1,10})|(\d{10}))', question, re.I)
    if cik:
        return cik.group(1) or cik.group(2)
    ticker = re.fullmatch(r'(?:\$([A-Za-z]{1,5}(?:[.-][A-Za-z])?)|([A-Z]{2,5}(?:[.-][A-Z])?))', question)
    if ticker:
        value = ticker.group(1) or ticker.group(2)
        if ticker.group(1) or value not in _AMBIGUOUS_ACRONYMS:
            return value
    return None


def _literal_company(query, question):
    return (isinstance(query, str) and 1 <= len(query.strip()) <= 120
            and query == query.strip() and all(char.isprintable() for char in query)
            and any(char.isalnum() for char in query)
            and bool(re.search(r'(?<![A-Za-z0-9])' + re.escape(query.lstrip('$')) + r'(?![A-Za-z0-9])', question, re.I)))


def _validate_plan(proposed, question, context):
    if not isinstance(proposed, dict) or not _REQUIRED <= set(proposed) or not set(proposed) <= _REQUIRED | _OPTIONAL:
        raise ValueError('Invalid intent schema')
    action, kind, section = proposed['action'], proposed['answer_kind'], proposed['section']
    if action not in ACTIONS or kind not in ANSWER_KINDS or (section is not None and section not in SECTIONS):
        raise ValueError('Unsupported action or answer kind')
    if not isinstance(proposed['question'], str):
        raise ValueError('Invalid question')
    company, clarification = proposed['company_query'], proposed['clarification']
    retrieval = proposed.get('retrieval_query')
    if clarification is not None and (not isinstance(clarification, str) or len(clarification) > 700):
        raise ValueError('Invalid clarification')
    if retrieval is not None and (not isinstance(retrieval, str) or not retrieval.strip() or len(retrieval) > 1500):
        raise ValueError('Invalid retrieval query')
    if 'company_search_requested' in proposed and type(proposed['company_search_requested']) is not bool:
        raise ValueError('Invalid company-search request')
    active = _active(context)
    if company is not None and not (_same_company(company, active) or _literal_company(company, question)):
        raise ValueError('Company was not explicitly supplied')
    mentions = proposed.get('company_mentions', [])
    if not isinstance(mentions, list) or len(mentions) > 8 or any(not _literal_company(name, question) for name in mentions):
        raise ValueError('Invalid company mentions')
    distinct = {('active' if _same_company(name, active) else name.casefold()) for name in mentions}
    if action == 'prepare':
        if kind != 'company' or proposed.get('company_search_requested') is not True or not _literal_company(company, question):
            raise ValueError('Company preparation requires an explicit company-search request')
        if len(distinct) > 1:
            return _result(question, clarification='This workspace analyzes one company at a time. Which company would you like to explore?')
        if _same_company(company, active):
            return _result(question, 'navigate', section or 'overview', answer_kind='company')
        return _result(question, 'prepare', section or 'overview', company, clarification, 'company', retrieval)
    if company is not None and not _same_company(company, active):
        raise ValueError('A question cannot implicitly prepare a different company')
    if action == 'clarify':
        return _result(question, clarification=clarification or 'What would you like me to explain or help you do?', answer_kind=kind)
    if action == 'navigate' and section is None:
        raise ValueError('Navigation needs a canvas destination')
    if (kind in ('company', 'scenario', 'forecast') or action == 'navigate') and not active:
        return _result(question, clarification='Which company should I use for that analysis? General financial explanations are also available without selecting a company.')
    if action == 'explain' and kind in ('company', 'scenario', 'forecast') and not _has_report(context):
        return _result(question, clarification='Select an analysis report first so I can explain this company’s evidence or saved results.')
    if kind in ('general', 'workflow'):
        retrieval = None
        if action == 'explain':
            section = None
    if action == 'navigate' and section == 'assumptions':
        clarification = _SCENARIO_NOTICE
    return _result(question, action, section, clarification=clarification, answer_kind=kind, retrieval_query=retrieval)


def parse_workspace_intent(question, context, language=None):
    """Interpret normal prose with the model; return only validated UI actions.

    The previous fields plus ``answer_kind`` and ``retrieval_query`` are returned.
    General education/workflow answers may have no section
    or selected report. ``conversation`` in context carries recent scoped turns.
    Model-only company-search fields are validated and then removed from output.
    This function never calls SEC or modifies the supplied workspace context.
    """
    if not isinstance(question, str) or not question.strip() or len(question) > 8000:
        raise ValueError('Enter a request of 1–8000 characters.')
    if not isinstance(context, dict):
        raise ValueError('Workspace context must be a dictionary.')
    question = question.strip()
    active = _active(context)
    if is_sec_refresh_request(question):
        if not active.get('ticker'):
            return _result(question, clarification='Choose a company first, then I can check SEC for new filings.')
        previous = context.get('previous_section', context.get('section'))
        return _result(question, 'prepare', previous if previous in SECTIONS else 'overview',
                       str(active['ticker']), answer_kind='company')
    identifier = _direct_identifier(question)
    if identifier:
        return _result(question, 'navigate' if _same_company(identifier, active) else 'prepare',
                       'overview', None if _same_company(identifier, active) else identifier, answer_kind='company')
    section = _NAVIGATION.get(question.casefold().rstrip('.!?'))
    if section:
        if not active:
            return _result(question, clarification='Which company should I use? Enter its name, ticker or CIK to open its analysis.')
        kind = 'scenario' if section in ('assumptions', 'impact') else 'forecast' if section == 'forecast' else 'company'
        return _result(question, 'navigate', section, answer_kind=kind,
                       clarification=_SCENARIO_NOTICE if section == 'assumptions' else None)
    if language is not None:
        try:
            proposed = language.json(_PLANNER_INSTRUCTIONS, {'question': question, 'context': _bounded_context(context)})
            return _validate_plan(proposed, question, context)
        except Exception:
            # Fail closed on actions, without treating an ordinary phrase as a
            # company name or leaking provider exception details into the UI.
            pass
    return _result(question, clarification='I could not reliably interpret this message just now. Please retry your question, or use an exact workspace command such as “Show exposures”.')
