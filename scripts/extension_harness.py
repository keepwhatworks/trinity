#!/usr/bin/env python3
"""Drive the REAL Trinity Chrome extension against the REAL Trinity pages —
the way the founder loads them — but over localhost so it can be automated.

WHY THIS EXISTS
---------------
The founder loads the launchpad / live council page in Chrome with the real
unpacked extension and the real Native-Messaging host. Two things make that
hard to reproduce in a test:

  * MCP/Playwright can't drive `file://` pages (Chrome's file-URL restrictions),
    and
  * the dispatch path (page → extension → Native-Messaging host → CLI) only
    runs with the actual extension loaded.

But the manifest's `externally_connectable` allows `http://127.0.0.1:*` AND
`file:///*`, and `isLaunchpadSender` (background.js) accepts a localhost page at
the `/review_pages/live_council.html` path — so serving the real page over
localhost reproduces the EXACT dispatch path the founder hits. The unpacked
extension loaded from this repo's `browser-extension/` gets the SAME id Chrome
derives for the founder (`caaojjh…`, path-derived), so the baked `extensionId`
in the page matches and `chrome.runtime.sendMessage(extensionId, …)` lands.

WHAT IT DOES
------------
1. Copies ~/.trinity to a temp dir (the real lens is NEVER touched) and
   regenerates launchpad.html + live_council.html there with the CURRENT code.
2. Seeds a synthetic completed council so the live page shows a finished round
   with the Refine / Continue / Auto-chain buttons enabled.
3. Loads the real extension in Playwright Chromium, serves the temp home over
   http://127.0.0.1, and (best-effort) registers a STUB Native-Messaging host so
   a chain dispatch actually writes a status file — WITHOUT spending council
   quota. (If Chrome-for-Testing's native-host dir can't be located, the run
   still validates routing + captures the wire payload.)
4. Hooks `chrome.runtime.sendMessage` on the page to record the EXACT payload
   the page forwards to the extension — so you can SEE the dispatch contract
   (e.g. that the chain token is sent under `status_token`, the 2026-06-12 bug).
5. Optionally drives a button (--drive) or stays open (--keep-open).

USAGE
-----
    .venv/bin/python scripts/extension_harness.py --drive continue
    .venv/bin/python scripts/extension_harness.py --page launchpad --keep-open

Requires the [test] extras (Playwright + chromium). Headed Chromium is launched
in new-headless mode; no window appears.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "browser-extension"
sys.path.insert(0, str(REPO / "src"))


def derived_extension_id(path: str) -> str:
    """Chrome's path-derived id for an unpacked extension: sha256(abspath),
    first 16 bytes, each nibble mapped 0-f → a-p."""
    h = hashlib.sha256(path.encode("utf-8")).hexdigest()
    return "".join(chr(ord("a") + int(c, 16)) for c in h[:32])


EXT_ID = derived_extension_id(str(EXT))
HOST_NAME = "local.trinity.capture"  # must match background.js NATIVE_HOST

SEED_TOKEN = "harness_seed"
SEED_COUNCIL_ID = "council_harness_seed"


def _prepare_home() -> Path:
    """Copy ~/.trinity to a temp home + regenerate the pages with current code.
    The real ~/.trinity is read-only here — nothing mutates the live lens."""
    real = Path(os.environ.get("TRINITY_HOME", Path.home() / ".trinity"))
    tmp = Path(tempfile.mkdtemp(prefix="trin_harness_"))
    home = tmp / ".trinity"
    # Copy only what the pages read — keeps it fast and avoids huge corpora.
    home.mkdir(parents=True)
    for sub in ("portal_pages", "review_pages", "council_outcomes", "memories",
                "scoreboard", "settings"):
        src = real / sub
        if src.exists():
            shutil.copytree(src, home / sub, dirs_exist_ok=True)
    (home / "portal_pages" / "status").mkdir(parents=True, exist_ok=True)
    (home / "council_outcomes").mkdir(parents=True, exist_ok=True)

    os.environ["TRINITY_HOME"] = str(home)
    os.environ["TRINITY_AUTOSCAN_DISABLED"] = "1"
    # Regenerate the pages with the CURRENT (fixed) code. force=True is REQUIRED:
    # the copytree above brought the real home's (possibly stale) live_council.html,
    # and write_live_council_page() skips an existing file by default (the
    # anti-stale-overwrite guard). Without force the harness would serve the OLD
    # page — exactly the trap that hid the dispatch fix on the first run.
    from trinity_local.council_review import write_live_council_page
    write_live_council_page(force=True)

    _seed_completed_council(home)
    return home


def _seed_completed_council(home: Path) -> None:
    """Write a synthetic completed status .js + outcome .js so the live page
    renders a finished round with chain buttons (no real council needed)."""
    from trinity_local.council_status import write_council_status

    write_council_status(
        SEED_TOKEN,
        status="completed",
        task_text="[harness] seed council — drive Refine/Continue from here",
        bundle_id="bundle_harness_seed",
        council_id=SEED_COUNCIL_ID,
        metadata={"members": ["claude", "codex"]},
    )
    # Minimal outcome .js so _loadOutcomeIntoSegment can populate councilId.
    outcome = {
        "council_run_id": SEED_COUNCIL_ID,
        "task_text": "[harness] seed council",
        "winner_provider": "claude",
        "primary_provider": "claude",
        "metadata": {"council_id": SEED_COUNCIL_ID, "round_number": 1},
        "routing_label": {"winner": "claude", "confidence": "high",
                          "agreed_claims": ["seed"], "disagreed_claims": []},
        "member_results": [],
    }
    oc = home / "council_outcomes" / f"{SEED_COUNCIL_ID}.js"
    oc.write_text(
        "window.__TRINITY_COUNCIL_OUTCOME__ = window.__TRINITY_COUNCIL_OUTCOME__ || {};\n"
        f"window.__TRINITY_COUNCIL_OUTCOME__[{json.dumps(SEED_COUNCIL_ID)}] = "
        f"{json.dumps(outcome, separators=(',', ':'))};\n",
        encoding="utf-8",
    )


def _serve(directory: Path):
    handler = functools.partial(_QuietHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - silence per-request noise
        pass


# A self-contained stub Native-Messaging host. Speaks the 4-byte-LE-length wire,
# LOGS every received payload, and for a council-iterate writes a completed
# status under the requested token so the page's poller resolves. Paths are
# baked in so it needs no env/PYTHONPATH.
_STUB_HOST = r'''#!/usr/bin/env python3
import sys, struct, json, os
LOG = {log!r}
STATUS_DIR = {status_dir!r}
def read_msg():
    raw = sys.stdin.buffer.read(4)
    if len(raw) != 4: return None
    n = struct.unpack('<I', raw)[0]
    return json.loads(sys.stdin.buffer.read(n) or b'null')
def write_msg(obj):
    b = json.dumps(obj).encode()
    sys.stdout.buffer.write(struct.pack('<I', len(b))); sys.stdout.buffer.write(b); sys.stdout.buffer.flush()
msg = read_msg()
with open(LOG, 'a') as f: f.write(json.dumps(msg) + '\n')
kind = (msg or {{}}).get('kind')
resp = {{'ok': True}}
if kind in ('council-iterate', 'launch-council'):
    token = (msg or {{}}).get('status_token') or (msg or {{}}).get('status-token')
    if token:
        payload = {{'status': 'completed', 'task_text': '[harness] chain round',
                   'council_id': 'council_' + token, 'status_token': token,
                   'members': {{'claude': {{'status': 'done'}}}},
                   'synthesis': {{'status': 'done'}}}}
        p = os.path.join(STATUS_DIR, 'council_status_' + token + '.js')
        with open(p, 'w') as f:
            f.write('window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {{}};\n')
            f.write('window.__TRINITY_COUNCIL_STATUS__[' + json.dumps(token) + '] = ' + json.dumps(payload) + ';\n')
        resp = {{'ok': True, 'detached': True, 'status_token': token}}
elif kind == 'trinity-ping' or (msg or {{}}).get('type') == 'trinity-ping':
    resp = {{'ok': True, 'type': 'trinity-pong'}}
write_msg(resp)
'''


def _register_stub_host(home: Path, log_path: Path, user_data_dir: Path) -> list[tuple[Path, str | None]]:
    """Write the stub host + its manifest to the candidate native-host dirs that
    are SAFE (never the real Google/Chrome dir, which holds the founder's real
    manifest — clobbering it is the #265 hazard). Returns (manifest, backup) so
    the caller can restore. self-reports which dir the extension actually reads."""
    stub_dir = home.parent / "stubhost"
    stub_dir.mkdir(exist_ok=True)
    host_path = stub_dir / "stub_host.py"
    host_path.write_text(
        _STUB_HOST.format(log=str(log_path), status_dir=str(home / "portal_pages" / "status")),
        encoding="utf-8",
    )
    host_path.chmod(host_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    appsup = Path.home() / "Library" / "Application Support"
    candidates = [
        user_data_dir / "NativeMessagingHosts",
        appsup / "Google" / "Chrome for Testing" / "NativeMessagingHosts",
        appsup / "Chromium" / "NativeMessagingHosts",
    ]
    manifest = {
        "name": HOST_NAME, "description": "Trinity harness stub host",
        "path": str(host_path), "type": "stdio",
        "allowed_origins": [f"chrome-extension://{EXT_ID}/"],
    }
    written: list[tuple[Path, str | None]] = []
    for cand in candidates:
        # Guard: refuse to touch the real Chrome dir even if a path resolves there.
        if "Google/Chrome/NativeMessagingHosts" in str(cand):
            continue
        cand.mkdir(parents=True, exist_ok=True)
        mpath = cand / f"{HOST_NAME}.json"
        backup = mpath.read_text() if mpath.exists() else None
        mpath.write_text(json.dumps(manifest), encoding="utf-8")
        written.append((mpath, backup))
    return written


def _restore_hosts(written: list[tuple[Path, str | None]]) -> None:
    for mpath, backup in written:
        try:
            if backup is None:
                mpath.unlink(missing_ok=True)
            else:
                mpath.write_text(backup)
        except OSError:
            pass


# Hooks chrome.runtime.sendMessage BEFORE the dispatcher loads, recording every
# payload the page forwards to the extension — the dispatch contract, on the wire.
_WIRE_HOOK = """
(() => {
  window.__WIRE__ = [];
  const real = chrome.runtime.sendMessage.bind(chrome.runtime);
  chrome.runtime.sendMessage = function(...args) {
    // (extensionId, message, cb) for external sends; (message, cb) for internal.
    const msg = (typeof args[0] === 'string') ? args[1] : args[0];
    if (msg && (msg.type === 'action' || msg.kind)) window.__WIRE__.push(msg);
    return real(...args);
  };
})();
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--page", choices=["council", "launchpad"], default="council")
    ap.add_argument("--transport", choices=["localhost", "file"], default="localhost",
                    help="serve over http://127.0.0.1 (default) or open the page via "
                         "file:// — the founder's exact surface. Both clear isLaunchpadSender.")
    ap.add_argument("--drive", choices=["continue", "refine", "autochain", "stop", "launch", "all", "none"],
                    default="continue", help="which action to drive: continue/refine/autochain/stop "
                    "on the council page; launch or all on the launchpad")
    ap.add_argument("--keep-open", action="store_true",
                    help="leave the browser open (sleeps) so you can inspect manually")
    ap.add_argument("--strip-query", action="store_true",
                    help="open the council page with NO ?status_token (what macOS hands the "
                         "browser for a file:// open) after the host writes the sidecar — "
                         "exercises the real 'Open council page' link, not a hand-built URL")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed — run: pip install -e '.[test]' && playwright install chromium")
        return 2

    home = _prepare_home()
    log_path = home.parent / "host_log.jsonl"
    log_path.write_text("", encoding="utf-8")
    user_data = home.parent / "profile"
    user_data.mkdir(exist_ok=True)
    written_hosts = _register_stub_host(home, log_path, user_data)

    if args.strip_query and args.page == "council":
        # Reproduce the founder's "Open council page" path: the host writes the
        # _active_council.js sidecar, then the OS opens the page with the query
        # STRIPPED. We open the bare URL → the page must recover via the sidecar.
        os.environ["TRINITY_HOME"] = str(home)
        from trinity_local import capture_host as _ch
        _ch._open_council_page({"status_token": SEED_TOKEN, "task": "strip-query harness",
                                "members": ["claude", "codex"]})
        print("strip-query: wrote _active_council.js sidecar; opening BARE page")

    httpd = None
    council_q = "" if args.strip_query else f"?status_token={SEED_TOKEN}&members=claude,codex"
    if args.transport == "file":
        # The founder's exact surface: a file:// page under .../.trinity/. The
        # extension's externally_connectable + isLaunchpadSender both accept it.
        rel = ("review_pages/live_council.html" if args.page == "council"
               else "portal_pages/launchpad.html")
        suffix = council_q if args.page == "council" else ""
        url = (home / rel).as_uri() + suffix
        base = f"file://{home}"
    else:
        httpd, port = _serve(home)
        base = f"http://127.0.0.1:{port}"
        if args.page == "council":
            url = f"{base}/review_pages/live_council.html{council_q}"
        else:
            url = f"{base}/portal_pages/launchpad.html"

    print(f"extension id : {EXT_ID}  (founder match: {EXT_ID == 'caaojjhagginmgobdaheincllmblcjoi'})")
    print(f"temp home    : {home}")
    print(f"serving      : {base}")
    print(f"opening      : {url}")
    print(f"stub host in : {[str(m.parent) for m, _ in written_hosts]}")

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(user_data), headless=False,
                args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--headless=new"],
            )
            if not ctx.service_workers:
                try:
                    ctx.wait_for_event("serviceworker", timeout=8000)
                except Exception:
                    pass
            sw = ctx.service_workers[0].url if ctx.service_workers else "NONE"
            print(f"service worker: {sw}")

            console: list[str] = []
            page = ctx.new_page()
            page.on("console", lambda m: console.append(f"[{m.type}] {m.text}"[:300]))
            page.on("pageerror", lambda e: console.append(f"[pageerror] {str(e)[:300]}"))
            page.goto(url, wait_until="networkidle", timeout=20000)
            # Install the wire hook once chrome.runtime exists.
            page.evaluate(_WIRE_HOOK)
            time.sleep(1.0)  # let the seed council render + buttons enable

            if args.page == "council" and args.drive != "none":
                _drive_chain(page, args.drive)
            elif args.page == "launchpad" and args.drive == "all":
                _drive_launchpad_all(page, url)
            elif args.page == "launchpad" and args.drive != "none":
                _drive_launchpad_launch(page)

            time.sleep(2.5)  # let dispatch + poll settle

            wire = page.evaluate("() => window.__WIRE__ || []")
            host_log = [json.loads(l) for l in log_path.read_text().splitlines() if l.strip()]
            new_status = sorted(
                f.name for f in (home / "portal_pages" / "status").glob("council_status_chain_*.js")
            ) + sorted(
                f.name for f in (home / "portal_pages" / "status").glob("council_status_council_chain_*.js")
            )

            print("\n=== DISPATCH WIRE (payloads the page sent the extension) ===")
            for w in wire:
                print("  ", json.dumps(w))
                if w.get("kind") == "council-iterate":
                    print("     status_token key present:", "status_token" in w,
                          "| hyphen key present:", "status-token" in w)
            print("\n=== HOST RECEIVED (stub native host log) ===")
            for h in host_log:
                print("  ", json.dumps(h))
            print("\n=== NEW chain status files written by the host ===")
            print("  ", new_status or "(none — stub host not reached; routing-only validation)")
            errs = [c for c in console if c.startswith("[error]") or c.startswith("[pageerror]")]
            print("\n=== console errors ===")
            print("  ", errs or "(none)")

            if args.keep_open:
                print("\n--keep-open: sleeping 600s. Ctrl-C to stop.")
                try:
                    time.sleep(600)
                except KeyboardInterrupt:
                    pass
            ctx.close()
    finally:
        if httpd is not None:
            httpd.shutdown()
        _restore_hosts(written_hosts)
    return 0


def _drive_launchpad_launch(page) -> None:
    """Type a question + click Launch Council on the landing page."""
    inp = page.query_selector("input[placeholder*='council question'], textarea[placeholder*='council question']")
    if inp:
        inp.fill("harness: name one real tradeoff in cache invalidation")
    btn = page.query_selector("button:has-text('Launch Council')")
    if not btn:
        print("!! could not find the 'Launch Council' button on the launchpad")
        return
    print("clicking: Launch Council")
    btn.click()


def _drive_launchpad_all(page, url: str) -> None:
    """Click EVERY reachable landing-page action button. A settings dispatch
    RELOADS the page (real behavior to reflect the new setting), which would
    wipe the modal mid-drive — so each settings-modal action gets a fresh
    navigation. Each click's dispatch lands in the stub host log, which the
    caller prints as the matrix. Idle-state actions needing extra state
    (lens-stop → active build; stop-council → running council; import confirm →
    a real probe result) are reported skipped; they're contract-audited clean."""
    import time as _t

    def _try(label, selector, *, fill=None, force=False):
        loc = page.locator(selector).first
        if loc.count() == 0:
            print(f"  [skip] {label}: not present in idle state")
            return False
        try:
            if fill is not None:
                loc.fill(fill, timeout=4000, force=True)
            else:
                loc.click(timeout=4000, force=force)
            print(f"  [click] {label}")
            _t.sleep(0.8)
            return True
        except Exception as e:
            print(f"  [err ] {label}: {str(e)[:80]}")
            return False

    def _reopen():
        page.goto(url, wait_until="networkidle", timeout=20000)
        page.evaluate(_WIRE_HOOK)
        _t.sleep(0.6)

    # Top cards (rapid clicks; each may reload, but the click lands first).
    _drive_launchpad_launch(page)
    _t.sleep(0.8)
    _reopen(); _try("Refresh memory (dream)", "button:has-text('Refresh memory')")
    _reopen(); _try("Repair extension", "button:has-text('Repair extension')")
    _reopen(); _try("Save as PNG card (me-card)", "button:has-text('Save as PNG card')")
    # Import (import-export-dry-run / import-export): the path input sits deep in
    # a long page and isn't reliably interactable under headless Playwright
    # actionability, and a force-fill doesn't fire petite-vue's v-model event
    # that enables Probe. Driven via the contract audit instead (the payload
    # shape {kind, path} is pinned there); skipped here to keep the run honest.
    print("  [skip] Import (dry-run/confirm): input not interactable headless; contract-audited clean")
    # Each settings action reloads → drive each from a fresh page.
    _reopen()
    if _try("Open settings", "button[title='Settings']"):
        _try("Ingest once", "button[title='Ingest transcripts once now']")
    _reopen()
    if _try("Open settings", "button[title='Settings']"):
        _try("Reset anonymous id (telemetry-reset-id)", "button[aria-label='Reset anonymous ID']")
    _reopen()
    if _try("Open settings", "button[title='Settings']"):
        _try("Toggle sharing (telemetry-enable/disable)", ".sharing-toggle .toggle-switch", force=True)


def _drive_chain(page, action: str) -> None:
    """Click the requested chain control on the live council page."""
    sel = {
        "continue": "button:has-text('Continue')",
        "autochain": "button:has-text('Auto-chain')",
        "refine": "button:has-text('Refine')",
        "stop": "button:has-text('Stop')",
    }[action]
    if action == "refine":
        inp = page.query_selector(".chain-refine-input")
        if inp:
            inp.fill("tighten the argument")
    btn = page.query_selector(sel)
    if not btn:
        print(f"!! could not find the '{action}' button on the page "
              "(seed council may not have rendered chain controls)")
        return
    print(f"clicking: {action}")
    btn.click()


if __name__ == "__main__":
    raise SystemExit(main())
