from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from pe_copilot.chat.adoption import (
    FEEDBACK_REASONS, anonymous_session_id, record_event, record_feedback, summarize_events,
)


class AnswerFeedbackTests(unittest.TestCase):
    def test_old_database_migrates_without_losing_completion_or_usage_events(self):
        with TemporaryDirectory() as root:
            session, answer = anonymous_session_id(), 'old-answer'
            connection = sqlite3.connect(Path(root) / 'events.sqlite')
            try:
                connection.execute('''CREATE TABLE events (session_id TEXT, answer_hash TEXT, event TEXT,
                    helpful INTEGER, task_completed INTEGER, created_at TEXT, updated_at TEXT,
                    PRIMARY KEY (session_id, answer_hash, event))''')
                connection.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?)',
                    (session, sha256(answer.encode()).hexdigest(), 'feedback', 0, 1, 'old', 'old'))
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(summarize_events(root)['feedback']['negative'], 1)
            record_feedback(session_id=session, answer_id=answer, helpful=False,
                            reasons=['missing_evidence'], root=root)
            record_event(enabled=True, session_id=session, event='answer_rendered', answer_id='new-answer', root=root)
            result = summarize_events(root)
            self.assertEqual(result['feedback']['negative_with_reasons'], 1)
            self.assertEqual(result['task_completion']['value'], 1)
            self.assertEqual(result['answers_rendered'], 1)

    def test_changed_vote_deduplicates_and_positive_vote_clears_negative_reasons(self):
        with TemporaryDirectory() as root:
            common = {'session_id': anonymous_session_id(), 'answer_id': 'answer-1', 'root': root}
            record_feedback(**common, helpful=False, reasons=['wrong_scope', 'missing_evidence', 'wrong_scope'])
            duplicate = record_feedback(**common, helpful=False, reasons=['missing_evidence', 'wrong_scope'])
            self.assertEqual(duplicate, {'status': 'duplicate', 'stored': False})
            self.assertEqual(record_feedback(**common, helpful=False)['status'], 'duplicate')
            before = summarize_events(root)
            self.assertEqual(before['event_count'], 1)
            self.assertEqual(before['feedback']['negative_with_reasons'], 1)
            self.assertEqual({row['reason']: row['count'] for row in before['feedback']['reason_counts']}['wrong_scope'], 1)
            record_feedback(**common, helpful=True, reasons=['wrong_scope'])
            after = summarize_events(root)
            self.assertEqual((after['feedback']['positive'], after['feedback']['negative'], after['feedback']['total']), (1, 0, 1))
            self.assertTrue(all(row['count'] == 0 for row in after['feedback']['reason_counts']))
            self.assertEqual(after['answers_rendered'], 0)

    def test_clear_removes_vote_and_reasons_but_retains_previous_task_completion(self):
        with TemporaryDirectory() as root:
            common = {'session_id': anonymous_session_id(), 'answer_id': 'answer-1', 'root': root}
            record_event(**common, enabled=True, event='feedback', task_completed=True)
            record_feedback(**common, helpful=False, reasons=['unclear_answer'])
            self.assertEqual(record_feedback(**common, helpful=None)['status'], 'cleared')
            result = summarize_events(root)
            self.assertEqual(result['feedback']['total'], 0)
            self.assertIsNone(result['feedback']['positive_rate'])
            self.assertEqual(result['feedback']['negative_with_reasons'], 0)
            self.assertEqual(result['task_completion']['value'], 1)
            other = {**common, 'answer_id': 'answer-2'}
            record_feedback(**other, helpful=True)
            record_feedback(**other, helpful=None)
            self.assertEqual(summarize_events(root)['event_count'], 1)
            self.assertFalse(record_feedback(**other, helpful=None)['stored'])

    def test_reason_allowlist_never_stores_free_text_or_answer_identifier(self):
        with TemporaryDirectory() as root:
            common = {'session_id': anonymous_session_id(), 'answer_id': 'opaque-answer-identifier', 'root': root}
            for reasons in (['PRIVATE SOURCE'], 'incorrect_facts', ['other'] * 9, [{'text': 'PRIVATE'}]):
                with self.assertRaises(ValueError):
                    record_feedback(**common, helpful=False, reasons=reasons)
            record_feedback(**common, helpful=False, reasons=list(FEEDBACK_REASONS))
            connection = sqlite3.connect(Path(root) / 'events.sqlite')
            try:
                rows = connection.execute('SELECT * FROM events').fetchall()
            finally:
                connection.close()
            contents = json.dumps(rows)
            self.assertNotIn('PRIVATE', contents)
            self.assertNotIn('opaque-answer-identifier', contents)
            self.assertEqual(summarize_events(root)['feedback']['negative_with_reasons'], 1)

    def test_disabled_usage_and_storage_failures_do_not_enable_tracking(self):
        with TemporaryDirectory() as root:
            common = {'session_id': anonymous_session_id(), 'answer_id': 'answer-1', 'root': root}
            record_feedback(**common, helpful=True)
            result = record_event(**common, enabled=False, event='answer_rendered')
            self.assertEqual(result['status'], 'disabled')
            self.assertEqual(summarize_events(root)['answers_rendered'], 0)
            blocked = Path(root) / 'file-not-directory'
            blocked.write_text('placeholder')
            self.assertEqual(record_feedback(**{**common, 'root': blocked}, helpful=False)['status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
