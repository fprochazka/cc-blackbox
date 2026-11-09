"""Typed views of ``tool_input`` for the built-in tools whose input the hooks reference documents.

A view is a convenience over ``tool_input``, which always stays available. Imported only by
:meth:`~cc_blackbox.hooks.ToolHookInput.typed_tool_input`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeAlias


def _text(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _integer(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _flag(data: Mapping[str, Any], key: str) -> bool | None:
    value = data.get(key)
    return value if isinstance(value, bool) else None


def _strings(data: Mapping[str, Any], key: str) -> tuple[str, ...] | None:
    value = data.get(key)
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else None


@dataclass(frozen=True, slots=True, kw_only=True)
class ShellInput:
    """The input of ``Bash`` and ``PowerShell``, which share their fields."""

    command: str
    description: str | None
    timeout_ms: int | None
    run_in_background: bool | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadInput:
    file_path: str
    """Absolute; Claude Code expands ``~`` and relative paths before hooks run. Backslashes on Windows."""
    offset: int | None
    limit: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class WriteInput:
    file_path: str
    """Absolute, as for :class:`ReadInput`."""
    content: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class EditInput:
    file_path: str
    """Absolute, as for :class:`ReadInput`."""
    old_string: str | None
    new_string: str | None
    replace_all: bool | None


@dataclass(frozen=True, slots=True, kw_only=True)
class GlobInput:
    pattern: str
    path: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class GrepInput:
    pattern: str
    path: str | None
    glob: str | None
    output_mode: str | None
    case_insensitive: bool | None
    """The ``-i`` field."""
    multiline: bool | None


@dataclass(frozen=True, slots=True, kw_only=True)
class WebFetchInput:
    url: str
    prompt: str | None
    offset: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class WebSearchInput:
    query: str
    allowed_domains: tuple[str, ...] | None
    blocked_domains: tuple[str, ...] | None
    mode: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentInput:
    prompt: str
    description: str | None
    subagent_type: str | None
    model: str | None


ToolInputView: TypeAlias = (
    ShellInput
    | ReadInput
    | WriteInput
    | EditInput
    | GlobInput
    | GrepInput
    | WebFetchInput
    | WebSearchInput
    | AgentInput
)


def _shell(data: Mapping[str, Any]) -> ShellInput | None:
    command = _text(data, "command")
    if command is None:
        return None
    return ShellInput(
        command=command,
        description=_text(data, "description"),
        timeout_ms=_integer(data, "timeout"),
        run_in_background=_flag(data, "run_in_background"),
    )


def _read(data: Mapping[str, Any]) -> ReadInput | None:
    path = _text(data, "file_path")
    if path is None:
        return None
    return ReadInput(file_path=path, offset=_integer(data, "offset"), limit=_integer(data, "limit"))


def _write(data: Mapping[str, Any]) -> WriteInput | None:
    path = _text(data, "file_path")
    return None if path is None else WriteInput(file_path=path, content=_text(data, "content"))


def _edit(data: Mapping[str, Any]) -> EditInput | None:
    path = _text(data, "file_path")
    if path is None:
        return None
    return EditInput(
        file_path=path,
        old_string=_text(data, "old_string"),
        new_string=_text(data, "new_string"),
        replace_all=_flag(data, "replace_all"),
    )


def _glob(data: Mapping[str, Any]) -> GlobInput | None:
    pattern = _text(data, "pattern")
    return None if pattern is None else GlobInput(pattern=pattern, path=_text(data, "path"))


def _grep(data: Mapping[str, Any]) -> GrepInput | None:
    pattern = _text(data, "pattern")
    if pattern is None:
        return None
    return GrepInput(
        pattern=pattern,
        path=_text(data, "path"),
        glob=_text(data, "glob"),
        output_mode=_text(data, "output_mode"),
        case_insensitive=_flag(data, "-i"),
        multiline=_flag(data, "multiline"),
    )


def _web_fetch(data: Mapping[str, Any]) -> WebFetchInput | None:
    url = _text(data, "url")
    if url is None:
        return None
    return WebFetchInput(url=url, prompt=_text(data, "prompt"), offset=_integer(data, "offset"))


def _web_search(data: Mapping[str, Any]) -> WebSearchInput | None:
    query = _text(data, "query")
    if query is None:
        return None
    return WebSearchInput(
        query=query,
        allowed_domains=_strings(data, "allowed_domains"),
        blocked_domains=_strings(data, "blocked_domains"),
        mode=_text(data, "mode"),
    )


def _agent(data: Mapping[str, Any]) -> AgentInput | None:
    prompt = _text(data, "prompt")
    if prompt is None:
        return None
    return AgentInput(
        prompt=prompt,
        description=_text(data, "description"),
        subagent_type=_text(data, "subagent_type"),
        model=_text(data, "model"),
    )


_VIEWS: dict[str, Callable[[Mapping[str, Any]], ToolInputView | None]] = {
    "Bash": _shell,
    "PowerShell": _shell,
    "Read": _read,
    "Write": _write,
    "Edit": _edit,
    "Glob": _glob,
    "Grep": _grep,
    "WebFetch": _web_fetch,
    "WebSearch": _web_search,
    "Agent": _agent,
}


def view_for(tool_name: str | None, tool_input: Mapping[str, Any]) -> ToolInputView | None:
    view = _VIEWS.get(tool_name or "")
    return None if view is None else view(tool_input)
