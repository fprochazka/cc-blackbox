from __future__ import annotations

from pathlib import Path

import pytest

from cc_blackbox.sessions import _tail


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"\n\n",
        b"one\ntwo\nthree\n",
        b"one\ntwo\nhalf-writ",
        b"short\n" + b"x" * 50 + b"\nend\n",
        b"y" * 40 + b"\n" + b"z" * 7 + b"\n",
        b"abcdefg\n" * 5,
        b"\n" + b"w" * 33,
    ],
)
@pytest.mark.parametrize("chunk", [1, 3, 8, 64])
def test_lines_are_yielded_last_first_whatever_the_chunk_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: bytes, chunk: int
) -> None:
    monkeypatch.setattr(_tail, "_CHUNK", chunk)
    path = tmp_path / "t.jsonl"
    path.write_bytes(content)

    expected = [line for line in reversed(content.split(b"\n")) if line.strip()]
    assert list(_tail._lines_backward(path)) == expected
