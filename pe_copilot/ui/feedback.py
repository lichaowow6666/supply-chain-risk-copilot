"""Explicit answer ratings; independent of optional passive usage measurement."""
from hashlib import sha256
import json

import streamlit as st


def answer_identity(message):
    """An opaque stable key; message content itself never enters the feedback store."""
    turn = message.get('turn_id')
    if isinstance(turn, str) and turn and len(turn) <= 256:
        return turn
    payload = {key: message.get(key) for key in ('question', 'text', 'scope')}
    return sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _keys(identifier):
    token = sha256(identifier.encode()).hexdigest()
    return 'answer_vote_' + token, 'answer_reasons_' + token


def _persist(identifier, helpful, reasons):
    try:
        from pe_copilot.chat.adoption import anonymous_session_id, record_feedback
        session = st.session_state.get('pilot_measurement_session_id')
        if not session:
            session = anonymous_session_id()
            st.session_state['pilot_measurement_session_id'] = session
        result = record_feedback(session_id=session, answer_id=identifier,
                                 helpful=helpful, reasons=reasons)
    except Exception:
        # Feedback must never interrupt the answer or the user's analysis.
        result = {'status': 'unavailable', 'stored': False}
    st.session_state.setdefault('answer_feedback', {})[identifier] = {
        'helpful': helpful, 'reasons': list(reasons), 'status': result['status']}


def _vote_changed(identifier):
    vote_key, reason_key = _keys(identifier)
    selected = st.session_state.get(vote_key)
    helpful = None if selected is None else selected == 1
    reasons = list(st.session_state.get(reason_key, [])) if helpful is False else []
    if helpful is not False:
        st.session_state[reason_key] = []
    _persist(identifier, helpful, reasons)


def _reasons_changed(identifier):
    _, reason_key = _keys(identifier)
    _persist(identifier, False, list(st.session_state.get(reason_key, [])))


def render_answer_feedback(message):
    from pe_copilot.chat.adoption import FEEDBACK_REASONS
    identifier = answer_identity(message)
    vote_key, reason_key = _keys(identifier)
    entry = st.session_state.get('answer_feedback', {}).get(identifier, {})
    # Application-owned ratings survive widget cleanup (tab/report changes).
    if vote_key not in st.session_state:
        value = entry.get('helpful')
        st.session_state[vote_key] = None if value is None else int(value)
    st.feedback('thumbs', key=vote_key, on_change=_vote_changed, args=(identifier,))
    if st.session_state.get(vote_key) == 0:
        if reason_key not in st.session_state:
            st.session_state[reason_key] = list(entry.get('reasons', []))
        st.multiselect('What missed your expectations?', list(FEEDBACK_REASONS),
                       format_func=FEEDBACK_REASONS.get, key=reason_key,
                       placeholder='Choose reasons (optional)',
                       on_change=_reasons_changed, args=(identifier,))
    if entry.get('status') == 'unavailable':
        st.caption('Feedback could not be saved.')
        if st.button('Retry feedback', key='retry_' + vote_key, type='tertiary'):
            _vote_changed(identifier)
            st.rerun()
