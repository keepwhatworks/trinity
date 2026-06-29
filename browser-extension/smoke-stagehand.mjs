#!/usr/bin/env node
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

let Stagehand;
try {
  ({ Stagehand } = await import("@browserbasehq/stagehand"));
} catch (error) {
  console.error("[setup] @browserbasehq/stagehand is not installed.");
  console.error("[setup] Run: cd browser-extension && npm install");
  console.error(`[setup] import failed: ${error.message}`);
  process.exit(3);
}

const THIS_DIR = path.dirname(fileURLToPath(import.meta.url));
const extensionDir = path.resolve(process.env.TRINITY_EXTENSION_DIR || THIS_DIR);
const extensionId = process.env.TRINITY_EXTENSION_ID || "";
const chromeExecutablePath = process.env.TRINITY_CHROME_EXECUTABLE_PATH || "";
const headless = ["1", "true", "yes"].includes(
  String(process.env.TRINITY_STAGEHAND_HEADLESS || "").toLowerCase(),
);
const launchpadUrl =
  process.env.TRINITY_LAUNCHPAD_URL ||
  pathToFileURL(
    process.env.TRINITY_LAUNCHPAD_PATH ||
      path.join(os.homedir(), ".trinity", "portal_pages", "launchpad.html"),
  ).href;

if (!/^[a-p]{32}$/.test(extensionId)) {
  console.error("[setup] TRINITY_EXTENSION_ID must be Chrome's 32-character a-p id.");
  process.exit(2);
}

if (!chromeExecutablePath) {
  console.error("[setup] TRINITY_CHROME_EXECUTABLE_PATH is required.");
  process.exit(2);
}

let stagehand;
let userDataDir;

try {
  userDataDir = await mkdtemp(path.join(os.tmpdir(), "trinity-stagehand-profile-"));
  stagehand = new Stagehand({
    env: "LOCAL",
    verbose: 0,
    localBrowserLaunchOptions: {
      executablePath: chromeExecutablePath,
      userDataDir,
      headless,
      viewport: { width: 1280, height: 900 },
      args: [
        `--disable-extensions-except=${extensionDir}`,
        `--load-extension=${extensionDir}`,
        "--no-default-browser-check",
        "--no-first-run",
      ],
    },
  });

  await stagehand.init();
  const context = stagehand.context;
  const pages = typeof context.pages === "function" ? context.pages() : [];
  const page = pages[0] || (await context.newPage());

  await page.goto(launchpadUrl, { waitUntil: "domcontentloaded", timeout: 15000 });
  if (typeof page.waitForLoadState === "function") {
    await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
  }

  const response = await page.evaluate(async (id) => {
    if (!globalThis.chrome?.runtime?.sendMessage) {
      return { ok: false, error: "chrome-runtime-sendMessage-missing" };
    }
    return await new Promise((resolve) => {
      const timer = setTimeout(() => {
        resolve({ ok: false, error: "timeout-waiting-for-trinity-pong" });
      }, 5000);
      globalThis.chrome.runtime.sendMessage(id, { type: "trinity-ping" }, (reply) => {
        clearTimeout(timer);
        const lastError = globalThis.chrome.runtime.lastError?.message;
        resolve(reply || { ok: false, error: lastError || "empty-response" });
      });
    });
  }, extensionId);

  if (!response?.ok || response.type !== "trinity-pong") {
    console.error("[fail] trinity-ping did not return trinity-pong.");
    console.error(JSON.stringify({ launchpadUrl, extensionId, response }, null, 2));
    process.exitCode = 1;
  } else {
    console.log(
      `[ok] launchpad reached Trinity extension ${extensionId} (${response.extensionVersion})`,
    );
  }
} catch (error) {
  console.error("[fail] Stagehand Chrome smoke failed.");
  console.error(error?.stack || String(error));
  process.exitCode = 1;
} finally {
  try {
    await stagehand?.close();
  } finally {
    if (userDataDir) {
      await rm(userDataDir, { recursive: true, force: true });
    }
  }
}
