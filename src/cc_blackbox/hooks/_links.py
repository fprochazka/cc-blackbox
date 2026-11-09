"""Links from a hook payload to the transcript and the running session. Each import happens inside the function, so
parsing a payload never loads the session, transcript or profile code."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cc_blackbox.sessions.active import ActiveSession
    from cc_blackbox.sessions.stored import SessionTranscript, SubagentTranscript


def transcript_of(transcript_path: str | None) -> SessionTranscript | None:
    if not transcript_path:
        return None
    from cc_blackbox.errors import TranscriptNotFound
    from cc_blackbox.sessions.stored import read_session_transcript

    try:
        return read_session_transcript(os.path.expanduser(transcript_path))
    except TranscriptNotFound:
        return None


def agent_transcript_of(transcript_path: str | None, agent_transcript_path: str | None) -> SubagentTranscript | None:
    transcript = transcript_of(transcript_path)
    if transcript is None or not agent_transcript_path:
        return None
    wanted = os.path.realpath(os.path.expanduser(agent_transcript_path))
    return next((s for s in transcript.subagents() if os.path.realpath(s.path) == wanted), None)


def active_session_of(session_id: str | None) -> ActiveSession | None:
    if not session_id:
        return None
    from cc_blackbox.sessions.active import current_session, list_active_sessions

    current = current_session()
    if current is not None and current.session_id == session_id:
        return current
    return next((s for s in list_active_sessions() if s.session_id == session_id), None)
