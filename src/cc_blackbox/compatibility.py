"""Whether a running session uses the record fields and protocol this package understands.

The check looks at what a session actually declares, never at its Claude Code version number. Claude Code updates
often and usually keeps these interfaces stable, so a newer Claude Code that still writes the same fields and speaks
the same peer protocol just works. Unknown extra record fields and unknown peer features are additions, and do not
make a session incompatible.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from cc_blackbox.sessions.active import ActiveSession

__all__ = [
    "SUPPORTED_PEER_PROTOCOLS",
    "TESTED_CLAUDE_CODE_VERSIONS",
    "Compatibility",
    "CompatibilityFeature",
    "check_compatibility",
]

SUPPORTED_PEER_PROTOCOLS: frozenset[int] = frozenset({1})
"""The ``peerProtocol`` values whose message frames this package can write."""

TESTED_CLAUDE_CODE_VERSIONS: tuple[str, ...] = ("2.1.319", "2.1.321")
"""The Claude Code versions the live contract test passed against. Informational only: nothing compares versions."""

CompatibilityFeature = Literal["messaging", "scratchpad", "liveness"]


@dataclass(frozen=True, slots=True)
class Compatibility:
    """The result of :func:`check_compatibility`."""

    compatible: bool
    """``True`` when every feature is aligned."""
    features: Mapping[CompatibilityFeature, bool]
    """For each feature, whether the session's record provides what the feature needs."""
    reasons: tuple[str, ...]
    """One sentence for each problem found, prefixed with the feature name. Empty when ``compatible`` is ``True``."""


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _messaging(raw: Mapping[str, Any]) -> list[str]:
    reasons = []
    protocol = raw.get("peerProtocol")
    supported = ", ".join(map(str, sorted(SUPPORTED_PEER_PROTOCOLS)))
    if protocol is None:
        reasons.append(f"messaging: the record has no peerProtocol, and this cc-blackbox version speaks {supported}.")
    elif not _int(protocol) or protocol not in SUPPORTED_PEER_PROTOCOLS:
        reasons.append(
            f"messaging: the session speaks peerProtocol {protocol!r}, and this cc-blackbox version speaks "
            f"{supported}. Upgrade cc-blackbox."
        )
    if not _nonempty_str(raw.get("messagingSocketPath")):
        reasons.append("messaging: the record has no messagingSocketPath, so the session has messaging turned off.")
    return reasons


def _scratchpad(raw: Mapping[str, Any]) -> list[str]:
    reasons = []
    if not _nonempty_str(raw.get("sessionId")):
        reasons.append("scratchpad: the record has no sessionId string.")
    cwd = raw.get("cwd")
    if not _nonempty_str(cwd) or cwd == "?":
        reasons.append("scratchpad: the record has no cwd string.")
    return reasons


def _liveness(raw: Mapping[str, Any]) -> list[str]:
    reasons = []
    if not _int(raw.get("pid")):
        reasons.append("liveness: the record has no integer pid.")
    if sys.platform.startswith("linux") and not _nonempty_str(raw.get("procStart")):
        reasons.append("liveness: the record has no procStart string, so a reused pid cannot be detected.")
    return reasons


def check_compatibility(session: ActiveSession) -> Compatibility:
    """Check whether ``session``'s record provides what each feature of this package needs.

    - ``messaging`` needs a ``peerProtocol`` in :data:`SUPPORTED_PEER_PROTOCOLS` and a ``messagingSocketPath``.
    - ``scratchpad`` needs ``sessionId`` and ``cwd`` strings.
    - ``liveness`` needs an integer ``pid``, and on Linux a ``procStart`` string.

    The Claude Code version in the record is not considered.
    """
    raw = session.raw
    checks: dict[CompatibilityFeature, list[str]] = {
        "messaging": _messaging(raw),
        "scratchpad": _scratchpad(raw),
        "liveness": _liveness(raw),
    }
    reasons = tuple(reason for found in checks.values() for reason in found)
    return Compatibility(
        compatible=not reasons,
        features=MappingProxyType({feature: not found for feature, found in checks.items()}),
        reasons=reasons,
    )
