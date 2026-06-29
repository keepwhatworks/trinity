"""The "Justified by · click to see the work" backref (the README "show its work"
pledge) must not blow a 320px side panel wide when the source decision carries a long
UNBREAKABLE token.

Founder-class lineage: the launchpad's recurring bug shape is a fixed-width / no-
overflow-wrap box that a long token blows past the viewport (see the running-council
320 overflow, the live-council token overflow, the eval/cortex/bc fixed-grid rows).
The lens "Justified by" backref under each paired lens expands a <details> showing the
privileged / sacrificed / verbatim source pair. That text is USER-CONTROLLED corpus
content — it comes from preference_acts.jsonl, derived from the user's own prompts —
so it can carry an unbreakable token (a ~/.cache/... path, a URL, a base64 blob, a
stack-trace line with no spaces). The expand block has `max-width: 600px` inline, which
does NOT constrain a long no-space token: without overflow-wrap the token demands its
full intrinsic width.

Real-panel drive at 320px BEFORE the fix: clicking the backref to "see the work"
produced documentElement.scrollWidth - clientWidth == 714 — the whole launchpad gained
714px of horizontal scroll, and the privileged token + the `pa_NNN` summary ran off the
right edge — precisely when the traceability feature is supposed to shine. A file:// /
http render at a wide window can't catch this (600px fits a 720px composer); only the
genuine narrow side panel reproduces it.

Fixed by the `.lens-decision-chip, .lens-decision-chip *` rule (min-width:0 +
overflow-wrap:anywhere + word-break:break-word + max-width:100% on the <details>):
every text descendant breaks anywhere and the flex/block boxes shrink below their
content's intrinsic min-width, so the chip stays inside the panel column.

Mutation-proven: reverting that CSS rule on the live built panel restored
docOverflow 714 + the privileged-token right edge off-screen — the founder
fixed-width-blowout symptom.

Slow + browser marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import json
import stat
import sys
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "browser-extension"
HOST = "local.trinity.capture"

# A realistic user-corpus token with NO spaces — a HF cache path + a base64-ish blob.
# This is the kind of string a user pastes into a prompt; the lens-build pipeline keeps
# it verbatim in preference_acts.jsonl, and the backref surfaces it.
_LONG_TOKEN = (
    "~/.cache/huggingface/hub/models--nomic-ai--modernbert-embed-base/snapshots/"
    "0a1b2c3d4e5f6789/config.json:line_847_column_22_in_the_tokenizer_special_tokens"
    "_map_blob_aGVsbG8gd29ybGQgdGhpcyBpcyBhIHZlcnkgbG9uZyBiYXNlNjQ"
)


def _seed_lens_with_long_decision(home: Path) -> None:
    """Augment the synthetic home so the taste card renders ONE paired lens whose
    'Justified by' backref resolves to a self_expressed preference act carrying a long
    unbreakable token in privileged / sacrificed / verbatim (the overflow stressor)."""
    me = home / "me"
    me.mkdir(parents=True, exist_ok=True)
    (me / "lenses.json").write_text(
        json.dumps(
            {
                "lenses": [
                    {
                        "pole_a": "concrete",
                        "pole_b": "abstract",
                        "failure_a": "vague",
                        "failure_b": "brittle",
                        "tension_decisions": ["pa_001"],
                        "dual_evidence": {},
                        "basins_spanned": ["b00"],
                        "verdict": "accepted",
                        "horizon": "tactical",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (me / "preference_acts.jsonl").write_text(
        json.dumps(
            {
                "id": "pa_001",
                "trigger": "self_expressed",
                "privileged": "keep_the_" + ("x" * 60) + "_path",
                "sacrificed": "drop_the_" + ("y" * 60) + "_path",
                "kind": "self_expressed",
                "basin": "b00",
                "context": _LONG_TOKEN,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _boot_panel(p, tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    _seed_lens_with_long_decision(home)
    from trinity_local.launchpad_page import build_launchpad_payload

    payload = build_launchpad_payload()
    pl = tmp_path / "payload.json"
    pl.write_text(json.dumps({"ok": True, **payload}, default=str), encoding="utf-8")

    stub = (
        "#!/usr/bin/env python3\n"
        "import sys, struct, json, os\n"
        f"os.environ['TRINITY_HOME'] = {str(home)!r}\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        "from trinity_local.capture_host import QUERY_HANDLERS\n"
        "raw = sys.stdin.buffer.read(4)\n"
        "msg = json.loads(sys.stdin.buffer.read(struct.unpack('<I',raw)[0]) or b'null') if len(raw)==4 else None\n"
        "msg = msg or {}\n"
        "qk = msg.get('query_kind')\n"
        "if qk == 'launchpad_data':\n"
        f"    out = open({str(pl)!r}).read().encode()\n"
        "elif qk in QUERY_HANDLERS:\n"
        "    out = json.dumps(QUERY_HANDLERS[qk](msg), default=str).encode()\n"
        "else:\n"
        "    out = json.dumps({'ok': True}).encode()\n"
        "sys.stdout.buffer.write(struct.pack('<I',len(out))); sys.stdout.buffer.write(out); sys.stdout.buffer.flush()\n"
    )
    ud = tmp_path / "profile"
    nm = ud / "NativeMessagingHosts"
    nm.mkdir(parents=True)
    hp = ud / "stub.py"
    hp.write_text(stub, encoding="utf-8")
    hp.chmod(hp.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    try:
        ctx = p.chromium.launch_persistent_context(
            str(ud),
            headless=False,
            args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--headless=new"],
        )
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"no launchable chromium: {exc}")

    sw = None
    for _ in range(50):
        if ctx.service_workers:
            sw = ctx.service_workers[0]
            break
        try:
            sw = ctx.wait_for_event("serviceworker", timeout=2000)
            break
        except Exception:
            time.sleep(0.1)
    assert sw, "extension service worker never registered (manifest invalid?)"
    ext_id = sw.url.split("/")[2]
    (nm / f"{HOST}.json").write_text(
        json.dumps(
            {
                "name": HOST,
                "description": "stub",
                "path": str(hp),
                "type": "stdio",
                "allowed_origins": [f"chrome-extension://{ext_id}/"],
            }
        ),
        encoding="utf-8",
    )

    page = ctx.new_page()
    page.set_viewport_size({"width": 320, "height": 760})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4500)
    return ctx, ext_id, page


def test_lens_decision_backref_long_token_fits_320px_panel(tmp_path, monkeypatch):
    """Expanding the 'Justified by · click to see the work' backref on a decision with a
    long unbreakable token must (a) actually EXPAND and (b) NOT add horizontal scroll to a
    320px side panel — every element inside the chip must keep its right edge inside the
    viewport. The founder fixed-width-blowout symptom: clicking to see the work scrolls the
    whole launchpad 714px sideways and the privileged token runs off the right edge.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"

            chips = lf.locator("details.lens-decision-chip")
            # Precondition: the backref chip must actually render (the seed wired a
            # tension_decision + a matching preference act) — else this guard is vacuous.
            assert chips.count() >= 1, (
                "the 'Justified by' backref <details> chip did not render — the taste card "
                "or the decision-ledger wiring regressed; this guard can't bite without it."
            )

            summary = lf.locator("details.lens-decision-chip summary").first
            summary.scroll_into_view_if_needed()
            summary.click(timeout=5000)
            page.wait_for_timeout(400)

            geo = lf.evaluate(
                r"""
                () => {
                  const vw = document.documentElement.clientWidth;
                  const docOverflow = document.documentElement.scrollWidth - vw;
                  const d = document.querySelector('details.lens-decision-chip');
                  const open = d ? d.open : null;
                  // Worst right-edge among the chip's text descendants (the unbreakable
                  // token lives in <strong>/<em>/<blockquote>/<div>).
                  let maxRight = 0, worstText = null;
                  if (d) {
                    d.querySelectorAll('*').forEach(el => {
                      const r = el.getBoundingClientRect();
                      if (r.width > 0 && r.right > maxRight) {
                        maxRight = r.right; worstText = (el.textContent || '').trim().slice(0, 30);
                      }
                    });
                  }
                  const rawLeak = /\{\{/.test(document.body.innerHTML);
                  return { vw, docOverflow, open, maxRight: Math.round(maxRight), worstText, rawLeak };
                }
                """
            )
            assert isinstance(geo, dict)
            assert geo.get("open") is True, (
                "clicking the 'Justified by' backref summary did NOT expand the native "
                "<details> in the side panel — the show-its-work interaction is dead."
            )
            assert geo.get("rawLeak") is False, "raw {{ }} template leaked in the expanded backref"

            # THE BITE — geometry, not a string check. With overflow-wrap stripped, the
            # 60-char privileged/sacrificed tokens + the no-space verbatim push the chip's
            # box past the viewport and the page scrolls sideways.
            doc_overflow = geo.get("docOverflow")
            assert doc_overflow is not None and doc_overflow <= 1, (
                "expanding the lens 'Justified by · click to see the work' backref on a "
                "long-unbreakable-token decision added horizontal scroll to the 320px side "
                f"panel (documentElement.scrollWidth exceeds clientWidth by {doc_overflow}px) "
                "— the founder fixed-width-blowout: a ~/.cache/... path / base64 blob in the "
                "verbatim demands its full intrinsic width because the expand block has no "
                "overflow-wrap. Keep .lens-decision-chip min-width:0 + overflow-wrap:anywhere."
            )
            vw = geo.get("vw")
            assert geo.get("maxRight", 0) <= vw + 1, (
                "an element inside the expanded 'show your work' backref has its right edge "
                f"past the 320px panel (maxRight={geo.get('maxRight')} > vw={vw}; "
                f"worst={geo.get('worstText')!r}) — the long privileged/verbatim token ran "
                "off the right edge instead of wrapping."
            )
        finally:
            ctx.close()
