"""Session transcripts: where Claude Code keeps them, how to read them and what their records mean."""

from cc_blackbox.transcripts.layout import (
    ProjectsDir,
    is_session_file_name,
    is_session_id,
    list_projects_dirs,
    session_id_for_path,
)

__all__ = [
    "ProjectsDir",
    "is_session_file_name",
    "is_session_id",
    "list_projects_dirs",
    "session_id_for_path",
]
