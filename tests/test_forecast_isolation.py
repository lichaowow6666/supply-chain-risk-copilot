"""Forecast model failures must remain ordinary, recoverable application errors."""
import json
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from pe_copilot.agents.forecasting_agent import ForecastRequest, build_forecast, forecast_path
from pe_copilot.agents.lstm_forecast import forecast_lstm
from pe_copilot.native_runtime import NativeWorkerError


class ForecastIsolationTests(unittest.TestCase):
    def setUp(self):
        self.prices = np.exp(3 + np.cumsum(np.random.default_rng(17).normal(0, .01, 650)))
        self.dates = list(pd.bdate_range('2023-01-02', periods=len(self.prices)))

    def test_xgboost_payload_is_serializable_and_preserves_result_dtype(self):
        expected = np.asarray([3.25, 3.125, 3.], dtype='float32')
        worker = Mock()
        worker.call.return_value = {'path': expected.tolist(), 'dtype': str(expected.dtype)}
        with patch('pe_copilot.native_runtime.get_worker', return_value=worker) as factory:
            actual = forecast_path('xgboost', self.prices, self.dates, 3)
        factory.assert_called_once_with('forecast-xgboost', 'pe_copilot.agents.forecast_worker:xgboost')
        payload = worker.call.call_args.args[0]
        self.assertEqual(payload['dates'][0], self.dates[0].isoformat())
        self.assertEqual(payload['prices'], self.prices.tolist())
        self.assertEqual(json.loads(json.dumps(payload)), payload)
        self.assertEqual(worker.call.call_args.kwargs, {'timeout': 120})
        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(actual.dtype, expected.dtype)

    def test_lstm_uses_a_distinct_worker(self):
        worker = Mock()
        worker.call.return_value = {'path': [3., 3.1], 'dtype': 'float64'}
        with patch('pe_copilot.native_runtime.get_worker', return_value=worker) as factory:
            actual = forecast_lstm(self.prices, 2)
        factory.assert_called_once_with('forecast-lstm', 'pe_copilot.agents.forecast_worker:lstm')
        self.assertEqual(worker.call.call_args.args[0], {'prices': self.prices.tolist(), 'horizon': 2})
        np.testing.assert_array_equal(actual, [3., 3.1])

    def test_failed_native_models_leave_real_benchmarks_available(self):
        frame = pd.DataFrame({'Date': self.dates, 'Price': self.prices,
                              'Metal': 'Copper', 'Ticker': 'HG=F', 'Volume': 10})
        worker = Mock()
        worker.call.side_effect = NativeWorkerError('Worker stopped unexpectedly')
        with patch('pe_copilot.native_runtime.get_worker', return_value=worker), \
             patch('pe_copilot.agents.forecasting_agent.xgb_version', return_value='test'), \
             patch('pe_copilot.agents.forecasting_agent.torch_version', return_value='test'):
            result = build_forecast(ForecastRequest('Copper', 5), frame)
        self.assertEqual(set(result['models']), {'naive', 'ar'})
        self.assertIn(result['recommended'], {'naive', 'ar'})
        self.assertTrue(any('XGBoost' in warning and 'excluded' in warning for warning in result['warnings']))
        self.assertTrue(any('LSTM' in warning and 'excluded' in warning for warning in result['warnings']))
        self.assertEqual(worker.call.call_count, 2)


if __name__ == '__main__':
    unittest.main()
