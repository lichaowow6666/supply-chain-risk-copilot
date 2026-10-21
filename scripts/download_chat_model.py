"""Explicit installation of pinned local retrieval models; never called by chat."""
import argparse
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RERANKER_ID = 'cross-encoder/ms-marco-MiniLM-L6-v2'
RERANKER_REVISION = '233902d25c440f23af6f7d6e94d2946bac0bee0a'


def install(model_id, revision, patterns, config_name, **settings):
    from huggingface_hub import snapshot_download
    root = ROOT / 'artifacts/chat_models'
    root.mkdir(parents=True, exist_ok=True)
    print(f'Installing {model_id} at revision {revision}', flush=True)
    path = snapshot_download(model_id, revision=revision, cache_dir=str(root/'hub'),
        allow_patterns=patterns, max_workers=3, token=False)
    config = {'model_id': model_id, 'revision': revision,
              'path': str(Path(path).resolve()), **settings}
    destination = root / config_name
    temporary = destination.with_suffix(f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, destination)
    print(f'Local model ready: {config_name}. Model files and configuration remain ignored by Git.', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=(
        'Install the pinned BGE-M3 model into ignored local artifacts. '
        'Add --reranker to also install the pinned official ONNX cross-encoder. '
        'Existing Hugging Face cached files are reused.'))
    parser.add_argument('--reranker', action='store_true', help=(
        'also install the English MS MARCO MiniLM-L6-v2 reranker; serving requires onnxruntime'))
    args = parser.parse_args(argv)
    revision = json.loads((ROOT/'evaluations/embedding_v1/training_config.json').read_text())['base_revision']
    install('BAAI/bge-m3', revision,
        ['config.json', 'config_sentence_transformers.json', 'sentence_bert_config.json',
         'modules.json', '1_Pooling/*', 'tokenizer.json', 'tokenizer_config.json',
         'special_tokens_map.json', 'sentencepiece.bpe.model', 'model.safetensors', 'pytorch_model.bin'],
        'base_model.json')
    if args.reranker:
        # This is the publisher's export at the same model revision used for the
        # live serving smoke check. No model conversion or training runs here.
        install(RERANKER_ID, RERANKER_REVISION,
            ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json',
             'vocab.txt', 'onnx/model.onnx', 'README.md'],
            'reranker_model.json', languages=['en'], max_length=512,
            backend='onnx', onnx_file='onnx/model.onnx')


if __name__ == '__main__':
    main()
