"""Fictional data adapter around the existing production chat and domain services."""
from copy import deepcopy
from time import monotonic

from .demo import SyntheticPreparation, build_demo
from pe_copilot.contracts import ScenarioMaterial, ScenarioRequest, WorkspaceContext
from pe_copilot.agents.forecasting_agent import ForecastingAgent
from pe_copilot.workspace_state import activate_prepared_company


class BenchmarkPreparation(SyntheticPreparation):
    def load_preparation(self, ticker):
        prepared = super().load_preparation(ticker)
        if prepared:
            prepared['company']['name'] = 'Synthetic Alpha' if ticker == 'SYNTA' else 'Synthetic Beta'
        return prepared

    def report_baseline(self, ticker, accession):
        baseline = super().report_baseline(ticker, accession)
        # Beta's authored source supplies quarter-only revenue. The application
        # baseline contract expects YTD for 10-Q; do not mislabel that quarter as
        # a YTD baseline. It remains available through its exact source passage.
        if ticker == 'SYNTB':
            baseline['pools']['income_statement'] = {}
            baseline['evidence'] = {}
            baseline['warnings'].append('Synthetic Beta has quarter-only revenue evidence; no YTD revenue baseline is supplied.')
        return baseline

    def prepare(self, query, progress=lambda text: None, refresh=False):
        aliases = {'synta': 'SYNTA', 'synthetic alpha': 'SYNTA', 'synthetic synta': 'SYNTA',
                   '9000000001': 'SYNTA', 'syntb': 'SYNTB', 'synthetic beta': 'SYNTB',
                   'synthetic syntb': 'SYNTB', '9000000002': 'SYNTB'}
        ticker = aliases.get(str(query).strip().casefold())
        if not ticker:
            raise ValueError('The synthetic benchmark only contains Synthetic Alpha and Synthetic Beta.')
        prepared = self.load_preparation(ticker)
        reports = [r for r in self.suite['reports'] if r['ticker'] == ticker]
        annual = [r for r in reports if r['form'] == '10-K']
        baseline_report = max(annual or reports, key=lambda r: r['period_end'])
        prepared.update(baseline=self.report_baseline(ticker, baseline_report['accession']), reused=True)
        progress('Using the explicitly synthetic benchmark filing store; no live SEC request.')
        return prepared


class BenchmarkWorkspace:
    def __init__(self, root):
        self.app, _ = build_demo(root)
        original = self.app.preparation_service
        self.service = BenchmarkPreparation(original.suite, original.index, original.root)
        self.app.preparation_service = self.service
        self.app.fundamental_agent.preparation_service = self.service
        self.app.forecasting_agent = ForecastingAgent(root / 'forecasts', include_xgboost=False, include_lstm=False)
        self.scenarios, self.forecasts = {}, None
        self.setup_timings = []

    def state_for(self, case):
        state = {'copilot_messages': []}
        scope = case.get('scope')
        if scope:
            report = next(r for r in self.service.suite['reports'] if r['id'] == scope)
            baseline = self.service.report_baseline(report['ticker'], report['accession'])
            materials, pools = self.service.scan(report['ticker'], report['accession'])
            activate_prepared_company(state, self.service.load_preparation(report['ticker']), baseline, materials, pools)
            state['selected_metals'] = ['Copper']
        setup = case.get('setup', {})
        if 'selected_materials' in setup:
            state['selected_metals'] = list(setup['selected_materials'])
        scenario = setup.get('scenario')
        if scenario:
            if scope != 'a25' or scenario not in ('up', 'down', 'mixed'):
                raise ValueError('Scenario fixtures require annual Alpha and a named scenario.')
            if scenario not in self.scenarios:
                since = monotonic()
                materials = [ScenarioMaterial('Copper', 100., .8, -.2 if scenario == 'down' else .2)]
                if scenario == 'mixed':
                    materials.append(ScenarioMaterial('Natural Gas', 25., .8, -.2))
                request = ScenarioRequest(WorkspaceContext(state['baseline_context']['company_id'], baseline['snapshot_id']),
                    report['ticker'], report['accession'], tuple(materials), .5)
                result = self.app.run_scenario(request)
                if result.status not in ('ok', 'partial') or not result.simulation:
                    raise ValueError('The real scenario service did not produce the synthetic fixture.')
                self.scenarios[scenario] = result
                self.setup_timings.append({'operation': 'scenario_' + scenario, 'seconds': monotonic() - since})
            state.update(sim_execution=self.scenarios[scenario], sim_inputs_current=True,
                         selected_metals=[m.material for m in self.scenarios[scenario].request.materials])
        if setup.get('forecast'):
            if self.forecasts is None:
                since = monotonic()
                self.forecasts = self.app.prepare_forecasts(['Copper'], horizon=5)
                self.setup_timings.append({'operation': 'forecast_benchmarks', 'seconds': monotonic() - since})
            state.update(forecast_prepared=deepcopy(self.forecasts), forecast_horizon=5, selected_metals=['Copper'])
            forecast = self.forecasts['Copper']
            state['forecast_model_Copper_' + forecast['market']['fingerprint'][:12] + '_5'] = 'naive'
        for entry in setup.get('history', []):
            item = deepcopy(entry)
            if 'content' in item and 'text' not in item:
                item['text'] = item.pop('content')
            if isinstance(item.get('scope'), str):
                history_report = next(r for r in self.service.suite['reports'] if r['id'] == item['scope'])
                item['scope'] = self.service.report_baseline(history_report['ticker'], history_report['accession'])
            if item.get('role') == 'assistant' and 'scope' not in item:
                item['scope'] = deepcopy(state.get('baseline_context', {}))
            state['copilot_messages'].append(item)
        return state
