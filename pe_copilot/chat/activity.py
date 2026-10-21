"""Passive timings for existing services; never rerun work to inspect it."""
from contextlib import contextmanager
from time import monotonic


@contextmanager
def timed_activity(runtime, name, actor, operation, *, status='completed', detail=''):
    started = monotonic()
    row = {'name': name, 'actor': actor, 'operation': operation,
           'status': status, 'detail': detail}
    try:
        yield row
    except Exception as error:
        row.update(status='failed', detail='Service failed: ' + type(error).__name__)
        raise
    finally:
        row['duration_ms'] = round((monotonic() - started) * 1000, 3)
        runtime.setdefault('activities', []).append(row)
