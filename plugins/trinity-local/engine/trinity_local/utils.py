from __future__ import annotations

import hashlib
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path


# Standing constraint #43102d25 — error strings that reach a user-facing surface
# must be TYPE-only honest: NEVER leak a raw `{exc}` carrying an absolute
# filesystem path, an `[Errno N]`, a Python type name, or a traceback frame. Two
# independent paths feed user-painted error copy from a raw `str(exc)`:
#   • the capture host's dispatch error (popup "Failed: <error>" + launchpad
#     dispatch ribbon) — capture_host._safe_error,
#   • a council MEMBER's failure detail (`update_member_failure(..., str(exc))`)
#     which lands in `members.<p>.reasoning_summary` and is painted by the live
#     council page / launchpad running card as the failed member's row.
# `safe_error_message` is the ONE choke point both go through so the redaction
# can't drift between surfaces.
_ABS_PATH_RX = re.compile(
    r"(?:[A-Za-z]:)?[/\\](?:Users|home|private|tmp|var|root|opt|usr|Library)"
    r"[/\\][^\s'\"]*"
)
_ERRNO_RX = re.compile(r"\[Errno\s*-?\d+\]\s*")
# A leading "pkg.mod.SomeError:" / "SomeError:" traceback line — capture the
# human-readable tail so a diagnosable summary survives without the type name.
_PY_EXC_RX = re.compile(
    r"^(?:[A-Za-z_][\w.]*\.)?([A-Z][A-Za-z0-9]*(?:Error|Exception|Warning))\s*:?\s*"
)


def safe_error_message(raw: object, *, fallback: str = "the command failed") -> str:
    """Map an arbitrary error string to a TYPE-only, leak-free message.

    Strips absolute filesystem paths, `[Errno N]` codes, a leading Python
    exception type name, and traceback frames — anything that leaks an internal
    or that a non-technical user can't act on. Keeps already-clean short
    messages (`bundle not found`, `usage limit reached`, hyphenated type slugs
    like `native-host-unavailable`) intact. Always returns a non-empty honest
    string so the surface never paints a raw traceback.
    """
    text = str(raw or "").strip()
    if not text:
        return fallback
    # Multi-line traceback → keep only the most informative (last non-empty,
    # non-frame) line; a "File ..." frame is pure path noise.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        non_frame = [ln for ln in lines if not ln.startswith('File "')
                     and not ln.startswith("Traceback")]
        text = (non_frame or lines)[-1]
    text = _PY_EXC_RX.sub("", text)
    text = _ERRNO_RX.sub("", text)
    text = _ABS_PATH_RX.sub("a local file", text)
    text = text.strip().strip(":").strip()
    text = re.sub(r"['\"]\s*a local file\s*['\"]", "a local file", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text or fallback


def finite_float_or_none(value: object) -> float | None:
    """Coerce a JSON-loaded scalar to a FINITE float, or ``None`` if it can't be.

    The single low-level shape-guard primitive shared by every render-path
    numeric reader (#304 corrupt-state-file class). A state file the user can
    hand-edit — ``scoreboard/picks.json``, a ``council_outcomes/*.json``
    ``provider_scores[...].overall``, an ``evals/results/*.json`` score — can
    carry a numeric field as a string (``"0.4"``), a bool, ``None``, a
    non-numeric string (``"abc"``), or a NaN/Inf (a poisoned float that
    ``json.loads`` happily accepts from the bare ``NaN``/``Infinity`` literal).
    A bare ``float(...)``/``int(...)`` on such a value raised ``ValueError`` that
    bubbled up and blanked the WHOLE surface (the routing cheat-sheet, the
    launchpad, the served portal); a surviving NaN serializes as bare ``NaN``
    and breaks the client's ``JSON.parse`` the same way.

    Returns a real finite ``float`` for a clean numeric/numeric-string value, or
    ``None`` so the caller can SKIP the field (keeping a mean honest) or fall
    back to a default (``launchpad_data._safe_number``). Lives in ``utils`` —
    the lowest-level home both ``personal_routing`` and ``launchpad_data``
    already import — so the guard is one source of truth and can't drift.
    """
    if isinstance(value, bool):
        # bool is an int subclass; a stray True/False is not a meaningful number.
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(value, str):
        try:
            f = float(value.strip())
        except (ValueError, TypeError):
            return None
        return None if math.isnan(f) or math.isinf(f) else f
    return None


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string with second precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_id(prefix: str, *parts: str) -> str:
    """Derive a deterministic short ID from a prefix and variable-length key parts."""
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write ``content`` to ``path`` atomically via tmp+rename.

    Why: ``path.write_text(content)`` can leave a half-written file on disk
    if the process is killed mid-write (crash, OOM, disk full, signal). The
    consumer then sees an invalid file at the canonical path. Atomic
    rename means readers always see either the old content or the full new
    content — never a partial write.

    Per-process tmp suffix (PID-stamped) avoids cross-process collisions
    where two concurrent writers share the same tmp file. Last-rename
    wins on the target path, but each writer's bytes were complete.

    Promoted to a single helper after Principle #17 audit found the same
    tmp+rename shape inlined in 5 places (cortex.py, cold_start.py,
    capture_host.py, incremental_ingest.py, council_runtime.py).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(content, encoding=encoding)
        tmp.replace(path)
    finally:
        # If the rename failed, clean up the leftover tmp so the dir
        # doesn't accumulate orphans on repeated failures.
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
