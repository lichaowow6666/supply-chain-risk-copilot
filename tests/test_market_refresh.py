"""Synthetic offline fixtures: refresh integrity, daily gating and data quality."""
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import duckdb
import pandas as pd
from pe_copilot.market_data import refresh_prices
from pe_copilot.agents.market_agent import MarketAgent


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name)/'prices.duckdb')
        self.catalog = {'Natural Gas': ('NG=F', 'USD/MMBtu')}

    def frame(self, prices, dates):
        return pd.DataFrame({'Close': prices, 'Volume': [10]*len(prices)}, index=pd.DatetimeIndex(dates, name='Date', tz='America/New_York'))

    def refresh(self, fn, day=date(2026,9,22), force=False):
        return refresh_prices(self.db, force=force, downloader=fn, today=day, catalog=self.catalog)

    def rows(self):
        with duckdb.connect(self.db) as db:
            return db.execute('SELECT Date,Price FROM futures_prices ORDER BY Date').fetchall()

    def test_daily_gate_overlap_update_precision_and_preservation(self):
        fn=Mock(return_value=self.frame([2.12345,2.25], ['2026-09-18','2026-09-21']))
        self.refresh(fn); self.refresh(fn)
        self.assertEqual(fn.call_count,1)
        fn.return_value=self.frame([2.3,2.45], ['2026-09-21','2026-09-22'])
        self.refresh(fn,date(2026,9,23))
        self.assertEqual([r[1] for r in self.rows()],[2.12345,2.3,2.45])
        self.assertEqual(fn.call_args.args[1],date(2026,9,14))

    def test_failure_keeps_history_and_force_retries(self):
        fn=Mock(return_value=self.frame([3],['2026-09-21']))
        self.refresh(fn)
        fn.side_effect=RuntimeError('do not persist credentials')
        result=self.refresh(fn,date(2026,9,23))
        self.assertEqual(len(self.rows()),1)
        self.assertIn('failed:RuntimeError',result['warnings'][0])
        self.assertNotIn('credentials',str(result))
        self.refresh(fn,date(2026,9,23));self.assertEqual(fn.call_count,2)
        self.refresh(fn,date(2026,9,23),True);self.assertEqual(fn.call_count,3)

    def test_invalid_provisional_and_duplicate_rows_filtered(self):
        fn=Mock(return_value=self.frame([2,3,-1,float('nan'),4,5],['2026-09-17','2026-09-17','2026-09-18','2026-09-19','2026-09-21','2026-09-22']))
        self.refresh(fn)
        self.assertEqual([r[1] for r in self.rows()],[3,4])

    def test_new_contract_stale_history_and_regression(self):
        fn=Mock(return_value=self.frame([2],['2026-08-01']))
        self.assertTrue(self.refresh(fn)['contracts'][0]['stale'])
        fn.return_value=self.frame([1],['2026-07-01'])
        self.assertTrue(self.refresh(fn,force=True)['warnings'])
        self.assertEqual(self.rows()[0][1],2)

    def test_partial_failure_does_not_block_other_contract(self):
        fn=Mock(side_effect=[RuntimeError(),self.frame([4],['2026-09-21'])])
        result=refresh_prices(self.db,downloader=fn,today=date(2026,9,22),catalog={'Copper':('HG=F','USD/lb'),**self.catalog})
        self.assertEqual(len(result['warnings']),1)
        self.assertEqual(self.rows()[0][1],4)

    def test_platinum_and_crude_oil_reads_can_overlap_market_refresh(self):
        self.catalog = {'Platinum': ('PL=F', 'USD/troy oz'),
                        'Crude Oil': ('CL=F', 'USD/barrel')}
        provider = Mock(return_value=self.frame([90, 100], ['2026-09-18', '2026-09-21']))
        self.refresh(provider)
        agent = MarketAgent(self.db)
        # Reproduces the configuration collision deterministically: a refresher
        # connection is still open when the scenario requests both histories.
        with duckdb.connect(self.db):
            histories = agent.capture_price_snapshot(('Platinum', 'Crude Oil'))
            self.assertEqual(set(histories), set(self.catalog))
            self.assertEqual(set(agent.get_available_commodities()), set(self.catalog))
            self.assertEqual(len(agent.fetch_price_history('Platinum')), 2)
        # Also exercise the reverse order: a reader is active when refresh opens.
        with agent.get_connection():
            self.assertFalse(self.refresh(provider, force=True)['warnings'])
        result = agent.calculate_multi_material_waterfall(
            {name: {'annual_spend': 10, 'unhedged_pct': .8, 'shock_fraction': .1}
             for name in self.catalog}, .25,
            {'income_statement': {'COGS': 100, 'Revenue': 200, 'Operating Income': 30}},
            price_histories=histories)
        self.assertEqual(set(result['materials']), set(self.catalog))
        self.assertAlmostEqual(result['waterfall_summary']['Net_COGS_Absorbed'], 1.2)
