"""Session transcripts: where Claude Code keeps them, how to read them and what their records mean."""

from cc_blackbox.transcripts.entries import (
    KNOWN_ENTRY_TYPES,
    AssistantEntry,
    AttachmentEntry,
    ContentBlock,
    Entry,
    ImageBlock,
    ReadDiagnostics,
    SystemEntry,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UnknownBlock,
    Usage,
    UserEntry,
)
from cc_blackbox.transcripts.layout import (
    ProjectsDir,
    is_session_file_name,
    is_session_id,
    list_projects_dirs,
    session_id_for_path,
)
from cc_blackbox.transcripts.markup import (
    ShellCommandPart,
    SlashCommandPart,
    TextPart,
    UserPart,
    parse_user_markup,
)
from cc_blackbox.transcripts.prompts import (
    Prompt,
    PromptOrigin,
    prompt_from,
    prompts,
)
from cc_blackbox.transcripts.reader import iter_entries

__all__ = [
    "AssistantEntry",
    "AttachmentEntry",
    "ContentBlock",
    "Entry",
    "ImageBlock",
    "KNOWN_ENTRY_TYPES",
    "ProjectsDir",
    "Prompt",
    "PromptOrigin",
    "ReadDiagnostics",
    "ShellCommandPart",
    "SlashCommandPart",
    "SystemEntry",
    "TextBlock",
    "TextPart",
    "ThinkingBlock",
    "ToolResultBlock",
    "ToolUseBlock",
    "UnknownBlock",
    "Usage",
    "UserEntry",
    "UserPart",
    "is_session_file_name",
    "is_session_id",
    "iter_entries",
    "list_projects_dirs",
    "parse_user_markup",
    "prompt_from",
    "prompts",
    "session_id_for_path",
]
