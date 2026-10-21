"""Application-level coordination without UI rendering or financial formulas."""

from typing import Any, Dict, List, Mapping, Tuple
from time import perf_counter
from datetime import datetime, timezone
from dataclasses import asdict, replace
from pathlib import Path
from copy import deepcopy
from uuid import uuid4

from pe_copilot.contracts import ExecutionStep, GraphRequest, ResultEnvelope, ScenarioRequest, ScenarioResult, HighlightContext, MaterialShock, stress_input_signature
from pe_copilot.supply_chain.graph_repository import GraphRepository
from pe_copilot.supply_chain.explorer import build_explorer_payload, snapshot_summary
from pe_copilot.supply_chain.scenario_impacts import annotate_scenario
from pe_copilot.agents.forecasting_agent import ForecastingAgent, ForecastRequest


class CopilotOrchestrator:
    """Coordinate the existing fundamental and market analysis agents."""

    def __init__(self, market_agent: Any, fundamental_agent: Any, graph_repository=None, preparation_service=None, run_directory="artifacts/scenario_runs", forecasting_agent=None):
        self.run_directory = Path(run_directory) if run_directory is not None else None
        self.preparation_service = preparation_service
        self.market_agent = market_agent
        self.forecasting_agent = forecasting_agent or ForecastingAgent()
        self.fundamental_agent = fundamental_agent
        if preparation_service is not None and hasattr(fundamental_agent, 'preparation_service'):
            fundamental_agent.preparation_service = preparation_service
        self.graph_repository = graph_repository if graph_repository is not None else GraphRepository()

    def prepare_forecasts(self, materials, horizon=30, on_step=None):
        histories=self.market_agent.capture_price_snapshot(tuple(materials))
        results={}
        for material in materials:
            if on_step:
                on_step(f'Forecasting Agent: evaluating {material} using earlier data at each forecast origin…')
            results[material]=self.forecasting_agent.prepare(ForecastRequest(material,horizon),histories[material])
        return results

    def prepare_company(self, query, progress=lambda message: None, refresh=False):
        if self.preparation_service is None:
            from pe_copilot.filings.service import CompanyPreparationService
            self.preparation_service = CompanyPreparationService(graph_repository=self.graph_repository)
            if hasattr(self.fundamental_agent, 'preparation_service'):
                self.fundamental_agent.preparation_service = self.preparation_service
        return self.preparation_service.prepare(query, progress, refresh)

    def get_graph_snapshots(self, ticker: str) -> List[Dict[str, Any]]:
        """Discover verified local bundles; do not invent available filings."""
        snapshots = []
        for company_id in self.graph_repository.list_company_ids():
            for snapshot_id in self.graph_repository.list_snapshots(company_id):
                view = self.graph_repository.load(company_id, snapshot_id)
                summary = snapshot_summary(view)
                if summary['ticker'].upper() == ticker.upper():
                    snapshots.append(summary)
        if self.preparation_service:
            prepared = self.preparation_service.load_preparation(ticker)
            if prepared and prepared.get('status') == 'complete':
                current = {r['snapshot_id'] for r in prepared['snapshots']}
                snapshots = [s for s in snapshots if s['snapshot_id'] in current or not s['snapshot_id'].startswith('sec-')]
        return sorted(snapshots, key=lambda s: (s['period_end'], s['filing_date'], s['snapshot_id']), reverse=True)

    def get_graph_history(self, company_id, entity_id, policy='latest'):
        from pe_copilot.filings.history import observation_history, relationship_history
        ids = self.graph_repository.list_snapshots(company_id)
        # Only current prepared versions participate; older automatic rebuilds remain immutable audit artifacts.
        if self.preparation_service:
            ticker = next((s['ticker'] for s in self.get_graph_snapshots_for_company(company_id)),None)
            prepared = self.preparation_service.load_preparation(ticker) if ticker else None
            if prepared and prepared.get('status') == 'complete':
                ids = [r['snapshot_id'] for r in prepared['snapshots']]
        views = [self.graph_repository.load(company_id,snapshot_id) for snapshot_id in ids]
        return {'observations':observation_history(views,entity_id,policy),'relationships':relationship_history(views,entity_id)}

    def get_graph_snapshots_for_company(self, company_id):
        return [snapshot_summary(self.graph_repository.load(company_id,s)) for s in self.graph_repository.list_snapshots(company_id)]

    def invoke(self, request, on_step=None):
        """Shared graph/scenario routes for the workbench and future chatbot.

        Session history, selections and pending chat actions remain caller-owned.
        Legacy discovery/stress methods keep their existing output contracts.
        """
        if isinstance(request, ScenarioRequest):
            return self.run_scenario(request, on_step=on_step)
        if not isinstance(request, GraphRequest):
            raise TypeError('invoke expects a GraphRequest or ScenarioRequest')
        started = perf_counter()
        run_id = str(uuid4())
        trace = [ExecutionStep('Route request', 'passed', 0.0, f'{request.source}: {request.intent}')]
        try:
            phase = perf_counter()
            view = self.graph_repository.load(request.context.company_id, request.context.snapshot_id)
            trace.append(ExecutionStep('Load and validate evidence graph', 'passed', (perf_counter()-phase)*1000,
                                       f'{len(view.snapshot.entities)} entities; {len(view.snapshot.claims)} relationships'))
            phase = perf_counter()
            payload = build_explorer_payload(view, request)
            trace.append(ExecutionStep('Resolve graph view and exposure highlights', 'passed', (perf_counter()-phase)*1000,
                                       f'{len(payload["nodes"])} visible entities; {request.lens}'))
            evidence = {}
            for record in (*payload['nodes'], *payload['edges']):
                for citation in record['evidence']:
                    evidence[(citation['document_id'], citation['locator'])] = citation
            for observation in payload['observations']:
                for citation in observation['raw']['provenance']['citations']:
                    evidence[(citation['document_id'], citation['locator'])] = citation
            reported = tuple(o['raw'] for o in payload['observations'] if o['basis'] == 'reported')
            derived = tuple(o['raw'] for o in payload['observations'] if o['basis'] == 'derived')
            assumptions = tuple(dict.fromkeys(a for o in payload['observations'] for a in o['assumptions']))
            trace.append(ExecutionStep('Preserve provenance and financial boundaries', 'passed', 0.0,
                                       'No scenario recalculation or graph loss allocation performed'))
            return ResultEnvelope(run_id, 'ok', request.context, payload, reported, derived,
                                  assumptions, tuple(evidence.values()), tuple(payload['warnings']), tuple(trace),
                                  (perf_counter()-started)*1000)
        except (ValueError, KeyError, FileNotFoundError) as error:
            trace.append(ExecutionStep('Graph request validation', 'failed', 0.0, type(error).__name__))
            message = 'The selected graph bundle is not available. Build or select a local evidence snapshot.' if isinstance(error, FileNotFoundError) else f'Graph request could not be resolved: {error}'
            return ResultEnvelope(run_id, 'unavailable', request.context, warnings=(message,), trace=tuple(trace),
                                  duration_ms=(perf_counter()-started)*1000)

    def get_available_commodities(self) -> List[str]:
        return self.market_agent.get_available_commodities()

    def get_available_target_tickers(self) -> List[str]:
        return self.fundamental_agent.get_available_tickers()

    def discover_target(self, ticker: str, accession=None) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, float]]]:
        if self.preparation_service is not None:
            report = self.fundamental_agent.analyze_report(ticker, accession, self.get_available_commodities())
            return report['materials'], report['baseline']['pools']
        materials = self.fundamental_agent.get_top_vulnerable_materials(ticker)
        baselines = self.fundamental_agent.get_baseline_financials(ticker)
        return materials, baselines

    def run_stress_test(
        self,
        material_configs: Mapping[str, Mapping[str, float]],
        pass_through_pct: float,
        baselines: Mapping[str, Mapping[str, float]],
    ) -> Dict[str, Any]:
        return self.market_agent.calculate_multi_material_waterfall(
            material_configs=material_configs,
            pass_through_pct=pass_through_pct,
            baselines=baselines,
        )


    def run_scenario(self, request: ScenarioRequest, on_step=None) -> ScenarioResult:
        """Coordinate audited report -> market -> calculation -> graph operations.

        No LLM calculates money, and no dollar losses are allocated to graph paths.
        Completed results and failures can be exported through the same contract.
        """
        from pe_copilot.scenario_support import describe_market_snapshot, fingerprint, validate_report, ScenarioValidationError
        from pe_copilot.filings.sec import atomic_json
        if not isinstance(request, ScenarioRequest):
            raise TypeError('run_scenario expects a ScenarioRequest')
        started = perf_counter()
        run_id = str(uuid4())
        created = datetime.now(timezone.utc).isoformat()
        trace, warnings, evidence = [], [], []
        baseline, market_snapshot, simulation, graph = {}, {}, {}, {}
        exposures, highlight = (), None
        status, active_step, active_actor = 'ok', 'Validate scenario', 'Orchestrator'
        phase = started
        assumptions = (
            f'User-assumed {request.spend_basis} material spending in USD millions; not reported purchase amounts.',
            'User-assumed unhedged fractions and a common pass-through fraction; not verified contractual coverage.',
            f'Price horizon: {request.shock_horizon_days} trading sessions. Explicit user shocks override the legacy automatic upward stress. The selected price change is assumed sustained across the full entered spending period, not a time-weighted earnings forecast.',
            'For price decreases, the same pass-through fraction offsets savings passed to customers; symmetric pass-through is an assumption.',
            'Existing 21% tax shield and debt trigger after half the baseline cash balance is absorbed are unchanged.',
            'No company loss allocation to individual products, facilities, segments or graph relationships.',
            'Market forward paths are illustrative simulations, not observed or guaranteed forecasts.',
        )

        def emit(name, state, detail, actor='Orchestrator', basis='workflow', inputs=(), outputs=(), duration=0):
            step = ExecutionStep(name,state,duration,detail,actor,basis,tuple(inputs),tuple(outputs))
            trace.append(step)
            if on_step:
                on_step(step)

        def begin(name, actor, basis, inputs=()):
            nonlocal active_step, active_actor, phase
            active_step, active_actor, phase = name, actor, perf_counter()
            emit(name,'running','Step started',actor,basis,inputs)

        def done(detail, basis, outputs=(), state='passed'):
            emit(active_step,state,detail,active_actor,basis,outputs=outputs,duration=(perf_counter()-phase)*1000)

        try:
            begin('Resolve selected report', 'Fundamental Agent', 'reported + model-extracted evidence', (request.accession,))
            report = self.fundamental_agent.analyze_report(request.ticker,request.accession,self.get_available_commodities())
            baseline = deepcopy(validate_report(request, report))
            selected_names = {m.material.casefold() for m in request.materials}
            exposures = tuple(deepcopy(m) for m in report['materials'] if m['material'].casefold() in selected_names)
            warnings.extend(baseline.get('warnings', []))
            evidence.extend({'kind':'financial', 'metric':metric, 'source_url':baseline.get('source_url'), **row}
                            for metric,row in baseline['evidence'].items())
            evidence.extend({'kind':'material', 'material':m['material'], **chunk} for m in exposures for chunk in m.get('raw_chunks',[]))
            done(f"Verified {request.ticker} / {request.accession}; {len(exposures)} selected exposures and {sum(len(p) for p in baseline['pools'].values())} financial inputs. Cached extraction is reused when valid; numerical inputs come from XBRL and documented derivations.",
                 'reported facts + cited LLM interpretation', ('baseline','exposures','evidence'))

            begin('Capture market history', 'Market Agent', 'market observations', tuple(m.material for m in request.materials))
            histories = self.market_agent.capture_price_snapshot(tuple(m.material for m in request.materials))
            market_snapshot = describe_market_snapshot(histories)
            for name,row in market_snapshot['series'].items():
                evidence.append({'kind':'market','material':name,**row})
            done('; '.join(f"{name}: {row['observations']} observations through {row['end'][:10]}" for name,row in market_snapshot['series'].items()),
                 'market observations', (market_snapshot['fingerprint'],))

            forecast_references={}
            begin('Validate selected price scenarios', 'Forecasting Agent', 'frozen forecast + explicit user inputs')
            for material in request.materials:
                if material.market_fingerprint and material.market_fingerprint!=market_snapshot['series'][material.material]['fingerprint']:
                    raise ScenarioValidationError('Market history changed. Refresh the price controls before running the scenario.')
                if material.reference_price is not None and abs(material.reference_price-float(histories[material.material]['Price'].iloc[-1]))>1e-8:
                    raise ScenarioValidationError('Market reference price changed. Refresh the forecast and reselect your price shock.')
                if material.forecast_id:
                    forecast=self.forecasting_agent.load(material.forecast_id)
                    if (not forecast or forecast['material']!=material.material or forecast['horizon']!=request.shock_horizon_days or
                            forecast['market']['fingerprint']!=market_snapshot['series'][material.material]['fingerprint'] or
                            material.forecast_model not in forecast['models']):
                        raise ScenarioValidationError('Forecast context is stale or mismatched. Prepare forecasts again before running this scenario.')
                    forecast_references[material.material]=forecast
                    evidence.append({'kind':'forecast','material':material.material,'forecast_id':material.forecast_id,
                                     'model':material.forecast_model,'as_of':forecast['market']['end']})
            done('Selected prices are scenario assumptions. Referenced forecasts were verified against this market snapshot; no forecast was retrained during financial calculation.',
                 'market data + versioned numerical forecast + user selection',tuple(m.forecast_id for m in request.materials if m.forecast_id))

            begin('Calculate scenario', 'Market Agent', 'deterministic Python', ('baseline','market snapshot','user assumptions'))
            simulation = self.market_agent.calculate_multi_material_waterfall(
                request.material_configs(),request.pass_through_fraction,baseline['pools'],price_histories=histories)
            if forecast_references:
                simulation['forecast_references']=forecast_references
            if set(simulation['materials']) != {m.material for m in request.materials}:
                raise ScenarioValidationError('The calculation returned a different material set than requested')
            warnings.extend(simulation.get('warnings',[]))
            done(f"Applied the existing cost and KPI formulas. Net absorbed cost: ${simulation['waterfall_summary']['Net_COGS_Absorbed']:,.2f}M. No LLM performed financial arithmetic.",
                 'deterministic calculation', ('simulation.materials','simulation.waterfall_summary','simulation.stressed_pools'))
            begin('Estimate joint company risk', 'Risk service', 'paired historical forecast errors + fixed user assumptions')
            from pe_copilot.analytics.company_risk import build_company_risk
            risk = build_company_risk(request, baseline['pools'], forecast_references)
            simulation['company_risk'] = risk
            done(f"Evaluated {risk['case_count']} matched material-error cases through the same financial formulas. This is a conditional empirical distribution, not a calibrated earnings forecast."
                 if risk['status']=='ok' else risk['reason'],
                 'historical error cases + deterministic financial calculations', ('simulation.company_risk',),
                 state='passed' if risk['status']=='ok' else 'unavailable')
            highlight = HighlightContext('chatbot' if request.source=='chatbot' else 'tab2',run_id,request.ticker,
                tuple(MaterialShock(name,row['cost_contribution']['shock_fraction']) for name,row in simulation['materials'].items()),
                company_id=request.context.company_id,snapshot_id=request.context.snapshot_id,
                input_signature=stress_input_signature(request.ticker,request.material_configs(),request.pass_through_fraction,baseline['pools']),
                created_at=created,horizon=f'{request.shock_horizon_days}-day market shock applied to {request.spend_basis} spending')

            begin('Map evidenced exposure paths', 'Graph service', 'disclosed connectivity', (request.context.snapshot_id,run_id))
            # The graph snapshot is pinned to the report. Comparative observation filters
            # stay a view concern, not a reason to silently switch the scenario filing.
            graph_context = replace(request.context,period_start=None,period_end=None)
            try:
                view = self.graph_repository.load(graph_context.company_id,graph_context.snapshot_id)
                summary = snapshot_summary(view)
                if summary['ticker'].upper()!=request.ticker or summary['period_end']!=baseline['period_end']:
                    raise ValueError('Graph company or reporting period does not match the scenario')
                graph = build_explorer_payload(view, GraphRequest(graph_context,lens='all_relationships',confirmed_only=False,
                                                                 highlight=highlight,source=request.source))
                graph = annotate_scenario(graph, request, simulation, exposures)
                warnings.extend(graph['warnings'])
                for row in (*graph['nodes'], *graph['edges']):
                    evidence.extend({'kind':'graph', **citation} for citation in row['evidence'])
                done(f"Mapped {len(graph['overlay']['nodes'])} entities and {len(graph['overlay']['edges'])} relationships. These are potential exposure paths, not allocated dollar losses.",
                     'conditional cost direction; company/material amounts; source-linked explanations', ('graph.overlay','graph.scenario_explanations'))
                emit('Explain scenario impacts', 'passed',
                     'Generated source-linked explanations from saved financial results, user assumptions and permitted cost paths. Uncertain relationships remain unsigned; downstream dollar allocation is not inferred.',
                     'Explanation service', 'grounded deterministic templates',
                     outputs=('graph.scenario_explanations','graph.scenario_financials'))
            except (ValueError, KeyError, OSError) as error:
                status = 'partial'
                warnings.append(f'Graph mapping unavailable ({type(error).__name__}); the validated financial result remains available.')
                done(warnings[-1],'graph mapping unavailable',state='failed')
                graph = {}
            emit('Scenario result ready', 'passed' if status=='ok' else 'partial',
                 'Financial results, directional annotations and grounded template explanations share this run ID. Downstream dollar allocation is not supported without exposure weights.',
                 outputs=(run_id,))
        except Exception as error:
            # Provider exceptions may embed endpoints or credentials. Persist only safe
            # validation messages or exception type, never raw network exception text.
            message = str(error) if isinstance(error, ScenarioValidationError) else f'{type(error).__name__}; no complete scenario result was produced'
            emit(active_step,'failed',message,active_actor,duration=(perf_counter()-phase)*1000)
            warnings.append(message)
            status, simulation, graph, highlight = 'failed', {}, {}, None

        try:
            input_fingerprint = fingerprint({'request':asdict(request),'baseline':baseline,'market':market_snapshot.get('fingerprint')})
        except (ValueError, TypeError):
            status, baseline, market_snapshot, simulation, graph, exposures, evidence, highlight = 'failed', {}, {}, {}, {}, (), [], None
            warnings.append('Scenario inputs contained unsupported or nonfinite values; results were withheld.')
            emit('Validate execution record','failed',warnings[-1])
            input_fingerprint = fingerprint(asdict(request))
        result = ScenarioResult(run_id,status,request,created,baseline,exposures,market_snapshot,simulation,graph,highlight,
            assumptions,tuple(evidence),tuple(dict.fromkeys(warnings)),tuple(trace),(perf_counter()-started)*1000,input_fingerprint)
        # Strict serialization is also a validation gate before exposing numerical results.
        try:
            record = result.to_dict()
        except (ValueError, TypeError):
            emit('Validate execution record','failed','Unsupported or nonfinite output; results withheld.')
            result = replace(result,status='failed',baseline={},exposures=(),market_snapshot={},simulation={},graph={},highlight=None,evidence_references=(),
                trace=tuple(trace),warnings=(*result.warnings,'Scenario output contained an unsupported or nonfinite value; results were withheld.'))
            record = result.to_dict()
        if self.run_directory is not None:
            try:
                atomic_json(self.run_directory/(run_id+'.json'),record)
            except OSError:
                result = replace(result,warnings=(*result.warnings,'The local run record could not be saved; download the execution record from the result panel.'))
        return result
