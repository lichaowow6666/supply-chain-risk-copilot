"""Local synthetic smoke check for the model combination behind the macOS crash.

Uses already installed models only. No SEC/LLM calls, downloads or database edits.
Run from the repository: venv/bin/python scripts/check_native_runtime.py
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from pe_copilot.agents.forecasting_agent import forecast_path
from pe_copilot.chat.retrieval import BGEEncoder, CrossEncoderReranker
from pe_copilot.native_runtime import close_workers


def main():
    encoder = BGEEncoder(ROOT / 'artifacts/chat_models/base_model.json')
    reranker = CrossEncoderReranker(ROOT / 'artifacts/chat_models/reranker_model.json')
    prices = np.exp(3 + np.cumsum(np.random.default_rng(123).normal(0, .01, 300)))
    dates = pd.bdate_range('2023-01-02', periods=len(prices)).to_list()
    passages = ['This synthetic company uses natural gas in manufacturing.',
                'This synthetic company owns platinum production equipment.']
    baseline = None
    try:
        for cycle in range(3):
            started = monotonic()
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(encoder.encode, passages),
                    pool.submit(reranker.score, 'What energy does the company use?', passages),
                    pool.submit(forecast_path, 'xgboost', prices, dates, 5),
                    pool.submit(forecast_path, 'lstm', prices, dates, 5)]
                outputs = [future.result() for future in futures]
            assert all(np.isfinite(value).all() for value in outputs)
            assert outputs[0].shape[0] == 2 and outputs[1].shape == (2,)
            assert outputs[2].shape == outputs[3].shape == (5,)
            if baseline is not None:
                for actual, expected in zip(outputs, baseline):
                    np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-7)
            baseline = outputs
            forbidden = {'torch', 'xgboost', 'onnxruntime', 'sentence_transformers'} & set(sys.modules)
            assert not forbidden, f'Native model backend leaked into parent: {forbidden}'
            print(f'PASS cycle {cycle + 1}/3: embeddings + reranking + XGBoost + LSTM; '
                  f'finite repeatable outputs, application process isolated ({monotonic()-started:.1f}s)', flush=True)
    finally:
        close_workers()


if __name__ == '__main__':
    main()
