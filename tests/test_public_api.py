"""The package exports its names lazily; these tests pin that the public API did not change shape."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import assert_type

import pytest

import cc_blackbox
import cc_blackbox.hooks
from cc_blackbox import ActiveSession, ClaudeProfile, SessionTranscript
from cc_blackbox.hooks import PreToolUseInput, PreToolUseReply

PACKAGES = [cc_blackbox, cc_blackbox.hooks]


def _type_checking_imports(package: ModuleType) -> dict[str, str]:
    """The names the ``if TYPE_CHECKING:`` block imports, mapped to their module."""
    tree = ast.parse(Path(str(package.__file__)).read_text(encoding="utf-8"))
    names: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            for statement in node.body:
                assert isinstance(statement, ast.ImportFrom) and statement.module is not None
                for alias in statement.names:
                    names[alias.asname or alias.name] = statement.module
    return names


@pytest.mark.parametrize("package", PACKAGES, ids=lambda p: p.__name__)
def test_every_name_in_all_resolves(package: ModuleType) -> None:
    for name in package.__all__:
        assert getattr(package, name) is not None, name


@pytest.mark.parametrize("package", PACKAGES, ids=lambda p: p.__name__)
def test_type_checking_imports_and_lazy_map_agree(package: ModuleType) -> None:
    exports: dict[str, str] = getattr(package, "_EXPORTS")  # noqa: B009 - private, read on purpose
    assert _type_checking_imports(package) == exports
    assert sorted(exports) == sorted(package.__all__)


@pytest.mark.parametrize("package", PACKAGES, ids=lambda p: p.__name__)
def test_star_import_and_dir_cover_all(package: ModuleType) -> None:
    namespace: dict[str, object] = {}
    exec(f"from {package.__name__} import *", namespace)
    assert set(package.__all__) <= set(namespace)
    assert set(package.__all__) <= set(dir(package))


@pytest.mark.parametrize("package", PACKAGES, ids=lambda p: p.__name__)
def test_unknown_name_raises_attribute_error(package: ModuleType) -> None:
    with pytest.raises(AttributeError, match="no_such_name"):
        getattr(package, "no_such_name")  # noqa: B009


def test_import_loads_no_submodule() -> None:
    code = "import sys, cc_blackbox; print(__import__('json').dumps(sorted(sys.modules)))"
    loaded = json.loads(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout)
    assert [m for m in loaded if m.startswith("cc_blackbox.")] == []
    assert "yaml" not in loaded


def test_type_checkers_see_the_real_types() -> None:
    # assert_type is a no-op at run time; mypy --strict fails this file if a name were typed as Any.
    def check(session: ActiveSession, transcript: SessionTranscript, event: PreToolUseInput) -> None:
        assert_type(session.pid, int)
        assert_type(transcript.session_id, str)
        assert_type(event.tool_use_id, str | None)
        assert_type(PreToolUseReply.deny("x"), PreToolUseReply)

    assert_type(cc_blackbox.__version__, str)
    assert callable(check)
    assert ClaudeProfile.__module__ == "cc_blackbox.profiles"
