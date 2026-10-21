"""Opt-in usage metadata and explicitly submitted local answer feedback.

Session counts are not unique people. Feedback is self-reported and optional;
absence of observations is unavailable, not zero-percent adoption.
Neither path stores questions, answers, evidence or free-form feedback text.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4


LOCAL_USAGE_ROOT = Path(__file__).resolve().parents[2] / 'artifacts' / 'anonymous_usage'
EVENTS = {'answer_rendered', 'inspection_opened', 'feedback'}
FEEDBACK_REASONS = {
    'incorrect_facts': 'Incorrect facts',
    'missing_evidence': 'Missing evidence',
    'wrong_scope': 'Wrong company or report',
    'misunderstood_request': 'Misunderstood my request',
    'incomplete_answer': 'Incomplete answer',
    'unclear_answer': 'Unclear answer',
    'slow_response': 'Slow response',
    'other': 'Other',
}


def anonymous_session_id():
    return uuid4().hex


def _connect(root):
    directory = Path(root) if root is not None else LOCAL_USAGE_ROOT
    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / 'events.sqlite', timeout=10)
    try:
        connection.execute('''CREATE TABLE IF NOT EXISTS events (
            session_id TEXT NOT NULL, answer_hash TEXT NOT NULL, event TEXT NOT NULL,
            helpful INTEGER, task_completed INTEGER, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, reasons_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (session_id, answer_hash, event))''')
        columns = {row[1] for row in connection.execute('PRAGMA table_info(events)')}
        if 'reasons_json' not in columns:
            try:
                connection.execute("ALTER TABLE events ADD COLUMN reasons_json TEXT NOT NULL DEFAULT '[]'")
            except sqlite3.OperationalError:
                # Another session can complete the same additive migration.
                if 'reasons_json' not in {row[1] for row in connection.execute('PRAGMA table_info(events)')}:
                    raise
        connection.commit()
    except Exception:
        connection.close()
        raise
    return connection


def _answer_identity(session_id, answer_id):
    if not isinstance(session_id, str) or not re.fullmatch(r'[0-9a-f]{32}', session_id):
        raise ValueError('Use an anonymous session identifier.')
    if not isinstance(answer_id, str) or not answer_id or len(answer_id) > 256:
        raise ValueError('An opaque answer identifier is required.')
    return sha256(answer_id.encode()).hexdigest()


def _reasons(value):
    """Read only approved reason codes, tolerating old or corrupt local rows."""
    try:
        items = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    return [code for code in FEEDBACK_REASONS if code in items]


def record_event(*, enabled, session_id, event, answer_id=None, helpful=None,
                 task_completed=None, root=None):
    """Record one observed UI event after explicit opt-in, deduplicated by answer.

    Callers supply an opaque turn/answer ID, never the question or company. Only
    its hash is stored. Feedback updates replace the existing response, so UI
    rerenders and changed votes cannot inflate the denominator.
    """
    if enabled is not True:
        return {'status': 'disabled', 'stored': False}
    identity = _answer_identity(session_id, answer_id)
    if event not in EVENTS:
        raise ValueError('Unsupported anonymous event.')
    if any(value is not None and not isinstance(value, bool) for value in (helpful, task_completed)):
        raise ValueError('Feedback values must be boolean or unavailable.')
    if event != 'feedback' and (helpful is not None or task_completed is not None):
        raise ValueError('Outcome values belong to feedback events only.')
    if event == 'feedback' and helpful is None and task_completed is None:
        return {'status': 'no_feedback', 'stored': False}
    now = datetime.now(timezone.utc).isoformat()
    connection = None
    try:
        connection = _connect(root)
        with connection:
            existing = connection.execute('SELECT helpful, task_completed, reasons_json FROM events WHERE session_id=? AND answer_hash=? AND event=?',
                                          (session_id, identity, event)).fetchone()
            values = (None if helpful is None else int(helpful),
                      None if task_completed is None else int(task_completed))
            if existing and event != 'feedback':
                return {'status': 'duplicate', 'stored': False}
            if existing:
                # A separate helpfulness or completion control should preserve
                # the previously supplied other value.
                values = tuple(old if new is None else new for old, new in zip(existing[:2], values))
                reasons = json.dumps(_reasons(existing[2]) if values[0] == 0 else [])
                if tuple(existing[:2]) == values and existing[2] == reasons:
                    return {'status': 'duplicate', 'stored': False}
                connection.execute('UPDATE events SET helpful=?, task_completed=?, reasons_json=?, updated_at=? WHERE session_id=? AND answer_hash=? AND event=?',
                                   (*values, reasons, now, session_id, identity, event))
            else:
                connection.execute('INSERT INTO events (session_id, answer_hash, event, helpful, task_completed, created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
                                   (session_id, identity, event, *values, now, now))
        return {'status': 'recorded', 'stored': True}
    except (OSError, sqlite3.Error):
        return {'status': 'unavailable', 'stored': False}
    finally:
        if connection is not None:
            connection.close()


def record_feedback(*, session_id, answer_id, helpful, reasons=None, root=None):
    """Persist an explicit thumbs vote without enabling usage-event collection.

    A missing vote clears its reasons and preserves any prior task-completion
    feedback. Positive votes clear negative reasons. On an existing negative
    vote, reasons=None preserves its reasons, while [] explicitly clears them.
    Only fixed reason codes are accepted; there is no free-text storage field.
    """
    identity = _answer_identity(session_id, answer_id)
    if helpful is not None and not isinstance(helpful, bool):
        raise ValueError('Feedback must be positive, negative or cleared.')
    if reasons is not None and (not isinstance(reasons, list) or len(reasons) > len(FEEDBACK_REASONS)
            or any(not isinstance(code, str) or code not in FEEDBACK_REASONS for code in reasons)):
        raise ValueError('Choose only the available feedback reasons.')
    now, connection = datetime.now(timezone.utc).isoformat(), None
    try:
        connection = _connect(root)
        with connection:
            existing = connection.execute('SELECT helpful, task_completed, reasons_json FROM events WHERE session_id=? AND answer_hash=? AND event=?',
                                          (session_id, identity, 'feedback')).fetchone()
            if helpful is None:
                if existing is None:
                    return {'status': 'cleared', 'stored': False}
                if existing[1] is None:
                    connection.execute('DELETE FROM events WHERE session_id=? AND answer_hash=? AND event=?',
                                       (session_id, identity, 'feedback'))
                elif existing[0] is None and not _reasons(existing[2]):
                    return {'status': 'duplicate', 'stored': False}
                else:
                    connection.execute("UPDATE events SET helpful=NULL, reasons_json='[]', updated_at=? WHERE session_id=? AND answer_hash=? AND event=?",
                                       (now, session_id, identity, 'feedback'))
                return {'status': 'cleared', 'stored': True}
            selected = []
            if helpful is False:
                selected = (_reasons(existing[2]) if reasons is None and existing and existing[0] == 0
                            else _reasons(reasons))
            encoded = json.dumps(selected)
            if existing and existing[0] == int(helpful) and existing[2] == encoded:
                return {'status': 'duplicate', 'stored': False}
            if existing:
                connection.execute('UPDATE events SET helpful=?, reasons_json=?, updated_at=? WHERE session_id=? AND answer_hash=? AND event=?',
                                   (int(helpful), encoded, now, session_id, identity, 'feedback'))
            else:
                connection.execute('INSERT INTO events (session_id, answer_hash, event, helpful, task_completed, created_at, updated_at, reasons_json) VALUES (?,?,?,?,?,?,?,?)',
                                   (session_id, identity, 'feedback', int(helpful), None, now, now, encoded))
        return {'status': 'recorded', 'stored': True}
    except (OSError, sqlite3.Error):
        return {'status': 'unavailable', 'stored': False}
    finally:
        if connection is not None:
            connection.close()


def record_answer(*, enabled, session_id, answer_id, root=None):
    return record_event(enabled=enabled, session_id=session_id,
                        event='answer_rendered', answer_id=answer_id, root=root)


def _rate(numerator, denominator):
    return {'numerator': numerator, 'denominator': denominator,
            'value': numerator / denominator if denominator else None}


def summarize_events(root=None):
    """Read local pilot observations; no events or identities are returned."""
    path = (Path(root) if root is not None else LOCAL_USAGE_ROOT) / 'events.sqlite'
    rows, status, connection = [], 'no_data', None
    if path.exists():
        try:
            connection = sqlite3.connect(path, timeout=10)
            columns = {row[1] for row in connection.execute('PRAGMA table_info(events)')}
            reason_column = 'reasons_json' if 'reasons_json' in columns else "'[]'"
            rows = connection.execute('SELECT session_id, answer_hash, event, helpful, task_completed, '
                                      + reason_column + ' FROM events').fetchall()
            status = 'observed' if rows else 'no_data'
        except sqlite3.Error:
            status = 'unavailable'
        finally:
            if connection is not None:
                connection.close()
    rendered = {(row[0], row[1]) for row in rows if row[2] == 'answer_rendered'}
    inspected = {(row[0], row[1]) for row in rows if row[2] == 'inspection_opened'}
    feedback = [row for row in rows if row[2] == 'feedback']
    helpful = [row[3] for row in feedback if row[3] is not None]
    completed = [row[4] for row in feedback if row[4] is not None]
    negative = [row for row in feedback if row[3] == 0]
    positive_count = sum(row[3] == 1 for row in feedback)
    total_votes = positive_count + len(negative)
    reason_counts = {code: sum(code in _reasons(row[5]) for row in negative) for code in FEEDBACK_REASONS}
    return {'status': status, 'event_count': len(rows),
            'session_count': len({row[0] for row in rows}),
            'active_users': None, 'retention': None,
            'answers_rendered': len(rendered), 'inspections_opened': len(inspected),
            'inspection_rate': _rate(len(rendered & inspected), len(rendered)),
            'helpfulness': _rate(sum(helpful), len(helpful)),
            'task_completion': _rate(sum(completed), len(completed)),
            'feedback': {'positive': positive_count, 'negative': len(negative), 'total': total_votes,
                'positive_rate': positive_count / total_votes if total_votes else None,
                'negative_with_reasons': sum(bool(_reasons(row[5])) for row in negative),
                'reason_counts': [{'reason': code, 'label': label, 'count': reason_counts[code]}
                                  for code, label in FEEDBACK_REASONS.items()],
                'cleared_or_unvoted': sum(row[3] is None for row in feedback),
                'meaning': 'Explicit per-answer feedback; distinct votes, not unique users. Reasons describe negative votes only.'},
            'limitations': ['Local opt-in usage events and explicitly submitted anonymous feedback, not unique users or production adoption.',
                'Helpfulness and completion are voluntary self-reports with selection bias; completion is not independently verified.',
                'Unobserved rates are unavailable. Scripted benchmark cases are excluded from this event log.']}
