"""The package exports its names lazily; these tests pin that the public API did not change shape."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import assert_type

import cc_blackbox
from cc_blackbox import ActiveSession, ClaudeProfile, SessionTranscript

INIT = Path(cc_blackbox.__file__)


def _type_checking_imports() -> dict[str, str]:
    """The names the ``if TYPE_CHECKING:`` block imports, mapped to their module."""
    tree = ast.parse(INIT.read_text(encoding="utf-8"))
    names: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            for statement in node.body:
                assert isinstance(statement, ast.ImportFrom) and statement.module is not None
                for alias in statement.names:
                    names[alias.asname or alias.name] = statement.module
    return names


def test_every_name_in_all_resolves() -> None:
    for name in cc_blackbox.__all__:
        assert getattr(cc_blackbox, name) is not None, name


def test_type_checking_imports_and_lazy_map_agree() -> None:
    exports: dict[str, str] = getattr(cc_blackbox, "_EXPORTS")  # noqa: B009 - private, read on purpose
    assert _type_checking_imports() == exports
    assert sorted(exports) == sorted(cc_blackbox.__all__)


def test_star_import_and_dir_cover_all() -> None:
    namespace: dict[str, object] = {}
    exec("from cc_blackbox import *", namespace)
    assert set(cc_blackbox.__all__) <= set(namespace)
    assert set(cc_blackbox.__all__) <= set(dir(cc_blackbox))


def test_unknown_name_raises_attribute_error() -> None:
    try:
        getattr(cc_blackbox, "no_such_name")  # noqa: B009
    except AttributeError as error:
        assert "no_such_name" in str(error)
    else:
        raise AssertionError("expected AttributeError")


def test_import_loads_no_submodule() -> None:
    code = "import sys, cc_blackbox; print(__import__('json').dumps(sorted(sys.modules)))"
    loaded = json.loads(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout)
    assert [m for m in loaded if m.startswith("cc_blackbox.")] == []
    assert "yaml" not in loaded


def test_type_checkers_see_the_real_types() -> None:
    # assert_type is a no-op at run time; mypy --strict fails this file if a name were typed as Any.
    def check(session: ActiveSession, transcript: SessionTranscript) -> None:
        assert_type(session.pid, int)
        assert_type(transcript.session_id, str)

    assert_type(cc_blackbox.__version__, str)
    assert callable(check)
    assert ClaudeProfile.__module__ == "cc_blackbox.profiles"
