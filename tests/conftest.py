from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Variables a Claude Code session exports to its children. Tests often run inside such a session, so every test
# except the live contract test starts without them and with an empty home dir.
_SESSION_ENV = (
    "CLAUDE_CONFIG_DIR",
    "FP_CC_AUTH_SWITCH_PROFILE",
    "CLAUDE_PID",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_TMPDIR",
)


@pytest.fixture(autouse=True)
def home(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if request.node.get_closest_marker("live") is not None:
        return Path.home()  # the live contract test runs against the real Claude Code setup
    for name in _SESSION_ENV:
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home.resolve()


@pytest.fixture
def socket_dir() -> Iterator[Path]:
    """A short temp dir for Unix sockets.

    AF_UNIX socket paths are limited to 104 bytes on macOS, and a pytest tmp_path under /private/var/folders is
    already close to that, so a socket path inside it fails with "AF_UNIX path too long".
    """
    path = Path(tempfile.mkdtemp(prefix="cc-socks-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)
