"""Offline interactive SVG graph; data is escaped and never executed as code."""
import json
from pathlib import Path

ASSET = Path(__file__).parent / 'assets' / 'graph_explorer.html'


def render_graph_html(payload):
    encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False)
    # Prevent user-supplied labels or evidence from terminating the data script.
    encoded = encoded.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    return ASSET.read_text(encoding='utf-8').replace('__GRAPH_PAYLOAD__', encoded)
