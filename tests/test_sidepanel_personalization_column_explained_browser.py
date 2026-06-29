"""The /stats "Cheat-sheet · by task type" Personalization column must EXPLAIN itself.

UX-sweep finding: the by-task-type routing cheat-sheet renders a `Personalization`
column whose cells read a bare "18%  n=2" — but NOTHING on the surface said what
that percentage measured. The card prose explained the **Best** column and the
per-provider scores, and named neither the Personalization column nor what raises
it; the `<th>Personalization</th>` carried no tooltip; the memory-viewer routing
reader (the sibling surface) doesn't even render the column, so the launchpad was
the ONLY place the number appeared, and it appeared unexplained. An unlabeled "18%"
reads worse than no column — the user can't tell if it's a score, an error rate, or
a confidence, and can't tell how to move it. UNCLEAR-copy / orphan-value class.

The fix LEADS WITH THE ANSWER: the card prose now states "Personalization is how
much this pick leans on YOUR councils vs the global default — it climbs from near
0% toward 100% as you run more councils on that task type", the column header
carries that same explanation as a `title` tooltip, and each cell's `title`
restates it with the row's own n.

This drives the REAL extension side panel (the surface where the column actually
ships) at /stats over a SEEDED home (the seed lights the routing cheat-sheet with
n>=2 task types), and asserts (a) the Personalization column is rendered, (b) its
header `title` explains the metric in plain English, (c) the card prose names the
Personalization column (not just Best / per-provider scores).

Mutation-provable: revert the prose sentence + the header title and this reds (the
column ships as a bare unexplained "%"). Slow + browser marked; skips without
Playwright/chromium.
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


def _boot_panel(p, tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
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
            str(ud), headless=False,
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
    (nm / f"{HOST}.json").write_text(json.dumps({
        "name": HOST, "description": "stub", "path": str(hp), "type": "stdio",
        "allowed_origins": [f"chrome-extension://{ext_id}/"],
    }), encoding="utf-8")

    page = ctx.new_page()
    page.set_viewport_size({"width": 393, "height": 852})
    # Stub dispatch so no real council can fire from any click.
    page.add_init_script("window.__TRINITY_DISPATCH__ = async () => ({ ok: true });")
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)
    return ctx, ext_id, page


def test_personalization_column_explains_itself(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, ext_id, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            lf = page.frames[-1]
            assert "sandbox/launchpad.html" in (lf.url or ""), f"launchpad iframe missing: {lf.url}"
            # Flip to /stats (in-place toggle).
            lf.locator('a[href$="stats.html"]').first.click(timeout=5000)
            page.wait_for_timeout(1000)
            sf = page.frames[-1]

            probe = sf.evaluate(
                "()=>{"
                "const cards=[...document.querySelectorAll('.stats-card')];"
                "const card=cards.find(c=>(c.querySelector('.eyebrow')||{}).textContent"
                "  && (c.querySelector('.eyebrow').textContent.includes('by task type')));"
                "if(!card) return {found:false};"
                "const ths=[...card.querySelectorAll('table.routing-table thead th')];"
                "const pth=ths.find(t=>t.textContent.trim()==='Personalization');"
                "const prose=(card.querySelector('p.meta')||{}).textContent||'';"
                "return {found:true,"
                " hasColumn:!!pth,"
                " headerTitle:(pth&&pth.getAttribute('title'))||'',"
                " prose:prose.replace(/\\s+/g,' ').trim(),"
                " rowCount:card.querySelectorAll('table.routing-table tbody tr').length};"
                "}"
            )
            assert probe["found"], "the 'Cheat-sheet · by task type' card never rendered on /stats"
            # Precondition: the column IS present with rows (non-vacuous — the
            # seed lights n>=2 task types so the routing table actually paints).
            assert probe["hasColumn"], (
                "the Personalization column header is missing — the seed should light "
                f"the routing cheat-sheet table (rows={probe['rowCount']})"
            )
            assert probe["rowCount"] >= 1, (
                f"the routing cheat-sheet has no body rows to carry the column: {probe}"
            )

            # The DEFECT this guards: the column shipped a bare "%" with NO explanation.
            ht = probe["headerTitle"].lower()
            assert ht, (
                "the Personalization column header carries NO title tooltip — the bare "
                "'18% n=2' cell reads as an unexplained number (UNCLEAR-copy class): "
                f"{probe}"
            )
            assert "your councils" in ht and "global default" in ht, (
                "the Personalization header tooltip doesn't explain what the metric IS "
                "(leans-on-your-councils-vs-global-default): "
                f"{probe['headerTitle']!r}"
            )

            prose_l = probe["prose"].lower()
            assert "personalization" in prose_l, (
                "the card prose names Best + per-provider scores but NEVER the "
                "Personalization column — the only place the number appears, "
                f"unexplained: {probe['prose']!r}"
            )
            assert "global default" in prose_l, (
                "the card prose mentions 'Personalization' but doesn't say what it "
                f"measures (vs the global default): {probe['prose']!r}"
            )
        finally:
            ctx.close()
