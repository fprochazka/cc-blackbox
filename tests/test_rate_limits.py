from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from records import SAMPLE_RECORD, make_profile_dir, write_record

from cc_blackbox import (
    RATE_LIMITS_FILE,
    ClaudeProfile,
    list_active_sessions,
    list_profiles,
    read_rate_limits,
)

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)
NOW_S = int(NOW.timestamp())
SESSION = "5f0c3a52-8d4e-4c1b-9a7e-2b6d1e0f4a11"


def window(used: float, resets_in_s: int, *, observed_ago_s: int = 60, **extra: Any) -> dict[str, Any]:
    return {
        "used_percentage": used,
        "resets_at": NOW_S + resets_in_s,
        "observed_at": NOW_MS - observed_ago_s * 1000,
        "changed_at": NOW_MS - observed_ago_s * 1000 - 5000,
        "session_id": SESSION,
        **extra,
    }


def write_file(config_dir: Path, data: dict[str, Any] | str) -> Path:
    path = config_dir / RATE_LIMITS_FILE
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def file_data(**windows: Any) -> dict[str, Any]:
    return {
        "v": 1,
        "profile": "work",
        "written_at": NOW_MS - 30_000,
        "session_id": SESSION,
        "model": "claude-opus-5-5",
        "claude_version": "2.1.325",
        "windows": windows,
    }


@pytest.fixture
def work(home: Path) -> ClaudeProfile:
    make_profile_dir(home / ".claude")
    (home / ".claude" / "auth-switch.yaml").write_text("profiles:\n  work: {}\n", encoding="utf-8")
    make_profile_dir(home / ".claude-profiles" / "work")
    return next(p for p in list_profiles() if p.name == "work")


def test_the_file_is_read_into_windows(work: ClaudeProfile) -> None:
    spend = window(120.5, 86_400 * 10, used_usd=602.5, limit_usd=500, period="monthly")
    seven_day = window(41, 86_400 * 3, observed_ago_s=600)
    path = write_file(work.config_dir, file_data(five_hour=window(23.5, 3600), seven_day=seven_day, spend_limit=spend))

    limits = read_rate_limits(work)

    assert limits is not None
    assert limits.path == path
    assert (limits.profile_name, limits.session_id, limits.model, limits.claude_version) == (
        "work",
        SESSION,
        "claude-opus-5-5",
        "2.1.325",
    )
    assert limits.written_at == datetime.fromtimestamp((NOW_MS - 30_000) / 1000, tz=UTC)
    assert limits.measured_at == datetime.fromtimestamp((NOW_MS - 60_000) / 1000, tz=UTC)
    assert limits.five_hour is not None
    assert limits.five_hour.used_percent == 23.5
    assert limits.five_hour.resets_at == datetime.fromtimestamp(NOW_S + 3600, tz=UTC)
    assert limits.five_hour.is_reset(NOW) is False
    assert limits.five_hour.session_id == SESSION
    assert limits.seven_day is not None and limits.seven_day.used_percent == 41.0
    assert limits.spend_limit is not None and limits.spend_limit.used_percent == 120.5
    assert limits.spend_limit.raw["period"] == "monthly"
    assert sorted(limits.windows) == ["five_hour", "seven_day", "spend_limit"]


def test_a_window_whose_reset_time_passed_is_reported_as_reset(work: ClaudeProfile) -> None:
    write_file(work.config_dir, file_data(five_hour=window(87, -600, observed_ago_s=7200), seven_day=window(40, 3600)))

    limits = work.rate_limits()

    assert limits is not None and limits.five_hour is not None and limits.seven_day is not None
    assert limits.five_hour.is_reset(NOW) is True
    assert limits.five_hour.used_percent == 87.0
    assert limits.seven_day.is_reset(NOW) is False
    # The same snapshot asked later: the weekly window has reset too.
    assert limits.seven_day.is_reset(datetime.fromtimestamp(NOW_S + 3600, tz=UTC)) is True
    # Without a moment, the current time decides; these reset times are long past.
    assert limits.seven_day.is_reset() is True


def test_old_values_stay_valid_until_their_window_resets(work: ClaudeProfile) -> None:
    write_file(work.config_dir, file_data(seven_day=window(12, 86_400 * 6, observed_ago_s=86_400 * 2)))

    limits = read_rate_limits(work)

    assert limits is not None and limits.seven_day is not None
    assert (limits.seven_day.used_percent, limits.seven_day.is_reset(NOW)) == (12.0, False)


def test_unknown_windows_are_kept_and_invalid_ones_left_out(work: ClaudeProfile) -> None:
    write_file(
        work.config_dir,
        file_data(
            five_hour={**window(10, 3600), "used_percentage": "10"},
            seven_day={**window(10, 3600), "used_percentage": True},
            weekly_opus=window(5, 86_400),
            spend_limit={**window(1, 3600), "observed_at": None},
        ),
    )

    limits = read_rate_limits(work)

    assert limits is not None
    assert list(limits.windows) == ["weekly_opus"]
    assert limits.five_hour is None and limits.seven_day is None and limits.spend_limit is None


def test_a_reset_time_out_of_range_is_dropped_but_the_value_kept(work: ClaudeProfile) -> None:
    write_file(work.config_dir, file_data(five_hour={**window(10, 3600), "resets_at": 99}))

    limits = read_rate_limits(work)

    assert limits is not None and limits.five_hour is not None
    assert (limits.five_hour.used_percent, limits.five_hour.resets_at) == (10.0, None)
    # Without a reset time a window never counts as reset.
    assert limits.five_hour.is_reset(datetime(2100, 1, 1, tzinfo=UTC)) is False


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        json.dumps({**file_data(), "v": 2}),
        json.dumps({**file_data(), "v": None}),
        json.dumps({**file_data(), "written_at": "yesterday"}),
    ],
)
def test_an_unreadable_file_gives_none(work: ClaudeProfile, content: str) -> None:
    write_file(work.config_dir, content)

    assert read_rate_limits(work) is None


def test_a_naive_moment_is_refused(work: ClaudeProfile) -> None:
    write_file(work.config_dir, file_data(five_hour=window(10, 3600)))
    limits = read_rate_limits(work)
    assert limits is not None and limits.five_hour is not None

    with pytest.raises(ValueError, match="timezone-aware"):
        limits.five_hour.is_reset(datetime(2026, 11, 19, 12, 0))


def test_a_profile_without_the_file_has_none(work: ClaudeProfile) -> None:
    assert work.rate_limits() is None
    assert read_rate_limits(work.config_dir / "missing") is None


def test_an_active_session_has_the_rate_limits_of_its_profile(work: ClaudeProfile) -> None:
    write_file(work.config_dir, file_data(five_hour=window(33, 3600)))
    write_record(work.config_dir, 4242, {**SAMPLE_RECORD, "pid": 4242})

    [session] = list_active_sessions(check_liveness=False)

    assert session.rate_limits is not None
    assert session.rate_limits.five_hour is not None and session.rate_limits.five_hour.used_percent == 33.0
