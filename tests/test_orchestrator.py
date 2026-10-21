import unittest

from pe_copilot.orchestrator import CopilotOrchestrator


class FakeFundamentalAgent:
    def __init__(self):
        self.calls = []

    def get_top_vulnerable_materials(self, ticker):
        self.calls.append(("materials", ticker))
        return [{"material": "Copper"}]

    def get_available_tickers(self):
        return ["MTRN"]

    def get_baseline_financials(self, ticker):
        self.calls.append(("baselines", ticker))
        return {"income_statement": {"COGS": 820.0}}


class FakeMarketAgent:
    def __init__(self):
        self.stress_kwargs = None

    def get_available_commodities(self):
        return ["Copper", "Gold"]

    def calculate_multi_material_waterfall(self, **kwargs):
        self.stress_kwargs = kwargs
        return {"waterfall_summary": {"Net_COGS_Absorbed": 1.25}}


class CopilotOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.market = FakeMarketAgent()
        self.fundamental = FakeFundamentalAgent()
        self.orchestrator = CopilotOrchestrator(self.market, self.fundamental)

    def test_available_commodities_delegates_unchanged(self):
        self.assertEqual(self.orchestrator.get_available_commodities(), ["Copper", "Gold"])

    def test_available_target_tickers_delegates_unchanged(self):
        self.assertEqual(self.orchestrator.get_available_target_tickers(), ["MTRN"])

    def test_discovery_coordinates_existing_agent_calls_in_order(self):
        materials, baselines = self.orchestrator.discover_target("MTRN")
        self.assertEqual(materials, [{"material": "Copper"}])
        self.assertEqual(baselines, {"income_statement": {"COGS": 820.0}})
        self.assertEqual(
            self.fundamental.calls,
            [("materials", "MTRN"), ("baselines", "MTRN")],
        )

    def test_stress_test_delegates_inputs_and_output_unchanged(self):
        configs = {"Copper": {"annual_spend": 25.0, "unhedged_pct": 0.8}}
        baselines = {"income_statement": {"COGS": 820.0}}
        result = self.orchestrator.run_stress_test(configs, 0.75, baselines)
        self.assertEqual(result, {"waterfall_summary": {"Net_COGS_Absorbed": 1.25}})
        self.assertEqual(
            self.market.stress_kwargs,
            {
                "material_configs": configs,
                "pass_through_pct": 0.75,
                "baselines": baselines,
            },
        )


if __name__ == "__main__":
    unittest.main()
