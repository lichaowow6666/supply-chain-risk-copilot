"""Small CPU LSTM challenger. Every fit receives only its historical prefix."""
from threading import RLock
import numpy as np

PARAMETERS = dict(sequence_length=20, hidden_size=8, layers=1, epochs=40,
                  learning_rate=.01, weight_decay=.001, seed=42,
                  device='cpu', batch='full', minimum_training_examples=128)
_LOCK = RLock()


def training_arrays(prices, horizon):
    logs = np.log(np.asarray(prices, dtype=float))
    returns = np.diff(logs)
    length = PARAMETERS['sequence_length']
    origins = np.arange(length, len(logs)-horizon)
    if len(origins) < PARAMETERS['minimum_training_examples']:
        raise ValueError('Insufficient complete LSTM training examples')
    # Both scaling statistics belong to the supplied training prefix only.
    center, scale = returns.mean(), max(float(returns.std()), 1e-6)
    x = np.stack([(returns[t-length:t]-center)/scale for t in origins])[:, :, None]
    target_scale = scale*np.sqrt(np.arange(1, horizon+1))
    y = np.stack([(logs[t+1:t+horizon+1]-logs[t])/target_scale for t in origins])
    current = ((returns[-length:]-center)/scale)[None, :, None]
    return x.astype('float32'), y.astype('float32'), current.astype('float32'), target_scale


def forecast_lstm(prices, horizon):
    """Run PyTorch in its own process, separate from XGBoost and the UI."""
    from pe_copilot.native_runtime import get_worker
    result = get_worker('forecast-lstm',
                        'pe_copilot.agents.forecast_worker:lstm').call({
        'prices': np.asarray(prices, dtype=float).tolist(),
        'horizon': int(horizon),
    }, timeout=120)
    return np.asarray(result['path'], dtype=result['dtype'])


def _forecast_lstm_local(prices, horizon):
    """Worker-only implementation; all parameters and calculations are unchanged."""
    import torch
    from torch import nn
    x, y, current, scale = training_arrays(prices, horizon)
    # Isolate RNG/thread settings between concurrent Streamlit forecast requests.
    with _LOCK, torch.random.fork_rng(devices=[]):
        threads = torch.get_num_threads()
        try:
            torch.set_num_threads(1)
            torch.random.default_generator.manual_seed(PARAMETERS['seed'])
            recurrent = nn.LSTM(1, PARAMETERS['hidden_size'], batch_first=True)
            head = nn.Linear(PARAMETERS['hidden_size'], horizon)
            parameters = list(recurrent.parameters())+list(head.parameters())
            optimizer = torch.optim.Adam(parameters, lr=PARAMETERS['learning_rate'],
                                         weight_decay=PARAMETERS['weight_decay'])
            inputs, targets = torch.from_numpy(x), torch.from_numpy(y)
            for _ in range(PARAMETERS['epochs']):
                optimizer.zero_grad()
                encoded, _ = recurrent(inputs)
                loss = nn.functional.mse_loss(head(encoded[:, -1]), targets)
                if not torch.isfinite(loss):
                    raise ValueError('Nonfinite LSTM training loss')
                loss.backward()
                nn.utils.clip_grad_norm_(parameters, 1.)
                optimizer.step()
            recurrent.eval(); head.eval()
            with torch.no_grad():
                encoded, _ = recurrent(torch.from_numpy(current))
                projected = head(encoded[:, -1]).numpy()[0]
            return np.log(float(prices[-1]))+projected*scale
        finally:
            torch.set_num_threads(threads)
