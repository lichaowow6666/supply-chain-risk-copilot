"""Shared financial cascade; formulas match the existing deterministic scenario engine."""


def apply_financial_impact(baselines, net_cogs_absorbed):
    if 'COGS' in baselines.get('income_statement', {}) and baselines['income_statement']['COGS']+net_cogs_absorbed < 0:
        raise ValueError('Assumed savings exceed reported COGS; negative COGS is unsupported.')
    inc = baselines.get('income_statement', {})
    bal = baselines.get('balance_sheet', {})
    cashflow = baselines.get('cash_flow', {})
    income_result, balance_result, cash_result = {}, {}, {}
    def row(base, stressed, delta):
        return {'base':base, 'stressed':round(stressed,2), 'delta':delta}
    for metric, base in inc.items():
        if metric == 'Revenue':
            income_result[metric] = {'base':base,'stressed':base,'delta':0.0}
        elif metric == 'COGS':
            income_result[metric] = row(base,base+net_cogs_absorbed,net_cogs_absorbed)
        elif metric in ('Gross Profit','EBITDA','Operating Income'):
            income_result[metric] = row(base,base-net_cogs_absorbed,-net_cogs_absorbed)
        elif metric == 'Net Income':
            hit = net_cogs_absorbed - net_cogs_absorbed*.21
            income_result[metric] = row(base,base-hit,-round(hit,2))
    if 'Cash & Equivalents' in bal:
        cash = bal['Cash & Equivalents']
        balance_result['Cash & Equivalents'] = row(cash,max(0.0,cash-net_cogs_absorbed),-min(cash,net_cogs_absorbed))
    for metric in ('Working Capital','Total Assets','Total Equity'):
        if metric in bal:
            balance_result[metric] = row(bal[metric],bal[metric]-net_cogs_absorbed,-net_cogs_absorbed)
    if 'Total Debt' in bal and 'Cash & Equivalents' in bal:
        debt_increase = max(0.0,net_cogs_absorbed-bal['Cash & Equivalents']*.5)
        stressed_debt = bal['Total Debt']+debt_increase
        balance_result['Total Debt'] = row(bal['Total Debt'],stressed_debt,round(debt_increase,2))
        if 'Net Debt' in bal:
            balance_result['Net Debt'] = row(bal['Net Debt'],stressed_debt-max(0.0,bal['Cash & Equivalents']-net_cogs_absorbed),round(net_cogs_absorbed,2))
    for metric in ('Operating Cash Flow','Free Cash Flow','CapEx'):
        if metric in cashflow:
            hit = 0.0 if metric == 'CapEx' else net_cogs_absorbed
            cash_result[metric] = row(cashflow[metric],cashflow[metric]-hit,-hit) if metric != 'CapEx' else {'base':cashflow[metric],'stressed':cashflow[metric],'delta':0.0}
    return {'income_statement':income_result,'balance_sheet':balance_result,'cash_flow':cash_result}
