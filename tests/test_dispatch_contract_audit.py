"""COMPREHENSIVE dispatch-contract audit across EVERY action on the launchpad +
council pages — the generalized guard for the 2026-06-12 "council never started"
bug class.

That bug: a page emitted `extensionAction: {kind:'council-iterate', 'status-token':…}`
but capture_host reads `payload['status_token']`. The hyphen key was silently
dropped → the action ran wrong. A guard that pins one payload's shape (we have
those) can't catch the NEXT divergence on a DIFFERENT action. This one can: it
extracts every dispatch payload the pages emit and checks, for each:

  1. `kind` is a real capture_host.ACTION_ALLOWLIST entry (else it silently
     no-ops at the host — `_run_action` returns "unknown action").
  2. Every data-carrying key in the payload is a key the host actually READS for
     that kind (its arg_spec json_field OR the hyphen CLI-flag spelling, both of
     which capture_host now accepts). A key the host doesn't read is dropped on
     the floor — exactly the original bug.

If someone adds a new dispatch button, or renames a CLI flag, or fat-fingers a
key, this reds — for ANY action, on EITHER page, without a per-action test.
"""
from __future__ import annotations

import re

from trinity_local import capture_host


def _host_readable_keys(kind: str) -> set[str] | None:
    """The keys capture_host reads for `kind`: every arg's json_field AND its
    hyphen CLI-flag spelling (the host tolerates both), plus the literal `kind`.
    Returns None if the kind isn't an allowlisted CLI action."""
    entry = capture_host.ACTION_ALLOWLIST.get(kind)
    if entry is None:
        return None
    arg_spec = entry[1]
    keys = {"kind"}
    for arg_name, json_field, _required in arg_spec:
        keys.add(json_field)
        keys.add(arg_name)  # hyphen spelling
    return keys


def _extract_payloads(blob: str) -> list[tuple[str, set[str]]]:
    """Pull every `extensionAction: {…}` / `extensionAction = {…}` literal out of
    a chunk of JS (rendered HTML or the council_review source). Returns
    (kind_or_variable, top_level_keys). Any `extensionAction.X =` dynamic key
    assignment is attached to the NEAREST PRECEDING literal — i.e. the payload
    it actually mutates (council-iterate adds .prompt/.rounds; stop-council does
    not), so a key is never mis-attributed across dispatch sites."""
    literals: list[tuple[int, str, set[str]]] = []  # (start, kind, keys)
    for m in re.finditer(r"extensionAction\s*[:=]\s*\{", blob):
        i = m.end() - 1
        depth = 0
        j = i
        while j < len(blob):
            if blob[j] == "{":
                depth += 1
            elif blob[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        block = blob[i : j + 1]
        kind_m = re.search(r"kind:\s*'([a-z-]+)'", block)
        kind = kind_m.group(1) if kind_m else "<variable>"
        keys: set[str] = set()
        d = 0
        for km in re.finditer(r"([{}])|([a-zA-Z_]+)\s*:", block):
            if km.group(1) == "{":
                d += 1
            elif km.group(1) == "}":
                d -= 1
            elif km.group(2) and d == 1:
                keys.add(km.group(2))
        literals.append((m.start(), kind, keys))

    # Attach each dynamic `extensionAction.X =` to the nearest literal before it.
    for dm in re.finditer(r"extensionAction\.([a-zA-Z_]+)\s*=", blob):
        pos = dm.start()
        owner = None
        for idx, (start, _k, _keys) in enumerate(literals):
            if start <= pos:
                owner = idx
            else:
                break
        if owner is not None:
            literals[owner][2].add(dm.group(1))
    return [(kind, keys) for _start, kind, keys in literals]


def _audit(blob: str, *, label: str) -> list[str]:
    """Return a list of contract violations found in `blob` (empty = clean)."""
    violations: list[str] = []
    for kind, keys in _extract_payloads(blob):
        if kind == "<variable>":
            # Variable kind (e.g. settings toggles via entry.extensionKind) — its
            # concrete values are audited separately in the settings-entries test.
            continue
        readable = _host_readable_keys(kind)
        if readable is None:
            violations.append(
                f"[{label}] kind '{kind}' is not in capture_host.ACTION_ALLOWLIST "
                "→ this dispatch silently no-ops at the host"
            )
            continue
        for key in keys - {"kind"}:
            if key not in readable:
                violations.append(
                    f"[{label}] kind '{kind}' sends key '{key}' the host never reads "
                    f"(reads: {sorted(readable - {'kind'})}) → silently dropped "
                    "(the 2026-06-12 'council never started' bug class)"
                )
    return violations


def _render_launchpad() -> str:
    from trinity_local.launchpad_template import render_launchpad_html

    return render_launchpad_html(page_data={})


def _render_live_council() -> str:
    from trinity_local.council_review import render_live_council_page

    return render_live_council_page()


def _council_source() -> str:
    from pathlib import Path

    return (Path(capture_host.__file__).parent / "council_review.py").read_text(encoding="utf-8")


def test_launchpad_dispatch_contract_is_clean():
    violations = _audit(_render_launchpad(), label="launchpad")
    assert not violations, "launchpad dispatch contract violations:\n  " + "\n  ".join(violations)


def test_live_council_dispatch_contract_is_clean():
    violations = _audit(_render_live_council(), label="live_council")
    assert not violations, "live council dispatch contract violations:\n  " + "\n  ".join(violations)


def test_council_source_dispatch_contract_is_clean():
    # Covers BOTH council Vue apps (live + unified single-council) — they share
    # this one source module, so the f-string templates carry every council
    # dispatch site. `{{`/`}}` in the source reduce to `{`/`}` for our brace scan.
    violations = _audit(_council_source(), label="council_review.py")
    assert not violations, "council source dispatch contract violations:\n  " + "\n  ".join(violations)


def test_settings_toggle_extension_kinds_are_allowlisted():
    """The privacy toggles dispatch `kind: entry.extensionKind` (a server-provided
    value, not a literal). Audit those concrete kinds against the allowlist."""
    from pathlib import Path

    src = Path(capture_host.__file__).parent / "launchpad_data.py"
    kinds = set(re.findall(r'"extensionKind":\s*"([a-z-]+)"', src.read_text(encoding="utf-8")))
    assert kinds, "no extensionKind values found — the settings-entry shape changed"
    for kind in kinds:
        assert kind in capture_host.ACTION_ALLOWLIST, (
            f"settings toggle dispatches kind '{kind}' that the host doesn't allowlist "
            "→ the toggle silently no-ops"
        )
    # Sanity: the known privacy toggles are present.
    assert {"telemetry-enable", "telemetry-disable", "telemetry-reset-id"} <= kinds


def test_audit_would_catch_the_original_bug():
    # Meta-guard: a payload using the hyphen CLI-flag spelling for a key whose
    # json_field is underscore must be... ACCEPTED now (the host tolerates both),
    # but a payload using a key the host reads under NEITHER spelling must be
    # flagged. Prove the auditor actually fires on a true unknown key.
    bad = "dispatcher.dispatch({ extensionAction: { kind: 'council-iterate', councle: x } });"
    assert _audit(bad, label="synthetic"), "the auditor failed to flag an unknown key (it's vacuous)"
    good = "dispatcher.dispatch({ extensionAction: { kind: 'council-iterate', council: x } });"
    assert not _audit(good, label="synthetic"), "the auditor false-flagged a valid key"
