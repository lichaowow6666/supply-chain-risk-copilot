"""Private JSON-lines transport for model-family worker processes."""
import importlib
import json
import os
import sys


def main():
    # Reserve a dedicated protocol descriptor. Native libraries and Python print
    # statements may write stdout; neither may corrupt the response stream.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), 'w', encoding='utf-8', buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    module_name, function_name = sys.argv[1].split(':', 1)
    handler = getattr(importlib.import_module(module_name), function_name)
    for line in sys.stdin:
        try:
            result = handler(json.loads(line))
            response = json.dumps({'ok': True, 'result': result}, allow_nan=False)
        except Exception:
            response = json.dumps({'ok': False})
        protocol.write(response + '\n')
        protocol.flush()


if __name__ == '__main__':
    main()
