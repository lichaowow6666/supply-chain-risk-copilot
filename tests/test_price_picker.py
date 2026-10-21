"""Focused checks for chart prices flowing into the existing shock contract."""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock, patch

from pe_copilot.ui.price_picker import apply_choice, render_price_picker


class ContinuousPriceTests(TestCase):
    def test_arbitrary_positive_price_is_not_clamped_to_forecast_endpoints(self):
        state = {'picker': {'price': 122.91912345}}
        with patch('pe_copilot.ui.price_picker.st.session_state', state):
            apply_choice('picker', 'target', 'shock', 100.)
        self.assertEqual(state['target'], 122.91912345)
        self.assertAlmostEqual(state['shock'], 22.91912345)

    def test_invalid_component_values_do_not_replace_the_existing_scenario(self):
        for value in (None, True, '120', float('nan'), float('inf'), -1., 0., .01):
            state = {'picker': {'price': value}, 'target': 110., 'shock': 10.}
            with patch('pe_copilot.ui.price_picker.st.session_state', state):
                apply_choice('picker', 'target', 'shock', 100.)
            self.assertEqual((state['target'], state['shock']), (110., 10.))

    def test_combined_chart_preserves_horizon_target_and_model_independent_key(self):
        row = {'label': 'Benchmark', 'path': [100., 100.], 'lower80': [98., 96.], 'upper80': [102., 104.]}
        forecast = {
            'forecast_id': 'synthetic', 'horizon': 2, 'reference_price': 100.,
            'market': {'unit': 'USD / unit'},
            'history': [{'date': '2026-09-20', 'price': 99.}, {'date': '2026-09-21', 'price': 100.}],
            'future_dates': ['2026-09-22', '2026-09-23'],
            'models': {'first': row, 'second': deepcopy(row)},
        }
        state = {'target': 250., 'shock': 150.}
        renderer = Mock()
        with patch('pe_copilot.ui.price_picker.st.session_state', state), patch(
                'pe_copilot.ui.price_picker.component', return_value=renderer):
            render_price_picker(forecast, 'first', 'target', 'shock', 'same_scope')
            render_price_picker(forecast, 'second', 'target', 'shock', 'same_scope')
        first, second = [call.kwargs for call in renderer.call_args_list]
        self.assertEqual(first['key'], second['key'])
        self.assertEqual(second['data']['horizon'], 2)
        self.assertEqual(second['data']['futureStart'], '2026-09-21')
        self.assertEqual(second['data']['futureEnd'], '2026-09-23')
        self.assertEqual(second['data']['price'], 250.)
        self.assertGreater(second['data']['figure']['layout']['yaxis']['range'][1], 250.)
        self.assertEqual(state, {'target': 250., 'shock': 150.})
