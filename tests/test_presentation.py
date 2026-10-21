import unittest

import pandas as pd

from pe_copilot.ui.presentation import (
    available_discovered_materials,
    build_history_chart,
    build_kpi_card,
    build_stochastic_chart,
    build_waterfall_message,
)


class PresentationBuilderTests(unittest.TestCase):
    def test_available_discovered_materials_preserves_discovery_order(self):
        materials = [{"material": "copper"}, {"material": "gold"}, {"material": "tin"}]
        self.assertEqual(
            available_discovered_materials(materials, ["Gold", "Copper"]),
            ["Copper", "Gold"],
        )

    def test_waterfall_message_preserves_units_signs_and_assumptions(self):
        message = build_waterfall_message(
            {
                "Total_Gross_COGS_Surge": 12.345,
                "Pass_Through_Rate_Pct": 75.0,
                "Passed_To_Clients": 9.26,
                "Net_COGS_Absorbed": 3.09,
            },
            ["Copper", "Gold"],
            "MTRN",
        )
        self.assertIn("(Copper, Gold)", message)
        self.assertIn("$+12.35M", message)
        self.assertIn("75.0%", message)
        self.assertIn("$9.26M", message)
        self.assertIn("$3.09M", message)
        self.assertIn("MTRN", message)

    def test_kpi_cards_mark_rising_costs_and_falling_profit_as_adverse(self):
        data = {"base": 820.0, "stressed": 823.09, "delta": 3.09}
        self.assertEqual(
            build_kpi_card("COGS", data, "income_statement"),
            {
                "label": "COGS (Baseline: $820.0M)",
                "value": "$823.09M",
                "delta": "+3.09M",
                "delta_color": "inverse",
            },
        )
        self.assertEqual(
            build_kpi_card("Total Debt", data, "balance_sheet")["delta_color"],
            "inverse",
        )
        for metric in ('Operating Income','Gross Profit','Net Income'):
            card = build_kpi_card(metric, {'base':100,'stressed':90,'delta':-10}, 'income_statement')
            self.assertEqual(card['delta_color'], 'normal')
            self.assertEqual(card['delta'], '-10.00M')
        self.assertEqual(
            build_kpi_card("Free Cash Flow", data, "cash_flow")["delta_color"],
            "normal",
        )

    def test_history_chart_preserves_trace_and_layout(self):
        history = pd.DataFrame({"Date": ["2026-01-01"], "Price": [4.25]})
        figure = build_history_chart("Copper", history)
        self.assertEqual(figure.data[0].name, "Copper Futures benchmark")
        self.assertEqual(list(figure.data[0].y), [4.25])
        self.assertEqual(figure.layout.title.text, "Copper Futures benchmark history")
        self.assertEqual(figure.layout.yaxis.title.text, "Price ($)")
        self.assertEqual(figure.layout.height, 280)

    def test_kpi_change_reconciles_to_displayed_endpoints(self):
        card=build_kpi_card('Net Debt',{'base':100,'stressed':130,'delta':20},'balance_sheet')
        self.assertEqual(card['delta'],'+30.00M')

    def test_stochastic_chart_preserves_band_paths_and_layout(self):
        figure = build_stochastic_chart(
            "Copper",
            {
                "shock_pct": 12.5,
                "stochastic_projection": {
                    "days": [1, 2],
                    "upper_band": [5.5, 5.6],
                    "lower_band": [4.5, 4.4],
                    "baseline_path": [5.0, 5.1],
                    "stressed_path": [5.5, 5.4],
                },
            },
        )
        self.assertEqual([trace.name for trace in figure.data], [
            "95% Monte Carlo Band",
            "Baseline Trajectory",
            "Stressed (+12.5%)",
        ])
        self.assertEqual(list(figure.data[0].x), [1, 2, 2, 1])
        self.assertEqual(list(figure.data[0].y), [5.5, 5.6, 4.4, 4.5])
        self.assertEqual(figure.layout.title.text, "Copper 30-Day Forward Stochastic Scenario")
        self.assertEqual(figure.layout.height, 300)


if __name__ == "__main__":
    unittest.main()
