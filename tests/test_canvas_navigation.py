"""Sidebar handoffs select existing canvas content without injecting user text."""
import unittest
from unittest.mock import patch

from pe_copilot.ui.navigation import (
    CHAT_TAB, DISCOVERY_TAB, SCENARIO_TAB, INTERVIEW_TAB, migrate_tab,
    render_canvas_anchor, render_pending_canvas_focus,
)


class CanvasNavigationTests(unittest.TestCase):
    def test_old_chat_selection_migrates_to_one_of_the_two_remaining_tabs(self):
        self.assertEqual(migrate_tab(CHAT_TAB), DISCOVERY_TAB)
        self.assertEqual(migrate_tab(DISCOVERY_TAB), DISCOVERY_TAB)
        self.assertEqual(migrate_tab(SCENARIO_TAB), SCENARIO_TAB)
        self.assertEqual(migrate_tab('Tab 3: Supply Chain'), SCENARIO_TAB)
        self.assertEqual(migrate_tab(None), DISCOVERY_TAB)

    def test_plain_labels_preserve_existing_company_scenario_and_evaluation_tabs(self):
        self.assertEqual(migrate_tab('🏛️ Tab 1: Company & Material Discovery'), DISCOVERY_TAB)
        self.assertEqual(migrate_tab('🕸️ Tab 2: Scenario Analysis'), SCENARIO_TAB)
        self.assertEqual(migrate_tab('📊 Tab 3: Interview Evaluation', interview_enabled=True), INTERVIEW_TAB)
        self.assertEqual(migrate_tab('📊 Tab 3: Interview Evaluation', interview_enabled=False), SCENARIO_TAB)
        self.assertEqual(migrate_tab('Tab 3: Supply Chain', interview_enabled=True), SCENARIO_TAB)

    @patch('pe_copilot.ui.navigation.st.html')
    def test_focus_is_consumed_once_without_changing_selected_work_or_tab(self, html):
        request = {'section': 'impact', 'nonce': 'one-action'}
        state = {'canvas_focus_request': request, 'workbench_tab': SCENARIO_TAB,
                 'sim_results': {'run': 'existing'}}
        self.assertTrue(render_pending_canvas_focus(state))
        self.assertNotIn('canvas_focus_request', state)
        self.assertEqual(state['workbench_tab'], SCENARIO_TAB)
        self.assertEqual(state['sim_results'], {'run': 'existing'})
        self.assertTrue(html.call_args.kwargs['unsafe_allow_javascript'])
        self.assertIn("targetId = 'copilot-canvas-impact'", html.call_args.args[0])
        self.assertIn("fallbackId = 'copilot-canvas-assumptions'", html.call_args.args[0])
        state['canvas_focus_request'] = request
        self.assertFalse(render_pending_canvas_focus(state))
        self.assertEqual(html.call_count, 1)
        state['canvas_focus_request'] = {'section': 'impact', 'nonce': 'next-action'}
        self.assertTrue(render_pending_canvas_focus(state))
        self.assertEqual(html.call_count, 2)

    @patch('pe_copilot.ui.navigation.st.html')
    def test_untrusted_section_is_rejected_and_nonce_never_enters_html(self, html):
        for request in (
            'impact', {'section': [], 'nonce': 'x'},
            {'section': "impact';alert(1)", 'nonce': 'x'},
            {'section': 'impact', 'nonce': ''}, {'section': 'impact', 'nonce': 1},
            {'section': 'impact', 'nonce': 'x' * 257},
        ):
            state = {'canvas_focus_request': request}
            self.assertFalse(render_pending_canvas_focus(state))
            self.assertNotIn('canvas_focus_request', state)
        html.assert_not_called()
        nonce = '</script><script>alert("untrusted nonce")</script>'
        self.assertTrue(render_pending_canvas_focus({'canvas_focus_request': {'section': 'graph', 'nonce': nonce}}))
        self.assertNotIn(nonce, html.call_args.args[0])
        self.assertNotIn('untrusted nonce', html.call_args.args[0])

    @patch('pe_copilot.ui.navigation.st.html')
    def test_anchor_accepts_only_supported_canvas_sections(self, html):
        for section in ('overview', 'exposures', 'materials', 'assumptions', 'graph', 'impact', 'forecast'):
            render_canvas_anchor(section)
            self.assertIn(f'id="copilot-canvas-{section}"', html.call_args.args[0])
            self.assertIn('tabindex="-1"', html.call_args.args[0])
            self.assertNotIn('unsafe_allow_javascript', html.call_args.kwargs)
        with self.assertRaises(ValueError):
            render_canvas_anchor('unrecognized')


if __name__ == '__main__':
    unittest.main()
