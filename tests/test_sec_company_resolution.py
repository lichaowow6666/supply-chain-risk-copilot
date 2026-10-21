"""Offline company-identity resolution using synthetic SEC catalog fixtures."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from pe_copilot.filings.sec import SECClient, atomic_json


class CompanyCIKResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.client = SECClient(session=Mock(), user_agent='Synthetic resolver test')
        self.catalog = {
            '0': {'ticker': 'SYNTA', 'cik_str': 123456, 'title': 'Synthetic Alpha Chemicals Inc.'},
            '1': {'ticker': 'SYNTB', 'cik_str': 654321, 'title': 'Synthetic Alpha Materials Inc.'},
        }
        self.client.get = Mock(return_value=self.catalog)

    def test_numeric_and_prefixed_cik_resolve_same_catalog_identity(self):
        for query in ('123456', '0000123456', 'CIK123456', 'cik 123456', ' CIK: 0000123456 '):
            with self.subTest(query=query):
                result = self.client.resolve(query, self.root)
                self.assertEqual(result, {'cik': '0000123456', 'ticker': 'SYNTA',
                                          'name': 'Synthetic Alpha Chemicals Inc.'})
        self.client.get.assert_called_once_with('https://www.sec.gov/files/company_tickers.json')

    def test_local_manifest_reuses_cik_without_network(self):
        company = {'cik': '0000123456', 'ticker': 'SYNTA', 'name': 'Synthetic Alpha Chemicals Inc.'}
        atomic_json(self.root / company['cik'] / 'manifest.json', {'company': company})
        for query in ('123456', 'CIK: 123456', 'synta', company['name']):
            with self.subTest(query=query):
                self.assertEqual(self.client.resolve(query, self.root), company)
        self.client.get.assert_not_called()

    def test_unknown_cik_does_not_fall_back_to_numeric_name_match(self):
        self.catalog['2'] = {'ticker': 'SYNTC', 'cik_str': 111111, 'title': 'Synthetic 999999 Holdings'}
        with self.assertRaisesRegex(ValueError, 'No SEC-listed company matched this CIK'):
            self.client.resolve('999999', self.root)

    def test_invalid_cik_rejected_before_catalog_access(self):
        for query in ('0', '0000000000', '12345678901', 'CIK: 12345678901', 'CIK abc',
                      'CIK: ../123456', 'CIK -1', 'CIK', '１２３４５６'):
            with self.subTest(query=query), self.assertRaisesRegex(ValueError, 'valid CIK'):
                self.client.resolve(query, self.root)
        self.client.get.assert_not_called()

    def test_cik_with_multiple_share_classes_requires_ticker_choice(self):
        self.catalog['2'] = {'ticker': 'SYNTA-B', 'cik_str': 123456, 'title': 'Synthetic Alpha Chemicals Inc.'}
        with self.assertRaisesRegex(ValueError, 'CIK matches multiple listed tickers') as caught:
            self.client.resolve('123456', self.root)
        self.assertIn('SYNTA-B', str(caught.exception))
        self.assertEqual(self.client.resolve('SYNTA-B', self.root)['cik'], '0000123456')

    def test_name_and_ticker_resolution_keep_existing_ambiguity_rules(self):
        self.assertEqual(self.client.resolve('synta', self.root)['cik'], '0000123456')
        self.assertEqual(self.client.resolve('Synthetic Alpha Materials Inc', self.root)['ticker'], 'SYNTB')
        with self.assertRaisesRegex(ValueError, 'Company name is ambiguous'):
            self.client.resolve('Synthetic Alpha', self.root)

    def test_invalid_generic_query_has_actionable_input_message(self):
        for query in (None, '', ' ' * 3, 'a' * 161):
            with self.subTest(query=query), self.assertRaisesRegex(ValueError, 'name, ticker or CIK'):
                self.client.resolve(query, self.root)
        self.client.get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
