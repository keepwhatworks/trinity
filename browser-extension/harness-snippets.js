// Trinity Local — per-harness paste-in snippet generator (#166, Phase A).
//
// Replaces "go run install-mcp in the right CLI" with: pick your harness,
// copy the exact MCP config block, paste it where it says. Solves the
// acquisition friction for non-coder + multi-harness users — they don't
// need to know which CLI they're in, just which app they use.
//
// PURE module: depends only on `document` + `navigator.clipboard`, never on
// the chrome extension runtime — so it loads standalone (independently
// browser-testable) and is
// the SINGLE SOURCE OF TRUTH for the config shapes (mirrors the Python
// `install-mcp` writer in src/trinity_local/commands/install.py).
//
// Curl-primary (founder decision, 2026-05-29): the recommended install is the
// one-line bootstrap (`curl … install.sh | bash`), which clones the repo,
// installs runtime deps, and runs `install-mcp` for every harness it detects.
// These paste-in blocks are the MANUAL fallback for a harness the bootstrap
// didn't auto-wire — they mirror exactly what `install-mcp` writes:
// `command = <your python>`, `args = ["-m", "trinity_local.main", "--mcp"]`.
// `PYTHON` below is a placeholder — substitute the absolute path printed by
// the bootstrap (e.g. `/opt/homebrew/bin/python3.12`) or `which python3`.
//
// Exposed as `globalThis.TRINITY_HARNESS_SNIPPETS` (data) +
// `globalThis.renderHarnessPicker(targetEl)` (UI). popup.js calls the
// renderer inside the setup card.

(function () {
  "use strict";

  // The one-line install everyone should prefer — clones the repo (auditable),
  // installs deps, and registers the MCP server in every detected harness.
  var BOOTSTRAP_CMD =
    "curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh | bash";

  // Manual fallback — what `install-mcp` writes (module-mode, no PyPI runner).
  // PYTHON is a placeholder for the absolute interpreter path the bootstrap
  // bakes into the wrapper; swap in `which python3` if you wire it by hand.
  var JSON_BLOCK =
    '{\n' +
    '  "mcpServers": {\n' +
    '    "trinity-local": {\n' +
    '      "command": "PYTHON",\n' +
    '      "args": ["-m", "trinity_local.main", "--mcp"]\n' +
    '    }\n' +
    '  }\n' +
    '}';

  // Codex reads TOML, not JSON. Same command/args, different surface.
  var TOML_BLOCK =
    '[mcp_servers.trinity-local]\n' +
    'command = "PYTHON"\n' +
    'args = ["-m", "trinity_local.main", "--mcp"]';

  // Each harness: where the block goes + the block itself. `merge` tells the
  // user this is an ADD into an existing object, not a whole-file replace
  // (the #1 paste-in footgun — clobbering a config that already has servers).
  var SNIPPETS = [
    {
      id: "claude-code",
      label: "Claude Code",
      file: "~/.claude.json",
      lang: "json",
      merge: 'merge into the top-level "mcpServers" object',
      snippet: JSON_BLOCK,
    },
    {
      id: "claude-desktop",
      label: "Claude Desktop",
      file: "~/Library/Application Support/Claude/claude_desktop_config.json (macOS) · %APPDATA%\\Claude\\claude_desktop_config.json (Windows)",
      lang: "json",
      merge: 'merge into the top-level "mcpServers" object',
      snippet: JSON_BLOCK,
    },
    {
      id: "codex",
      label: "Codex CLI",
      file: "~/.codex/config.toml",
      lang: "toml",
      merge: "append this table",
      snippet: TOML_BLOCK,
    },
    {
      id: "cursor",
      label: "Cursor",
      file: "~/.cursor/mcp.json",
      lang: "json",
      merge: 'merge into the top-level "mcpServers" object',
      snippet: JSON_BLOCK,
    },
    {
      id: "antigravity",
      label: "Antigravity",
      file: "~/.gemini/settings.json",
      lang: "json",
      merge: 'merge into the "mcpServers" key',
      snippet: JSON_BLOCK,
    },
    {
      id: "cline",
      label: "Cline (VS Code)",
      file: "Cline → MCP Servers → Configure (cline_mcp_settings.json)",
      lang: "json",
      merge: 'merge into the top-level "mcpServers" object',
      snippet: JSON_BLOCK,
    },
  ];

  function snippetFor(id) {
    for (var i = 0; i < SNIPPETS.length; i++) {
      if (SNIPPETS[i].id === id) return SNIPPETS[i];
    }
    return null;
  }

  // Build the picker DOM: a row of harness pills, a detail area that shows
  // the target file + the copyable config block. Pure DOM, no extension APIs.
  function renderHarnessPicker(targetEl) {
    var doc = targetEl.ownerDocument || document;

    // WCAG 4.1.3 — announce the copy-ack ("✓ Copied") confirmation. A button's
    // own textContent flipping is MUTE to a screen reader, so the same text the
    // sighted user reads on the pill is pushed through a persistent role=status
    // region. Reuse the popup's shared announcer when present; otherwise lazily
    // create a local sr-only region in this document so the module stays pure.
    function announce(text) {
      var host = (typeof globalThis !== "undefined") ? globalThis : window;
      if (host && typeof host.__trinityAnnounce === "function") {
        host.__trinityAnnounce(text);
        return;
      }
      var region = doc.getElementById("popup-sr-status");
      if (!region) {
        region = doc.createElement("div");
        region.id = "popup-sr-status";
        region.className = "sr-only";
        region.setAttribute("role", "status");
        region.setAttribute("aria-live", "polite");
        region.setAttribute("aria-atomic", "true");
        (doc.body || targetEl).appendChild(region);
      }
      if (region.textContent === text) region.textContent = "";
      region.textContent = text;
    }

    var wrap = doc.createElement("div");
    wrap.className = "harness-picker";

    var heading = doc.createElement("p");
    heading.className = "setup-step";
    heading.textContent =
      "Recommended: run the one-line bootstrap (" + BOOTSTRAP_CMD +
      ") — it wires every harness for you. Or paste the config straight into " +
      "your harness:";
    wrap.appendChild(heading);

    var pillRow = doc.createElement("div");
    pillRow.className = "harness-pills";
    wrap.appendChild(pillRow);

    var detail = doc.createElement("div");
    detail.className = "harness-detail";
    detail.style.display = "none";
    wrap.appendChild(detail);

    var fileLine = doc.createElement("p");
    fileLine.className = "harness-file";
    detail.appendChild(fileLine);

    var pre = doc.createElement("pre");
    pre.className = "harness-snippet";
    var code = doc.createElement("code");
    pre.appendChild(code);
    detail.appendChild(pre);

    // PYTHON in the block is a placeholder. The module header documents that,
    // but a source comment never reaches the non-coder pasting the block — and
    // pasting `command = "PYTHON"` verbatim spawns a non-existent binary, so the
    // MCP server silently never starts (the worst onboarding failure: looks
    // wired, does nothing). Surface the substitution IN the picker, shown only
    // for blocks that actually carry the placeholder.
    var hintLine = doc.createElement("p");
    hintLine.className = "harness-file harness-substitute-hint";
    hintLine.style.display = "none";
    detail.appendChild(hintLine);

    var copyBtn = doc.createElement("button");
    copyBtn.className = "btn";
    copyBtn.id = "copy-harness-snippet";
    copyBtn.textContent = "Copy config block";
    detail.appendChild(copyBtn);

    var selected = null;
    var pills = [];

    function select(spec, pill) {
      selected = spec;
      for (var i = 0; i < pills.length; i++) {
        pills[i].classList.toggle("active", pills[i] === pill);
      }
      fileLine.textContent = "Paste into " + spec.file + " — " + spec.merge + ".";
      code.textContent = spec.snippet;
      if (spec.snippet.indexOf("PYTHON") !== -1) {
        hintLine.textContent =
          "Replace PYTHON with your Python path — run `which python3`, or paste " +
          "the absolute path the bootstrap printed (e.g. /opt/homebrew/bin/python3.12). " +
          "Pasting it as-is spawns a non-existent binary and the server won't start.";
        hintLine.style.display = "block";
      } else {
        hintLine.style.display = "none";
      }
      copyBtn.textContent = "Copy config block";
      copyBtn.disabled = false;
      detail.style.display = "block";
    }

    SNIPPETS.forEach(function (spec) {
      var pill = doc.createElement("button");
      pill.className = "harness-pill";
      pill.type = "button";
      pill.dataset.harness = spec.id;
      pill.textContent = spec.label;
      pill.addEventListener("click", function () { select(spec, pill); });
      pillRow.appendChild(pill);
      pills.push(pill);
    });

    copyBtn.addEventListener("click", function () {
      if (!selected) return;
      var nav = (typeof navigator !== "undefined") ? navigator : null;
      if (nav && nav.clipboard && nav.clipboard.writeText) {
        nav.clipboard.writeText(selected.snippet).then(function () {
          copyBtn.textContent = "✓ Copied — paste into " + selected.label;
          copyBtn.disabled = true;
          announce("Copied — paste into " + selected.label);
        }, function () {
          copyBtn.textContent = "Clipboard blocked — select + copy manually";
          announce("Clipboard blocked — select + copy manually");
        });
      } else {
        copyBtn.textContent = "Clipboard unavailable — select + copy manually";
        announce("Clipboard unavailable — select + copy manually");
      }
    });

    targetEl.appendChild(wrap);
    return wrap;
  }

  var root = (typeof globalThis !== "undefined") ? globalThis : window;
  root.TRINITY_HARNESS_SNIPPETS = SNIPPETS;
  root.TRINITY_BOOTSTRAP_CMD = BOOTSTRAP_CMD;
  root.trinitySnippetFor = snippetFor;
  root.renderHarnessPicker = renderHarnessPicker;
})();
