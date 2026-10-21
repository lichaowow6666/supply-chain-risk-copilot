"""Process-local chat models; no native inference runs in the UI interpreter."""
import gc

from .retrieval import BGEEncoder, CrossEncoderReranker


_ENCODERS = {}
_RERANKERS = {}


def encode(payload):
    key = (payload['config'], payload['max_length'])
    if key not in _ENCODERS:
        # Opt-in base/tuned comparisons must not retain two multi-GB models.
        # Native worker requests are serialized, so no inference is in flight.
        _ENCODERS.clear()
        gc.collect()
        _ENCODERS[key] = BGEEncoder(config=key[0], max_length=key[1])
    return _ENCODERS[key]._encode_local(payload['texts']).tolist()


def score(payload):
    key = (payload['config'], payload['max_length'])
    if key not in _RERANKERS:
        _RERANKERS[key] = CrossEncoderReranker(config=key[0], max_length=key[1])
    return _RERANKERS[key]._score_local(payload['question'], payload['texts']).tolist()
