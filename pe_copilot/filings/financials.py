"""Deterministic inline XBRL extraction. No LLM numerical calculations or defaults."""
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re

from bs4 import BeautifulSoup

# Only standard tags with specified meanings feed the existing financial model.
METRICS = {
    'Revenue': ('RevenueFromContractWithCustomerExcludingAssessedTax', 'SalesRevenueNet', 'Revenues', 'SalesRevenueGoodsNet'),
    'COGS': ('CostOfRevenue', 'CostOfGoodsAndServicesSold', 'CostOfGoodsSold'),
    'Gross Profit': ('GrossProfit',),
    'Operating Income': ('OperatingIncomeLoss',),
    'Net Income': ('NetIncomeLoss', 'ProfitLoss'),
    'Cash & Equivalents': ('CashAndCashEquivalentsAtCarryingValue',),
    'Total Assets': ('Assets',),
    'Total Equity': ('StockholdersEquity',),
    'Operating Cash Flow': ('NetCashProvidedByUsedInOperatingActivities',),
    'CapEx': ('PaymentsToAcquirePropertyPlantAndEquipment', 'PaymentsToAcquireProductiveAssets', 'PaymentsToAcquireMachineryAndEquipment'),
    'Current Assets': ('AssetsCurrent',),
    'Current Liabilities': ('LiabilitiesCurrent',),
    'Current Debt': ('LongTermDebtCurrent',),
    'Noncurrent Debt': ('LongTermDebtNoncurrent',),
    'Short-term Borrowings': ('ShortTermBorrowings',),
    'Total Debt': ('LongTermDebtAndFinanceLeaseObligationsIncludingCurrentMaturities',),
    'D&A': ('DepreciationDepletionAndAmortization', 'DepreciationDepletionAndAmortizationPropertyPlantAndEquipment'),
    'Interest Expense': ('InterestExpense', 'InterestAndDebtExpense', 'InterestExpenseDebt'),
    'Income Tax Expense': ('IncomeTaxExpenseBenefit',),
}
POOLS = {
    'income_statement': ('Revenue', 'COGS', 'Gross Profit', 'EBITDA', 'Operating Income', 'Net Income'),
    'balance_sheet': ('Cash & Equivalents', 'Working Capital', 'Total Debt', 'Net Debt', 'Total Assets', 'Total Equity'),
    'cash_flow': ('Operating Cash Flow', 'CapEx', 'Free Cash Flow'),
}
INSTANT = {'Cash & Equivalents','Total Assets','Total Equity','Current Assets','Current Liabilities','Current Debt','Noncurrent Debt','Short-term Borrowings','Total Debt'}


def local(tag):
    return tag.name.split(':')[-1].lower()


def content(tag, name):
    match = tag.find(lambda child: local(child) == name.lower())
    return match.get_text(strip=True) if match else None


def read_inline_facts(path, cik):
    soup = BeautifulSoup(Path(path).read_bytes(), 'html.parser')
    contexts, units = {}, {}
    for tag in soup.find_all(lambda t: local(t) == 'context'):
        identifier = content(tag, 'identifier')
        if not identifier or not identifier.isdigit() or identifier.zfill(10) != cik:
            continue
        instant = content(tag, 'instant')
        dimensions = {t.get('dimension', ''): t.get_text(strip=True) for t in tag.find_all(lambda c: local(c) in ('explicitmember','typedmember'))}
        contexts[tag.get('id')] = {'start': content(tag, 'startdate') or instant,
                                   'end': content(tag, 'enddate') or instant, 'instant': bool(instant), 'dimensions': dimensions}
    for tag in soup.find_all(lambda t: local(t) == 'unit'):
        # Compound units (per share, ratios) are not monetary financial baselines.
        measures = tag.find_all(lambda c: local(c) == 'measure')
        if len(measures) == 1:
            units[tag.get('id')] = measures[0].get_text(strip=True).split(':')[-1]
    facts, seen = [], set()
    for tag in soup.find_all(lambda t: local(t) == 'nonfraction'):
        context = contexts.get(tag.get('contextref'))
        unit = units.get(tag.get('unitref'))
        if not context or not context['end'] or not context['start'] or unit != 'USD':
            continue
        name = tag.get('name', '')
        text = tag.get_text('', strip=True).replace('\u2212', '-')
        if str(tag.get('xsi:nil', '')).lower() == 'true':
            continue
        try:
            token = re.sub(r'[,\s$]', '', text)
            if token in ('—','–','-'):
                if not any(z in tag.get('format','').lower() for z in ('zerodash','fixed-zero')):
                    continue
                token = '0'
            if token.startswith('(') and token.endswith(')'):
                token = '-' + token[1:-1]
            number = Decimal(token)
            if tag.get('sign') == '-':
                number = -abs(number)
            value = number * (Decimal(10) ** int(tag.get('scale', '0')))
            if not value.is_finite():
                continue
        except (InvalidOperation, ValueError):
            continue
        fact = {**context, 'tag': name, 'value': str(value), 'unit': unit, 'raw_text': text,
                'context_id': tag.get('contextref'), 'scale': tag.get('scale', '0'), 'sign': tag.get('sign', '')}
        key = (name, context['start'], context['end'], tuple(sorted(context['dimensions'].items())), str(value))
        if key not in seen:
            facts.append(fact); seen.add(key)
    return facts


def baseline_from_facts(facts, period_end, accession=None, form="10-K"):
    """Select annual or quarterly YTD flows and period-end balances; expose missing/ambiguous metrics.

    EBITDA is derived only with the full net-income + taxes + interest + D&A
    bridge. No operating-EBITDA proxy or adjusted company measure is substituted.
    """
    from datetime import date
    values, evidence, warnings = {}, {}, []
    quarterly = form.startswith('10-Q')
    # Quarterly statements report cash flows YTD. Use a consistent YTD duration
    # for the stress pools instead of combining three-month income with YTD cash.
    starts = [f['start'] for f in facts if not f['instant'] and not f['dimensions']
              and f['end'] == period_end and f['tag'].split(':')[-1] in METRICS['Revenue']
              and 60 <= (date.fromisoformat(f['end'])-date.fromisoformat(f['start'])).days <= 300]
    duration_start = min(starts) if quarterly and starts else None
    for metric, tags in METRICS.items():
        for tag in tags:
            candidates = [f for f in facts if f['tag'].lower() == ('us-gaap:'+tag).lower()
                          and not f['dimensions'] and f['end'] == period_end
                          and (f['instant'] if metric in INSTANT else not f['instant'] and
                               ((f['start'] == duration_start) if quarterly else
                                330 <= (date.fromisoformat(f['end'])-date.fromisoformat(f['start'])).days <= 380))]
            if not candidates:
                continue
            unique = {(f['start'],f['value']) for f in candidates}
            if len(unique) != 1:
                warnings.append(f'{metric}: conflicting selected-period XBRL contexts; omitted.')
                break
            chosen = candidates[0]
            values[metric] = Decimal(chosen['value']) / Decimal(1000000)
            evidence[metric] = {'basis':'reported','tag':chosen['tag'],'start':chosen['start'],'end':chosen['end'],
                                'value':str(values[metric]),'unit':'USD millions','accession':accession,'context_id':chosen['context_id']}
            break
    def derive(metric, inputs, operation, definition):
        if metric not in values and all(key in values for key in inputs):
            periods = {(evidence[k]['start'], evidence[k]['end']) for k in inputs}
            if len(periods) != 1:
                return
            values[metric] = operation(*(values[k] for k in inputs))
            evidence[metric] = {'basis':'derived','inputs':list(inputs),'definition':definition,
                                'start':next(iter(periods))[0],'end':period_end,'value':str(values[metric]),'unit':'USD millions','accession':accession}
    derive('Gross Profit', ('Revenue','COGS'), lambda a,b:a-b, 'Revenue minus cost of sales')
    derive('Working Capital', ('Current Assets','Current Liabilities'), lambda a,b:a-b, 'Current assets minus current liabilities')
    # Component debt aggregation requires all three reported components; absence is not zero.
    derive('Total Debt', ('Current Debt','Noncurrent Debt','Short-term Borrowings'), lambda a,b,c:a+b+c,
           'Current long-term debt + noncurrent long-term debt + short-term borrowings')
    derive('Net Debt', ('Total Debt','Cash & Equivalents'), lambda a,b:a-b, 'Total debt minus cash and equivalents')
    derive('Free Cash Flow', ('Operating Cash Flow','CapEx'), lambda a,b:a-b, 'Operating cash flow minus payments to acquire PP&E')
    derive('EBITDA', ('Net Income','Income Tax Expense','Interest Expense','D&A'), lambda a,b,c,d:a+b+c+d,
           'Net income + income tax expense + interest expense + depreciation/depletion/amortization; unadjusted bridge')
    pools = {pool:{key:float(values[key]) for key in keys if key in values} for pool,keys in POOLS.items()}
    missing = [key for keys in POOLS.values() for key in keys if key not in values]
    if missing:
        warnings.append('Not available on a comparable reported basis: '+', '.join(missing)+'. No example values were substituted.')
    return {'pools':pools,'evidence':evidence,'warnings':warnings,'period_end':period_end,'accession':accession,'currency':'USD','unit':'millions','form':form,'duration_basis':'Year to date' if quarterly else 'Annual',
            'duration_start':duration_start if quarterly else next((r['start'] for k,r in evidence.items() if k not in INSTANT),None)}


def fact_projection(facts, filing, cik):
    """Auditable local projection, retaining original tag/context/sign/scale metadata."""
    return {'projection_version':'inline-xbrl-v1','source_url':filing['url'],'source_sha256':filing['sha256'],
            'company_cik':cik,'accession':filing['accessionNumber'],'facts':facts}
