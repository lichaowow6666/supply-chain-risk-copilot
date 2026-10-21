"""Synthetic selected-filing ratios with unit, provenance and period safeguards."""
from copy import deepcopy
import json
import unittest

from pe_copilot.chat.financial_context import build_financial_context
from pe_copilot.filings.financials import baseline_from_facts


def fixture(quarterly=False):
    end = '2025-06-30' if quarterly else '2025-12-31'
    def fact(tag, value, instant=False):
        return {'tag': 'us-gaap:' + tag, 'value': str(value * 1000000), 'start': end if instant else '2025-01-01',
                'end': end, 'instant': instant, 'dimensions': {}, 'context_id': 'synthetic:' + tag}
    return baseline_from_facts([
        fact('Revenues', 200), fact('CostOfRevenue', 120), fact('OperatingIncomeLoss', 30),
        fact('NetIncomeLoss', -10), fact('NetCashProvidedByUsedInOperatingActivities', 50),
        fact('AssetsCurrent', 100, True), fact('LiabilitiesCurrent', 40, True),
        fact('LongTermDebtAndFinanceLeaseObligationsIncludingCurrentMaturities', 60, True),
        fact('StockholdersEquity', 80, True),
    ], end, 'synthetic-selected-report', '10-Q' if quarterly else '10-K')


class FinancialContextTests(unittest.TestCase):
    def ratios(self, baseline):
        return {item['name']: item for item in build_financial_context(baseline)['ratios']}

    def test_ratios_use_real_baseline_contract_including_evidence_only_balances(self):
        baseline = fixture()
        original = deepcopy(baseline)
        self.assertNotIn('Current Assets', baseline['pools']['balance_sheet'])
        result = build_financial_context(baseline)
        ratios = {item['name']: item for item in result['ratios']}
        expected = {'Gross margin': 40, 'Operating margin': 15, 'Net margin': -5,
                    'Current ratio': 2.5, 'Debt-to-equity': .75, 'Operating cash flow margin': 25}
        for name, value in expected.items():
            self.assertEqual(ratios[name]['status'], 'available', ratios[name])
            self.assertAlmostEqual(ratios[name]['value'], value)
            self.assertEqual(ratios[name]['provenance']['basis'], 'deterministic derivation')
        self.assertEqual(result['financial_figures']['Gross Profit']['provenance']['basis'], 'derived')
        self.assertEqual(result['financial_figures']['Revenue']['provenance']['basis'], 'reported')
        self.assertEqual(result['financial_figures']['Working Capital']['value'], 60)
        self.assertEqual(result['financial_figures']['Working Capital']['provenance']['start'], '2025-12-31')
        self.assertEqual(baseline, original)
        json.dumps(result, allow_nan=False)

    def test_quarterly_flows_stay_ytd_without_annualization(self):
        result = build_financial_context(fixture(True))
        self.assertEqual(result['report']['flow_basis'], 'year-to-date')
        self.assertFalse(result['report']['annualized'])
        self.assertEqual(result['financial_figures']['Revenue']['value'], 200)
        ratio = next(row for row in result['ratios'] if row['name'] == 'Operating cash flow margin')
        self.assertEqual(ratio['value'], 25)
        self.assertEqual(ratio['provenance']['period_start'], '2025-01-01')
        self.assertEqual(ratio['provenance']['period_end'], '2025-06-30')

    def test_inconsistent_units_accession_and_periods_withhold_only_affected_ratios(self):
        for key, value in (('unit', 'USD'), ('currency', 'EUR'), ('accession', 'another-report'), ('start', '2025-04-01'), ('end', '2024-12-31')):
            baseline = fixture(True)
            baseline['evidence']['Operating Cash Flow'][key] = value
            ratios = self.ratios(baseline)
            self.assertIsNone(ratios['Operating cash flow margin']['value'])
            self.assertEqual(ratios['Operating cash flow margin']['status'], 'unavailable')
            self.assertEqual(ratios['Operating margin']['value'], 15)
        for key, value in (('unit', 'dollars'), ('currency', 'EUR')):
            baseline = fixture()
            baseline[key] = value
            self.assertTrue(all(row['status'] == 'unavailable' for row in self.ratios(baseline).values()))

    def test_missing_evidence_and_unknown_provenance_never_become_reported_ratios(self):
        for change in ('missing', 'unknown', 'value mismatch'):
            baseline = fixture()
            if change == 'missing':
                del baseline['evidence']['Operating Income']
            elif change == 'unknown':
                baseline['evidence']['Operating Income']['basis'] = 'LLM guess'
            else:
                baseline['evidence']['Operating Income']['value'] = '999'
            result = build_financial_context(baseline)
            self.assertIsNone(next(row for row in result['ratios'] if row['name'] == 'Operating margin')['value'])
            if change != 'value mismatch':
                self.assertEqual(result['financial_figures']['Operating Income']['status'], 'unverified')
                self.assertEqual(result['financial_figures']['Operating Income']['provenance']['basis'], 'unknown')

    def test_nonpositive_denominators_and_missing_values_are_not_zero_filled(self):
        for metric, pool, name, value in (
                ('Revenue', 'income_statement', 'Operating margin', 0),
                ('Revenue', 'income_statement', 'Operating margin', -100),
                ('Total Equity', 'balance_sheet', 'Debt-to-equity', -20),
                ('Total Equity', 'balance_sheet', 'Debt-to-equity', 0)):
            baseline = fixture()
            baseline['pools'][pool][metric] = value
            baseline['evidence'][metric]['value'] = str(value)
            row = self.ratios(baseline)[name]
            self.assertIsNone(row['value'])
            self.assertEqual(row['status'], 'unavailable')
        baseline = fixture()
        del baseline['evidence']['Current Liabilities']
        self.assertIsNone(self.ratios(baseline)['Current ratio']['value'])

    def test_nonfinite_data_and_invalid_flow_scope_remain_serializable(self):
        for value in (float('nan'), float('inf'), True):
            baseline = fixture()
            baseline['pools']['income_statement']['Revenue'] = value
            result = build_financial_context(baseline)
            self.assertIsNone(result['financial_figures']['Revenue']['value'])
            json.dumps(result, allow_nan=False)
        baseline = fixture(True)
        baseline['duration_basis'] = 'Annual'
        rows = self.ratios(baseline)
        self.assertIsNone(rows['Gross margin']['value'])
        self.assertEqual(rows['Current ratio']['value'], 2.5)
        baseline = fixture()
        baseline['evidence']['Current Assets']['start'] = '2025-01-01'
        self.assertIsNone(self.ratios(baseline)['Current ratio']['value'])
        json.dumps(build_financial_context(None), allow_nan=False)


if __name__ == '__main__':
    unittest.main()
