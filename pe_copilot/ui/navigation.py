"""Analysis tabs, optional interview evaluation, and bounded canvas focus."""
from collections.abc import Mapping
from hashlib import sha256

import streamlit as st


DISCOVERY_TAB = 'Tab 1: Company & Material Discovery'
SCENARIO_TAB = 'Tab 2: Scenario Analysis'
INTERVIEW_TAB = 'Tab 3: Evaluation'
_LEGACY_TAB_LABELS = {
    '🏛️ Tab 1: Company & Material Discovery': DISCOVERY_TAB,
    '🕸️ Tab 2: Scenario Analysis': SCENARIO_TAB,
    '📊 Tab 3: Interview Evaluation': INTERVIEW_TAB,
    'Tab 3: Interview Evaluation': INTERVIEW_TAB,
}
# Retain the old label only to migrate saved sessions; chat now lives in the sidebar.
CHAT_TAB = '💬 Copilot & Evaluation'

_SECTIONS = {
    'overview': ('Financial overview', 'overview'),
    'exposures': ('Material exposure evidence', 'overview'),
    'materials': ('Select materials', 'overview'),
    'assumptions': ('Scenario assumptions', 'assumptions'),
    'graph': ('Supply-chain scenario map', 'assumptions'),
    'impact': ('Financial impact', 'assumptions'),
    'forecast': ('Material prices and forecasts', 'assumptions'),
}


def migrate_tab(label, interview_enabled=False):
    if isinstance(label, str):
        label = _LEGACY_TAB_LABELS.get(label, label)
    if label == INTERVIEW_TAB and interview_enabled:
        return label
    if label in (DISCOVERY_TAB, SCENARIO_TAB):
        return label
    return SCENARIO_TAB if isinstance(label, str) and ('Tab 2' in label or 'Tab 3' in label) else DISCOVERY_TAB


def render_workbench_tabs(interview_enabled=False):
    """Add the optional dashboard without resetting any analysis widget."""
    requested = st.session_state.pop('requested_tab', st.session_state.get('workbench_tab'))
    if migrate_tab(requested, interview_enabled=True) == INTERVIEW_TAB and not interview_enabled:
        requested = st.session_state.get('interview_previous_analysis_tab', SCENARIO_TAB)
    selected = migrate_tab(requested, interview_enabled=interview_enabled)
    if selected in (DISCOVERY_TAB, SCENARIO_TAB):
        st.session_state['interview_previous_analysis_tab'] = selected
    st.session_state['workbench_tab'] = selected
    labels = [DISCOVERY_TAB, SCENARIO_TAB] + ([INTERVIEW_TAB] if interview_enabled else [])
    return st.tabs(labels, key='workbench_tab', on_change='rerun')


def render_canvas_anchor(section):
    """Place a keyboard-focusable destination without changing canvas content."""
    if section not in _SECTIONS:
        raise ValueError('Unknown canvas section')
    label = _SECTIONS[section][0]
    st.html(f'<div id="copilot-canvas-{section}" tabindex="-1" aria-label="{label}" '
            'style="height:0;scroll-margin-top:5rem;outline:none"></div>')


def render_pending_canvas_focus(state):
    """Consume one focus request after the application has rendered both tabs.

    The caller sets ``requested_tab`` before the rerun. Only fixed application
    IDs enter the script; the opaque request nonce is hashed, never executed.
    A bounded retry allows Streamlit to finish selecting/rendering the tab.
    Missing content (for example, a result not yet calculated) falls back to
    the beginning of the same tab, where its existing guidance is displayed.
    """
    request = state.get('canvas_focus_request')
    if request is None:
        return False
    if not isinstance(request, Mapping):
        state.pop('canvas_focus_request', None)
        return False
    section, nonce = request.get('section'), request.get('nonce')
    if not isinstance(section, str) or section not in _SECTIONS or not isinstance(nonce, str) or not nonce.strip() or len(nonce) > 256:
        state.pop('canvas_focus_request', None)
        return False
    token = sha256((section + ':' + nonce).encode()).hexdigest()
    if token == state.get('_canvas_last_focus_token'):
        state.pop('canvas_focus_request', None)
        return False
    fallback = _SECTIONS[section][1]
    script = """<script>
(() => {
  const token = '__TOKEN__';
  const targetId = 'copilot-canvas-__SECTION__';
  const fallbackId = 'copilot-canvas-__FALLBACK__';
  if (window.__copilotCanvasFocusToken === token) return;
  window.__copilotCanvasFocusToken = token;
  let attempts = 0;
  const visible = id => {
    const element = document.getElementById(id);
    return element && element.getClientRects().length ? element : null;
  };
  const move = () => {
    if (window.__copilotCanvasFocusToken !== token) return;
    const target = visible(targetId) || (attempts >= 40 ? visible(fallbackId) : null);
    if (target) {
      target.focus({preventScroll: true});
      target.scrollIntoView({behavior: 'auto', block: 'start', inline: 'nearest'});
      return;
    }
    if (++attempts < 60) window.setTimeout(move, 50);
  };
  window.requestAnimationFrame(move);
})();
</script>"""
    st.html(script.replace('__TOKEN__', token).replace('__SECTION__', section).replace('__FALLBACK__', fallback),
            unsafe_allow_javascript=True)
    state['_canvas_last_focus_token'] = token
    state.pop('canvas_focus_request', None)
    return True
