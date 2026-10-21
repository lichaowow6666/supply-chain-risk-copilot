"""Versioned, authored synthetic cases for the current WorkspaceChat benchmark.

References are evaluation-only targets. This loader never invokes a model,
prepares a company, calculates a scenario, or supplies references to generation.
"""
from collections import Counter
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import unicodedata


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BENCHMARK = ROOT / 'evaluations/interview_100_v1.json'
BASE_SUITE = ROOT / 'evaluations/report_scope_v1.json'
SCOPES = frozenset({'a24', 'a25', 'aq', 'bq'})
ACTIONS = frozenset({'prepare', 'navigate', 'explain', 'clarify'})
ANSWER_KINDS = frozenset({'general', 'company', 'scenario', 'forecast', 'workflow'})
SECTIONS = frozenset({'overview', 'exposures', 'materials', 'assumptions', 'graph', 'impact', 'forecast'})
CATEGORY_COUNTS = {'disclosure': 24, 'finance': 12, 'graph': 10, 'scenario': 16,
                   'forecast': 8, 'education': 8, 'navigation': 8, 'governance': 14}


def _text(value, label, maximum=12000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{label} requires nonempty bounded text.')


def _choices(value, allowed, label):
    if (not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value)
            or len(set(value)) != len(value) or not set(value) <= allowed):
        raise ValueError(f'Invalid {label}.')


def validate_benchmark(suite):
    """Validate fixture compatibility and fail closed on malformed expectations."""
    if not isinstance(suite, dict):
        raise ValueError('Expected a benchmark object.')
    expected_metadata = {'schema_version': 1, 'suite_id': 'interview-100-v1', 'version': '1.0.0',
        'data_status': 'synthetic_only', 'split': 'development', 'review_status': 'authored_not_human_reviewed',
        'base_suite_path': 'evaluations/report_scope_v1.json'}
    if any(suite.get(key) != value for key, value in expected_metadata.items()):
        raise ValueError('Expected the versioned synthetic, authored development benchmark.')
    base_bytes = BASE_SUITE.read_bytes()
    if suite.get('base_suite_sha256') != sha256(base_bytes).hexdigest():
        raise ValueError('Base report fixture version changed; references require review before execution.')
    base = json.loads(base_bytes)
    report_ids = {report['id'] for report in base['reports']}
    if report_ids != SCOPES:
        raise ValueError('Unexpected report scope in the base fixture.')
    fixture = suite.get('fixture_contract', {})
    if (set(fixture.get('report_ids', [])) != SCOPES or fixture.get('scenario_scope') != 'a25'
            or fixture.get('currency') != 'USD' or fixture.get('amount_unit') != 'millions'
            or fixture.get('spend_basis') != 'annual' or fixture.get('extra_paragraphs') != []):
        raise ValueError('Fixture scope, units or source additions changed; references require review.')
    if fixture.get('forecast') != {'observations': 400, 'horizon': 5, 'selected_model': 'naive',
                                   'include_xgboost': False, 'include_lstm': False}:
        raise ValueError('The versioned forecast setup changed.')
    copper = {'annual_spend': 100, 'shock_fraction': .2, 'unhedged_fraction': .8, 'pass_through_fraction': .5}
    gas = {'annual_spend': 25, 'shock_fraction': -.2, 'unhedged_fraction': .8, 'pass_through_fraction': .5}
    if fixture.get('scenario_definitions') != {'up': {'Copper': copper},
        'down': {'Copper': {**copper, 'shock_fraction': -.2}}, 'mixed': {'Copper': copper, 'Natural Gas': gas}}:
        raise ValueError('The versioned scenario assumptions changed; numeric references require review.')
    paragraph_ids = {paragraph['id'] for report in base['reports'] for paragraph in report['paragraphs']}
    catalog = suite.get('reference_basis_catalog')
    if not isinstance(catalog, dict) or not catalog:
        raise ValueError('Reference provenance catalog is required.')
    for name, description in catalog.items():
        _text(name, 'Reference basis identifier', 120)
        _text(description, 'Reference basis description')
    bases = paragraph_ids | set(catalog)
    cases = suite.get('cases')
    if not isinstance(cases, list) or len(cases) != 100:
        raise ValueError('The benchmark must contain exactly 100 cases.')
    questions, identifiers = set(), set()
    expected_fields = {'actions', 'answer_kinds', 'must_preserve_workspace', 'section',
                       'required_terms', 'forbidden_terms', 'numeric_facts'}
    for position, case in enumerate(cases, 1):
        if not isinstance(case, dict) or set(case) != {
            'id', 'category', 'language', 'scope', 'question', 'reference', 'expected', 'setup', 'tags', 'reference_basis'}:
            raise ValueError('Each case must use the current WorkspaceChat case contract.')
        if case['id'] != f'I{position:03d}' or case['id'] in identifiers:
            raise ValueError('Case IDs must be unique and ordered I001–I100.')
        identifiers.add(case['id'])
        _text(case['question'], 'Question', 8000)
        _text(case['reference'], 'Independent reference')
        normalized = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', case['question'])).strip().casefold()
        if normalized in questions:
            raise ValueError('Duplicate questions do not constitute distinct evaluation cases.')
        questions.add(normalized)
        if case['category'] not in CATEGORY_COUNTS or case['language'] not in ('en', 'zh'):
            raise ValueError('Unknown category or language.')
        if case['scope'] is not None and case['scope'] not in SCOPES:
            raise ValueError('Each company question requires a known selected report scope.')
        expectations = case['expected']
        if (not isinstance(expectations, dict) or not set(expectations) <= expected_fields
                or not {'actions', 'answer_kinds', 'must_preserve_workspace'} <= set(expectations)):
            raise ValueError('Invalid expected-result contract.')
        _choices(expectations['actions'], ACTIONS, 'expected actions')
        _choices(expectations['answer_kinds'], ANSWER_KINDS, 'expected answer kinds')
        if 'section' in expectations and expectations['section'] not in SECTIONS:
            raise ValueError('Unknown expected canvas section.')
        if type(expectations['must_preserve_workspace']) is not bool:
            raise ValueError('Workspace-preservation expectation must be boolean.')
        if not expectations['must_preserve_workspace'] and 'prepare' not in expectations['actions']:
            raise ValueError('Only an explicitly expected company preparation may replace the workspace.')
        for field in ('required_terms', 'forbidden_terms'):
            if field in expectations:
                values = expectations[field]
                if not isinstance(values, list):
                    raise ValueError('Term checks must be lists and are not semantic correctness scores.')
                for value in values:
                    _text(value, field, 200)
        facts = expectations.get('numeric_facts', [])
        if not isinstance(facts, list):
            raise ValueError('Numeric reference facts must be labeled values.')
        for fact in facts:
            if not isinstance(fact, dict) or set(fact) != {'label', 'value'}:
                raise ValueError('Numeric facts require a label and value.')
            _text(fact['label'], 'Numeric fact label', 200)
            if isinstance(fact['value'], bool) or not isinstance(fact['value'], (int, float)) or not math.isfinite(fact['value']):
                raise ValueError('Numeric reference facts must be finite numbers, never missing-value substitutes.')
        setup = case['setup']
        if not isinstance(setup, dict) or set(setup) != {'scenario', 'forecast', 'history'}:
            raise ValueError('Invalid deterministic setup contract.')
        if setup['scenario'] not in (None, 'up', 'down', 'mixed'):
            raise ValueError('Unknown saved scenario fixture.')
        if setup['scenario'] is not None and case['scope'] != 'a25':
            raise ValueError('Saved scenario amounts are valid only against the a25 annual fixture.')
        if type(setup['forecast']) is not bool or (setup['forecast'] and case['scope'] is None):
            raise ValueError('Prepared forecasts require a selected company scope.')
        if not isinstance(setup['history'], list) or len(setup['history']) > 8:
            raise ValueError('History must be a bounded list of prior turns.')
        for message in setup['history']:
            if (not isinstance(message, dict) or not {'role', 'text'} <= set(message)
                    or not set(message) <= {'role', 'text', 'scope'}
                    or message['role'] not in ('user', 'assistant')):
                raise ValueError('Invalid scoped conversation history.')
            _text(message['text'], 'History message', 4000)
            if message.get('scope') is not None and message['scope'] not in SCOPES:
                raise ValueError('History report scope is unknown.')
        if not isinstance(case['tags'], list) or any(not isinstance(tag, str) or not tag for tag in case['tags']):
            raise ValueError('Case tags must be text labels.')
        _choices(case['reference_basis'], bases, 'reference provenance')
    categories = dict(Counter(case['category'] for case in cases))
    languages = dict(Counter(case['language'] for case in cases))
    if categories != CATEGORY_COUNTS or suite.get('category_counts') != categories:
        raise ValueError('Category counts do not match the declared balanced design.')
    if languages != {'en': 90, 'zh': 10} or suite.get('language_counts') != languages:
        raise ValueError('Expected 90 English and 10 Chinese authored questions.')
    return suite


def load_benchmark(path=DEFAULT_BENCHMARK):
    return validate_benchmark(json.loads(Path(path).read_text(encoding='utf-8')))
