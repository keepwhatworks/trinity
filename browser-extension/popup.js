// Trinity Local — popup glue.
//
// The popup is a small extension UI. Clicking "Open Trinity launchpad"
// opens the launchpad packaged IN the extension (chrome.runtime.getURL(
// 'launchpad.html')) — no `trinity-local serve`, no generated file:// page.
// That page fetches its data live from the capture host over Native
// Messaging (query_kind='launchpad_data') and dispatches councils through
// the same background.js forwarders the popup uses.
//
// "Send to council" launches a council *detached* (Popen + start_new_session
// in capture_host) and returns immediately with a client-generated
// status_token. The popup then polls capture_host's in-process
// `get-council-status` handler every ~1.5s and renders a status panel
// with rotating loading copy + per-member chips + synthesis row, the
// same vocabulary as the launchpad's running surface.
//
// If Native Messaging isn't wired (CLI not installed, manifest not
// registered), we show a friendly setup card instead of cryptic errors.

"use strict";

const $ = (id) => document.getElementById(id);

// Mirrors src/trinity_local/launchpad_data.py:COUNCIL_LOADING_MESSAGES VERBATIM
// (the launchpad reads these from pageData; the popup has none, so it hardcodes
// them). tests/test_popup_loading_messages_sync.py fails the build if this drifts
// from the Python source — it had silently lost the 8 Trinity-specific lines.
// Cycled every 2.5s while the council is running.
const COUNCIL_LOADING_MESSAGES = [
  "Reticulating splines...",
  "Generating witty dialog...",
  "Tokenizing real life...",
  "Convincing AI not to turn evil...",
  "Computing chance of success...",
  "Optimizing the optimizer...",
  "Keeping all the 1's and removing all the 0's...",
  "Pushing pixels...",
  "Three models walk into a council...",
  "Asking the other two to disagree...",
  "Letting the models argue it out...",
  "Polling the hive mind...",
  "Weighing three opinions against your taste...",
  "Checking this against how you'd answer...",
  "Summoning the chairman...",
  "Counting the votes that matter...",
];

// Mirrors launchpad_template.py's formatProviderLabel — kept tiny since
// the popup only ever shows our three canonical providers. The slug
// rename (gemini → antigravity) was 2026-05-20; canonical lineup is now
// (claude, codex, antigravity). New councils dispatch through the
// canonical slugs; this popup only shows live councils so no legacy
// normalizer is needed here (unlike launchpad_template.py which reads
// historical outcomes).
// #275 (2026-06-06, founder call): labels read as the MODEL BRAND
// (Claude / GPT / Gemini), matching the launchpad council panel + the
// live council review page, so a council launched here reads the same
// names through to its "Open council page" review.
const PROVIDER_LABELS = {
  claude: "Claude",
  codex: "GPT",
  antigravity: "Gemini",
  openai: "GPT",
  anthropic: "Claude",
  google: "Gemini",
};
function providerLabel(p) {
  return PROVIDER_LABELS[p] || (p.charAt(0).toUpperCase() + p.slice(1));
}

// The empty-submit VALIDATION message — named so the #task input listener can
// clear EXACTLY this (and not a real dispatch error) the moment the field fills.
const EMPTY_TASK_ERROR = "Type a question first.";

function setStatus(text, cls = "") {
  const el = $("status");
  if (!el) return;
  el.textContent = text;
  el.className = "status " + cls;
}

// WCAG 4.1.3 Status Messages — announce a confirmation/status the user gets
// WITHOUT focus moving (e.g. a "✓ Copied" button-label flip, which AT does NOT
// auto-announce because the button's own text mutating is mute to a screen
// reader). Pushes the text through a persistent visually-hidden role=status
// region so the SAME confirmation a sighted user reads on the button is also
// HEARD — without changing the visible button copy. find-or-create on body
// because showSetupCard() wipes the body, so a static region wouldn't survive.
// Exposed on globalThis so the pure harness-snippets.js module can reuse it.
function announce(text) {
  let region = document.getElementById("popup-sr-status");
  if (!region) {
    region = document.createElement("div");
    region.id = "popup-sr-status";
    region.className = "sr-only";
    region.setAttribute("role", "status");
    region.setAttribute("aria-live", "polite");
    region.setAttribute("aria-atomic", "true");
    document.body.appendChild(region);
  }
  // Re-announce identical text (a second copy of the same button) by clearing
  // first — an unchanged aria-live string is not re-read by AT.
  if (region.textContent === text) region.textContent = "";
  region.textContent = text;
}
if (typeof globalThis !== "undefined") globalThis.__trinityAnnounce = announce;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const child of children) {
    if (typeof child === "string") node.appendChild(document.createTextNode(child));
    else if (child) node.appendChild(child);
  }
  return node;
}

function showSetupCard(reason) {
  // Replace the action surface with first-run guidance. The popup is
  // 380px wide — just wide enough for stacked copy + a single button.
  // We collapse the install into one "Copy install commands" button
  // that puts both shell lines on the clipboard.
  stopPolling();
  const body = document.querySelector("body");
  while (body.firstChild) body.removeChild(body.firstChild);

  const extensionId = chrome.runtime.id;
  const installCmd =
    "curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh | bash";
  const registerCmd = `trinity-local install-extension --extension-id ${extensionId}`;

  // Paste-into-agent brief — the primary install path for non-technical
  // users. They open Claude Code (or Claude Desktop), paste this brief,
  // the agent runs all three steps + verifies. No terminal expertise
  // required. The extension ID is auto-filled so the user never has to
  // copy a long opaque string.
  const brief = [
    "Install Trinity Local for me.",
    "",
    "It's a cross-provider memory layer that runs councils across Claude,",
    "Codex, and Gemini and learns my taste. Open source, no API key,",
    "transcripts stay local. https://github.com/keepwhatworks/trinity",
    "",
    "Three steps:",
    `1. ${installCmd}`,
    `2. ${registerCmd}`,
    "3. trinity-local status   (should report MCP wired and extension wired)",
    "",
    "On Windows: run these inside WSL2 (Ubuntu) — the installer is UNIX-only.",
    "",
    "Then reload the Trinity Chrome extension popup. Tell me when done.",
  ].join("\n");

  body.appendChild(el("h1", { text: "Trinity Local — setup needed" }));
  body.appendChild(el("p", { class: "setup-reason", text: reason }));

  body.appendChild(el("p", { class: "setup-step", text:
    "Easiest path: paste the install brief into Claude Code or Claude " +
    "Desktop — the agent runs all the setup for you."
  }));

  const briefBtn = el("button", {
    class: "btn",
    id: "copy-setup-brief",
    text: "Copy install brief",
  });
  body.appendChild(briefBtn);
  briefBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(brief);
      briefBtn.textContent = "✓ Copied — paste into Claude Code / Desktop";
      briefBtn.disabled = true;
      announce("Copied — paste into Claude Code / Desktop");
    } catch {
      briefBtn.textContent = "Clipboard blocked — see Setup link below";
      announce("Clipboard blocked — see Setup link below");
    }
  });

  // Secondary affordance for terminal-native users — copies just the
  // two shell commands without the agent-targeted preamble.
  body.appendChild(el("p", { class: "setup-step", text:
    "Prefer the terminal? Copy just the shell commands instead:"
  }));

  const cmdsBtn = el("button", {
    class: "btn secondary",
    id: "copy-setup-cmds",
    text: "Copy shell commands",
  });
  body.appendChild(cmdsBtn);
  cmdsBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(installCmd + "\n" + registerCmd);
      cmdsBtn.textContent = "✓ Copied — paste in terminal";
      cmdsBtn.disabled = true;
      announce("Copied — paste in terminal");
    } catch {
      cmdsBtn.textContent = "Clipboard blocked — see Setup link below";
      announce("Clipboard blocked — see Setup link below");
    }
  });

  // Per-harness paste-in snippet picker (#166). For users who'd rather
  // drop the MCP config block straight into their harness than run the
  // CLI. Rendered by the pure harness-snippets.js module (no chrome.*),
  // which is the single source of truth for the per-harness config shapes.
  if (typeof renderHarnessPicker === "function") {
    renderHarnessPicker(body);
  }

  body.appendChild(el("p", { class: "setup-step", text:
    "After installing, reload this popup."
  }));

  const footer = el("p", { class: "setup-footer" });
  const link = el("a", {
    href: "https://github.com/keepwhatworks/trinity#install",
    target: "_blank",
    text: "Setup details →",
  });
  footer.appendChild(link);
  body.appendChild(footer);
}

function dispatch(kind, extra = {}) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(
      { type: "action", kind, ...extra },
      (response) => {
        if (chrome.runtime.lastError) {
          resolve({ ok: false, error: "extension-error: " + chrome.runtime.lastError.message });
          return;
        }
        if (!response) {
          resolve({ ok: false, error: "native-host-unavailable" });
          return;
        }
        resolve(response);
      }
    );
  });
}

// Standing constraint #43102d25 — JS mirror of utils.safe_error_message.
// An error string painted into the popup ribbon ("Failed: <error>") must be
// TYPE-only honest: NEVER an absolute filesystem path, an `[Errno N]`, a Python
// exception type name, or a traceback frame. The capture host sanitizes its
// `error` field, but extractError ALSO falls through to the RAW `stderr` (the
// returncode-0-but-treated-as-failure backward-compat path, where the host sets
// no `error` field) — that candidate bypasses the host sanitizer and leaked a
// full `FileNotFoundError: [Errno 2] … '/Users/<name>/.trinity/…'` traceback
// into the painted ribbon. Sanitize here so no candidate can paint an internal.
const _ABS_PATH_RX =
  /(?:[A-Za-z]:)?[/\\](?:Users|home|private|tmp|var|root|opt|usr|Library)[/\\][^\s'"]*/g;
const _ERRNO_RX = /\[Errno\s*-?\d+\]\s*/g;
const _PY_EXC_RX = /^(?:[A-Za-z_][\w.]*\.)?([A-Z][A-Za-z0-9]*(?:Error|Exception|Warning))\s*:?\s*/;
function safeErrorMessage(raw, fallback) {
  fallback = fallback || "the command failed";
  let text = String(raw == null ? "" : raw).trim();
  if (!text) return fallback;
  // Multi-line traceback → keep the most informative (last non-empty, non-frame)
  // line; a `File "…"` frame is pure path noise.
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  if (lines.length) {
    const nonFrame = lines.filter(
      (l) => !l.startsWith('File "') && !l.startsWith("Traceback")
    );
    const pick = nonFrame.length ? nonFrame : lines;
    text = pick[pick.length - 1];  // (non_frame or lines)[-1]
  }
  text = text.replace(_PY_EXC_RX, "");
  text = text.replace(_ERRNO_RX, "");
  text = text.replace(_ABS_PATH_RX, "a local file");
  text = text.trim().replace(/^:+|:+$/g, "").trim();
  text = text.replace(/['"]\s*a local file\s*['"]/g, "a local file");
  text = text.replace(/\s{2,}/g, " ").trim();
  return text || fallback;
}
if (typeof globalThis !== "undefined") globalThis.__trinitySafeError = safeErrorMessage;

// Surface a real error string instead of "unknown error". The host
// now returns `error` for non-zero exits (last line of stderr) plus
// `returncode` / `stderr`; pick whichever is most informative. EVERY
// candidate is routed through safeErrorMessage so a raw `stderr` fallback
// (the one the host doesn't pre-sanitize) can't paint a path/Errno/type
// into the "Failed: <error>" ribbon (#43102d25).
function extractError(response) {
  if (!response) return "no response";
  const candidates = [
    response.error,
    response.detail,
    response.hint,
    (response.stderr || "").trim().split("\n").pop(),
    response.returncode != null ? `exit code ${response.returncode}` : null,
  ];
  for (const c of candidates) {
    if (c && String(c).trim()) return safeErrorMessage(c, "the command failed");
  }
  return "unknown error";
}

// ─── Council polling state ────────────────────────────────────────────

let pollTimer = null;
let rotateTimer = null;
let rotateIndex = 0;
let activeStatusToken = null;
let activeMembers = [];
let activeTask = "";

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (rotateTimer) { clearInterval(rotateTimer); rotateTimer = null; }
  activeStatusToken = null;
}

function newStatusToken() {
  // Same shape as launchpad's: launch_<base36ts>_<rand>. Filename-safe
  // (matches capture_host's _SAFE_ID_RX so the in-process status reader
  // accepts it).
  const ts = Date.now().toString(36);
  const rand = Math.random().toString(36).slice(2, 8);
  return `launch_${ts}_${rand}`;
}

function showStatusPanel(task, members) {
  activeMembers = members.slice();
  activeTask = task;
  $("compose").style.display = "none";
  const panel = $("status-panel");
  panel.style.display = "block";
  $("panel-title").textContent = "Council running";
  $("panel-tip").textContent = COUNCIL_LOADING_MESSAGES[0];
  // Open/Stop visible while busy; Dismiss hidden until terminal state.
  $("panel-open-btn").style.display = "";
  $("panel-stop-btn").style.display = "";
  $("panel-dismiss-btn").style.display = "none";

  // Pre-render member rows in pending state so the user sees structure
  // before the first status JSON arrives.
  const rows = $("member-rows");
  while (rows.firstChild) rows.removeChild(rows.firstChild);
  for (const p of members) {
    rows.appendChild(memberRow(p, "pending"));
  }
  rows.appendChild(memberRow("__synthesis__", "pending"));

  // WCAG 2.4.3 Focus Order — move focus INTO the panel we just revealed.
  // The control the user activated to get here (#run-btn, or the ⌘/Ctrl+Enter
  // submit, both inside #compose) is now display:none, so the browser drops
  // focus to <body> — a keyboard user is stranded and Tab restarts from the
  // document top, never reaching the panel's Stop / Open / Close controls.
  // Mirrors the launchpad's _focusOperationActions(): focus the first
  // actionable control in the newly-shown region. The panel-action buttons
  // (Open council page / Stop council) are the useful landing; fall back to
  // the close × (always present) so focus is never left on a hidden element.
  requestAnimationFrame(() => {
    const actionable = Array.from(
      panel.querySelectorAll('.panel-actions button:not([disabled])')
    ).find((b) => getComputedStyle(b).display !== "none");
    const target = actionable || $("panel-close-btn");
    if (target && typeof target.focus === "function") target.focus();
  });
}

// Map a member status → its pill/dot LABEL. Mirrors the live council page
// (council_review.py memberRowsFor) + the launchpad running card
// (launchpad_template.py providerStatusRows) EXACTLY, including the terminal-
// council normalization labels 'didnt-run' → "Didn't run" / 'stopped' →
// "Stopped" (see normalizeTerminalMemberRows). This is the hand-maintained
// THIRD sibling of those two getters — keep its branch set in lockstep.
function memberStatusLabel(status) {
  return (
    status === "done" ? "Done" :
    status === "failed" ? "Failed" :
    status === "running" ? "Running" :
    status === "didnt-run" ? "Didn't run" :
    status === "stopped" ? "Stopped" : "Queued"
  );
}

function memberRow(provider, status, detail) {
  const isSynth = provider === "__synthesis__";
  const label = isSynth ? "Chairman synthesis" : providerLabel(provider);
  const row = el("div", { class: "member-row" });
  row.dataset.provider = provider;
  row.appendChild(el("div", { class: "dot " + status }));
  row.appendChild(el("div", { class: "name", text: label }));
  row.appendChild(el("div", { class: "pill " + status, text: memberStatusLabel(status) }));
  return row;
}

// Reconcile the pre-rendered (provisional) member rows down to the providers
// the runner ACTUALLY dispatched. The status panel pre-renders the canonical
// trio (claude/codex/antigravity) so the user sees structure before the first
// status JSON arrives — but a single-/two-provider user (only `claude` enabled
// in config) gets a council of ONE, and the status file's `members` map carries
// only the dispatched slugs. Without this, the two un-dispatched rows (GPT,
// Gemini) sat permanently "Queued" — the popup painted a 3-voice contest when
// one voice answered (the popup analog of the share-card/review/rail solo
// overclaim fixed in 00f37adc; those LIST-reading surfaces derive from the real
// member map, this one hardcoded the trio). Once a real, non-empty members map
// arrives, drop any provisional row whose slug the runner never dispatched, and
// keep the live set in `activeMembers` so the poller only updates real rows.
function reconcileMemberRows(memberMap) {
  const realProviders = Object.keys(memberMap || {}).filter((p) => p && String(p).trim());
  if (!realProviders.length) return;  // pre-write window — keep provisional rows
  activeMembers = realProviders.slice();
  const rows = $("member-rows");
  for (const child of Array.from(rows.children)) {
    const slug = child.dataset.provider;
    if (slug === "__synthesis__") continue;  // chairman row is always last
    if (!realProviders.includes(slug)) rows.removeChild(child);
  }
  // Add any dispatched provider that wasn't in the provisional trio (defensive —
  // a future lineup or a member the popup didn't pre-render), before synthesis.
  const synth = Array.from(rows.children).find((c) => c.dataset.provider === "__synthesis__");
  for (const p of realProviders) {
    if (!Array.from(rows.children).some((c) => c.dataset.provider === p)) {
      const row = memberRow(p, (memberMap[p] || {}).status || "pending");
      if (synth) rows.insertBefore(row, synth); else rows.appendChild(row);
    }
  }
}

function updateMemberRow(provider, status) {
  const rows = $("member-rows");
  const existing = Array.from(rows.children).find((c) => c.dataset.provider === provider);
  if (!existing) {
    rows.appendChild(memberRow(provider, status));
    return;
  }
  const dot = existing.querySelector(".dot");
  const pill = existing.querySelector(".pill");
  if (dot) dot.className = "dot " + status;
  if (pill) {
    pill.className = "pill " + status;
    pill.textContent = memberStatusLabel(status);
  }
}

function rotateTip() {
  rotateIndex = (rotateIndex + 1) % COUNCIL_LOADING_MESSAGES.length;
  const tip = $("panel-tip");
  if (tip) tip.textContent = COUNCIL_LOADING_MESSAGES[rotateIndex];
}

function startPolling(statusToken, members) {
  stopPolling();
  activeStatusToken = statusToken;
  rotateIndex = 0;
  rotateTimer = setInterval(rotateTip, 2500);

  // Give-up cap for a launch whose status file never materializes. launch-council
  // fires DETACHED and returns {ok:true, detached:true} BEFORE the runner has
  // written anything; if that detached process then dies / is killed / never
  // reaches its first status write, get-council-status returns {ok:true,
  // status:null} (or a transient {ok:false}) on EVERY poll — load_council_status
  // returns None with no file to read, and _coerce_stale_running_status can't fire
  // because there's no 'running' record to age out. Without a cap the popup spins
  // "Council running" with its rotating tips FOREVER — no terminal state, no
  // "Try again", no honest banner. This is the hand-maintained THIRD sibling of
  // the live_council pollers (council_review.py MAX_MISSING_POLLS, v1.7.194) and
  // the launchpad poller (launchpad_template.py startOperationPolling, v1.7.x):
  // same name on purpose so a "grep every poller" sweep finds all three. 20 polls
  // ≈ 30s at 1500ms — a real council writes its first 'running' frame within
  // seconds, so a sustained null streak means the dispatch never reached a process.
  let missingPollCount = 0;
  const MAX_MISSING_POLLS = 20;

  const giveUpUnstarted = () => {
    const tip = $("panel-tip");
    $("panel-title").textContent = "Council didn't start";
    if (tip) {
      tip.textContent =
        "Council status unavailable — the dispatch may not have started. " +
        "If a council did launch it will still appear when it finishes.";
    }
    stopPolling();
    enterTerminalState();
    hideMemberRowsIfNoProgress();
    normalizeTerminalMemberRows("failed");
  };

  const check = async () => {
    if (statusToken !== activeStatusToken) return;
    const r = await dispatch("get-council-status", { status_token: statusToken });
    if (!r || !r.ok) {
      // Treat transient read failure as just "no status yet" — the
      // runner may not have written the first record. Don't bail
      // immediately, but DO count it toward the give-up cap so a host
      // that never recovers can't pin the popup on "running" forever.
      missingPollCount++;
      if (missingPollCount >= MAX_MISSING_POLLS) giveUpUnstarted();
      return;
    }
    const status = r.status;
    if (!status) {
      // pre-write window — keep cycling tips, but count toward give-up so a
      // dead/never-started runner (status file never written) eventually
      // resolves to an honest terminal state instead of an infinite spinner.
      missingPollCount++;
      if (missingPollCount >= MAX_MISSING_POLLS) giveUpUnstarted();
      return;
    }
    missingPollCount = 0;

    // Member updates. Collapse the provisional trio down to the providers the
    // runner actually dispatched (single-provider users get a council of one),
    // then update only those real rows — never the hardcoded launch-time trio,
    // which would resurrect the phantom "Queued" rows reconcile just dropped.
    const memberMap = status.members || {};
    reconcileMemberRows(memberMap);
    for (const p of activeMembers) {
      const m = memberMap[p] || {};
      updateMemberRow(p, m.status || "pending");
    }
    // Synthesis row
    const synth = status.synthesis || {};
    updateMemberRow("__synthesis__", synth.status || "pending");

    // Synthesis tip overrides rotating copy while chairman runs.
    const tip = $("panel-tip");
    if (tip) {
      if (synth.status === "running") {
        tip.textContent = "Synthesizing the strongest answer...";
      } else {
        const activeProvider = Object.entries(memberMap)
          .find(([, v]) => (v || {}).status === "running");
        if (activeProvider) {
          tip.textContent = `${providerLabel(activeProvider[0])}: ${COUNCIL_LOADING_MESSAGES[rotateIndex]}`;
        }
      }
    }

    if (status.status === "completed") {
      $("panel-title").textContent = "Council ready";
      // The user-pick / click-to-rate UI was retired 2026-05-22 — the chairman
      // picks the winner (the supervision signal), and the council page presents
      // it already marked ("the answer you'd have picked"). Don't tell the user
      // to "pick a winner": there's nothing to pick, just the verdict to read.
      if (tip) tip.textContent = "Opening the council page — the chairman's verdict is ready.";
      stopPolling();
      enterTerminalState();
      // Auto-open the council page for THIS council — not the launchpad.
      // The user just asked a specific question; landing them on the
      // launchpad would be a step backwards.
      const openRes = await dispatch("open-council-page", {
        status_token: statusToken,
        task: activeTask,
        members: activeMembers,
      });
      // NO-FEEDBACK guard (asymmetric sibling of the manual #panel-open-btn
      // handler, which already surfaces a failed open). The auto-open is a
      // deferred action that CAN fail — open-council-page returns {ok:false}
      // on needs_regen (first install, no live_council.html yet), an invalid
      // token, or webbrowser.open() returning False (no browser launched). If
      // we leave the optimistic "Opening the council page…" tip standing on a
      // failed open, the panel LIES: it claims the page is opening when it
      // never did, with no error and no nudge to the still-visible "Open
      // council page" button — a silent dead-end. On failure, correct the tip
      // and point the user at the manual retry that's right below it.
      if (tip && !(openRes && openRes.ok)) {
        tip.textContent =
          "Couldn't open the council page automatically: " + extractError(openRes) +
          ". The verdict is ready — tap “Open council page” to read it.";
      }
    } else if (status.status === "failed") {
      $("panel-title").textContent = "Council failed";
      if (tip) tip.textContent = status.error || "The council runner exited with an error.";
      stopPolling();
      enterTerminalState();
      // Hide the all-"Queued" grid so "Council failed" doesn't sit above a roster
      // of providers all still reading "Queued" (the launchpad's terminal
      // showProviderRows gate — see hideMemberRowsIfNoProgress).
      hideMemberRowsIfNoProgress();
      // And relabel any member frozen mid-flight (Done partner, but this one
      // still "Running"/"Queued") to the honest "Didn't run" — otherwise the
      // visible grid contradicts its own "Council failed" header.
      normalizeTerminalMemberRows("failed");
    } else if (status.status === "canceled") {
      $("panel-title").textContent = "Council stopped";
      if (tip) tip.textContent = status.error || "Run was canceled.";
      stopPolling();
      enterTerminalState();
      hideMemberRowsIfNoProgress();
      normalizeTerminalMemberRows("canceled");
    }
  };

  // Fire immediately so the first status JSON shows up as soon as the
  // runner writes it (typically <500ms after Popen).
  check();
  pollTimer = setInterval(check, 1500);
}

// Once a council reaches completed / failed / canceled, swap Open + Stop
// for a single Dismiss button — mirrors the launchpad's v-if logic.
function enterTerminalState() {
  $("panel-stop-btn").style.display = "none";
  $("panel-dismiss-btn").style.display = "";
}

// On a TERMINAL failed/canceled where NO member ever made real progress (the
// runner / native host died before writing any member status, so the rows are
// still the all-"Queued" pre-rendered set), HIDE the member-rows grid. Otherwise
// the popup paints "Council failed" ABOVE a list of providers all reading "Queued"
// + a chairman row "Queued" — a self-contradiction (the failed header says it
// didn't run; the grid says everyone's still patiently waiting). This is the
// popup analog of the launchpad's showProviderRows terminal gate
// (launchpad-init.js:1606, fixed 2026-06-02 driving the cold-start launch on an
// empty home): the running-card hides the grid when status is failed/canceled and
// every row is still 'pending'. The popup inherited the pre-render + reconcile but
// NOT this terminal hide — so the toolbar popup showed the contradiction the
// launchpad already suppressed. KEEP the grid when ANY row made real progress
// (a member Done/Failed/Running, or the chairman row past pending) — a
// partially-run council that then failed is informative, not contradictory.
function hideMemberRowsIfNoProgress() {
  const rows = $("member-rows");
  if (!rows) return;
  const madeProgress = Array.from(rows.children).some((child) => {
    const pill = child.querySelector(".pill");
    // A row is "progressed" once its pill leaves the pre-render 'pending'/'Queued'
    // state (running/done/failed). Read the dot's class too in case a pill is
    // mid-mutation. The chairman row counts: a completed synthesis under a failed
    // top-level status is still real progress worth showing.
    if (pill && pill.classList && !pill.classList.contains("pending")) return true;
    const dot = child.querySelector(".dot");
    if (dot && dot.classList && !dot.classList.contains("pending")) return true;
    return false;
  });
  rows.style.display = madeProgress ? "" : "none";
}

// On a TERMINAL failed/canceled council, the runner's finalize step flips ONLY
// the top-level status — it NEVER rewrites the per-member statuses
// (finalize_council_run_state, council_status.py). So a council that died
// PARTWAY (one member Done, the others still 'running'/'pending') leaves those
// never-finished members reading "Running" / "Queued" in the grid — directly
// UNDER a "Council failed" / "Council stopped" header. That's the same self-
// contradiction the live council page (council_review.py memberRowsFor) and the
// launchpad running card (launchpad_template.py providerStatusRows) already
// normalize: terminal && (pending|running) → 'didnt-run' (failed) / 'stopped'
// (canceled), rendered with the MUTED 'pending' style (a never-ran member is
// NOT an error — no red) and the honest "Didn't run" / "Stopped" label. The
// popup is the hand-maintained THIRD sibling and had drifted (no normalization).
// Mirror the two siblings exactly. Run this AFTER hideMemberRowsIfNoProgress so
// its progress detection still sees the original Done/running rows.
function normalizeTerminalMemberRows(terminal) {
  const rows = $("member-rows");
  if (!rows) return;
  const label = terminal === "failed" ? "Didn't run" : "Stopped";
  Array.from(rows.children).forEach((child) => {
    // Never touch the synthesis row — the live-page/launchpad siblings leave the
    // chairman row to its own (un-normalized) status, so do the same for parity.
    if (child.dataset && child.dataset.provider === "__synthesis__") return;
    const pill = child.querySelector(".pill");
    const dot = child.querySelector(".dot");
    if (!pill) return;
    // A member is "never finished" iff its pill is still pre-terminal. The status
    // JSON spells the queued state either 'pending' (council_status.py seeds the
    // member map with "pending") or 'queued' (a member never reconciled past the
    // pre-render), both rendering as "Queued"; 'running' is the in-flight badge.
    // Done/Failed are terminal — keep them (a stopped council can still carry one
    // real answer; an all-fail council shows Failed per provider).
    const stale =
      pill.classList.contains("pending") ||
      pill.classList.contains("queued") ||
      pill.classList.contains("running");
    if (!stale) return;
    pill.className = "pill pending";  // muted style, not an error
    pill.textContent = label;
    if (dot) dot.className = "dot pending";
  });
}

// ─── Wire UI ──────────────────────────────────────────────────────────

$("run-btn").addEventListener("click", async () => {
  const task = $("task").value.trim();
  if (!task) {
    setStatus(EMPTY_TASK_ERROR, "error");
    return;
  }
  const statusToken = newStatusToken();
  const members = ["claude", "codex", "antigravity"];
  showStatusPanel(task, members);

  const response = await dispatch("launch-council", {
    task,
    status_token: statusToken,
  });

  if (response.ok && response.detached) {
    // Council is running headless; start polling.
    startPolling(statusToken, members);
    return;
  }
  // Backward-compat: if the host runs synchronously (older capture_host),
  // we still get here with the full result. Re-show compose + a real error.
  $("status-panel").style.display = "none";
  $("compose").style.display = "block";
  if (response.error === "native-host-unavailable") {
    showSetupCard("Native Messaging host not found. Trinity's CLI isn't wired to this extension yet.");
  } else if ((response.error || "").includes("CLI not on PATH")) {
    showSetupCard("Trinity's CLI isn't on PATH. Install it via curl-bash, then come back here.");
  } else {
    setStatus("Failed: " + extractError(response), "error");
  }
});

$("task").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    $("run-btn").click();
  }
});

// Clear the "Type a question first." VALIDATION error the moment the field
// becomes non-empty — otherwise the red status stayed pinned under a textarea
// the user had since filled, contradicting itself ("type a question" shown
// while a question is typed). The launchpad composer's onPromptInput already
// does exactly this (EMPTY_TASK_ERROR clear); the popup was the hand-maintained
// sibling that never got it. Scoped to the validation message AND the non-empty
// state so a real dispatch error isn't swallowed by a keystroke.
$("task").addEventListener("input", () => {
  const el = $("status");
  if (!el) return;
  if (
    el.classList.contains("error") &&
    el.textContent === EMPTY_TASK_ERROR &&
    $("task").value.trim()
  ) {
    setStatus("");
  }
});

$("panel-open-btn").addEventListener("click", async () => {
  // Open THIS council's live page (not the launchpad). Same shape as
  // the launchpad's `<a :href="liveCouncilUrl">Open council page</a>`.
  const btn = $("panel-open-btn");
  // DOUBLE-FIRE guard: open-council-page does a `webbrowser.open` on the host
  // side, so a real double-click (the button stays visible+enabled through the
  // ~120ms native-host round-trip) opens TWO browser tabs for ONE council.
  // Disable + relabel immediately — the same idempotency the Stop button and
  // the launchpad's `if (this.busy) return` already have; this twin was missed.
  if (btn.disabled) return;
  btn.disabled = true;
  btn.textContent = "Opening…";
  const r = await dispatch("open-council-page", {
    status_token: activeStatusToken,
    task: activeTask,
    members: activeMembers,
  });
  if (r && r.ok) {
    window.close();
    return;
  }
  // NO-FEEDBACK guard: a failed open (page not written yet / host down) must
  // say SO in the panel — otherwise the click reads as a dead no-op. The
  // council keeps running; the user can retry, so RE-ENABLE + restore the label
  // (not stuck-disabled forever — mirrors the Stop button's failure recovery).
  btn.disabled = false;
  btn.textContent = "Open council page";
  const tip = $("panel-tip");
  if (tip) {
    tip.textContent =
      "Couldn't open the council page yet: " + extractError(r) +
      ". The council is still running — try again in a moment.";
  }
});

$("panel-stop-btn").addEventListener("click", async () => {
  // Mirrors launchpad's stopCurrentCouncil — fires the real stop-council
  // action (NOT just hiding the popup). The runner sees SIGTERM and
  // writes status=canceled before exiting.
  if (!activeStatusToken) return;
  const btn = $("panel-stop-btn");
  // Immediate ack: relabel to "Stopping…" (disabled, no double-fire) so the
  // click isn't a silent no-op while the host writes status=canceled. Mirrors
  // the launchpad's stopRequested flag.
  btn.disabled = true;
  btn.textContent = "Stopping…";
  const tip = $("panel-tip");
  if (tip) tip.textContent = "Stopping the council…";
  const r = await dispatch("stop-council", { status_token: activeStatusToken });
  if (r && r.ok) {
    // Success — let the next poll tick render the canceled terminal state.
    return;
  }
  // NO-FEEDBACK guard: a FAILED stop must re-enable the button + say why,
  // otherwise it's stuck-disabled forever while the council keeps running.
  btn.disabled = false;
  btn.textContent = "Stop council";
  if (tip) {
    tip.textContent =
      "Couldn't stop the council: " + extractError(r) + ". Try again.";
  }
});

$("panel-dismiss-btn").addEventListener("click", () => {
  // Terminal-state dismiss — return to compose for the next question.
  stopPolling();
  $("status-panel").style.display = "none";
  $("compose").style.display = "block";
  setStatus("");
});

$("panel-close-btn").addEventListener("click", () => {
  // ✕ — closes the popup without stopping the council (it keeps
  // running headless since we Popen'd with start_new_session). User
  // can reopen the popup or use the launchpad to find it again.
  stopPolling();
  window.close();
});

$("open-launchpad-btn").addEventListener("click", async () => {
  // Open the WORKING launchpad — the host's file:// page (~/.trinity/portal_pages/
  // launchpad.html), which renders because file:// allows the eval petite-vue
  // needs. The in-extension chrome-extension://launchpad.html does NOT render: MV3
  // forbids 'unsafe-eval' in extension pages, so petite-vue can't evaluate its
  // templates and the page shows raw {{ }} (founder report 2026-06-12, verified).
  // Routing through the host's open-launchpad action is the working path until
  // the launchpad UI is made CSP-safe (the side-panel migration is blocked on it).
  const btn = $("open-launchpad-btn");
  // DOUBLE-FIRE guard (same class as the panel "Open council page" fix): the host's
  // _open_launchpad does an `open <launchpad.html>` (open_path → webbrowser.open
  // analog) on EVERY call, so a real double-click — the button stays enabled through
  // the ~120ms native-host round-trip — opens TWO launchpad tabs for one click. This
  // is the missed sibling of `open-council-page`: same disable-before-dispatch fix.
  if (btn.disabled) return;
  btn.disabled = true;
  setStatus("Opening launchpad…");
  try {
    const r = await dispatch("open-launchpad", {});
    if (r && r.ok) {
      setTimeout(() => window.close(), 200);
    } else {
      // NO-FEEDBACK guard: a failed open must speak AND re-enable so the user can
      // retry — not leave the button stuck-disabled (mirrors panel Open/Stop recovery).
      btn.disabled = false;
      setStatus("Couldn't open the launchpad: " + ((r && (r.error || r.detail)) || "host unavailable"), "error");
    }
  } catch (e) {
    btn.disabled = false;
    setStatus("Couldn't open the launchpad: " + ((e && e.message) || e), "error");
  }
});
