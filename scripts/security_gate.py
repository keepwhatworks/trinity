#!/usr/bin/env python3
"""security_gate -- does this diff need the pre-push security pass? (council_0d0d398d463f5f7b, amd_0470)

Two background security reviews AFTER push found real holes in build mode (025bba74, ad4b4b6a); a pass
before the push would have held them. The trigger is mechanical, on ADDED lines of non-test code:
process execution, patch application, archive extraction, deserialization, the network, sandbox and
permission flags, symlinks and path resolution, and the Native Messaging host. The pass is separate from
verify's STOP (which stays "a relevant test is red"):

    trinity-local verify --diff D --criteria scripts/security_criteria.json --exclude-lab <your lab> --cwd .

Any concern blocks the push until the change fixes it or the commit message answers it with a reason.

    git diff origin/main...HEAD | python scripts/security_gate.py      # exit 1: the pass is required
"""
from __future__ import annotations

import re
import sys

TRIGGERS = {
    "process execution": r"\bsubprocess\b|\bos\.(?:system|popen|exec\w*|spawn\w*|startfile)\(|\bPopen\(|shell=True"
                         r"|\beval\(|\bexec\(|create_subprocess_|\bpty\.spawn",
    "patch application": r"\bgit\b[\"',\s]+apply\b|\[\s*[\"']patch[\"']",
    "archive extraction": r"\.extract(?:all)?\(|\btarfile\b|\bzipfile\b|unpack_archive",
    "deserialization": r"\bpickle\b|\bmarshal\b|yaml\.(?:unsafe_)?load\(",
    "network": r"\bsocket\b|\burllib\b|\brequests\b|http\.client|\bhttpx\b|urlopen|\baiohttp\b",
    "sandbox or permissions": r"sandbox-exec|--sandbox|permission-mode|dangerously|allowedTools|disallowedTools",
    "symlinks and paths": r"\.symlink_to\(|os\.symlink\(|os\.readlink\(|\.resolve\(\)|realpath\(|is_relative_to\(",
    "native messaging host": r"capture_host",
}
_CODE = re.compile(r"\.(?:py|js|ts|sh|mjs)$")
# Config and manifest files: a permission or sandbox setting there is as live as code.
_CONFIG = re.compile(r"\.(?:json|toml|ya?ml|cfg|ini)$")
_CONFIG_TRIGGER = re.compile(r"dangerously|skip-permissions|bypassPermissions|workspace-write|danger-full-access"
                             r"|--yolo|--full-auto|sandbox|permission|allowedTools|nativeMessaging", re.I)
_TEST = re.compile(r"(?:^|/)tests?/|(?:^|/)test_[^/]*$")
_SELF = "scripts/security_gate.py"           # its TRIGGERS are the patterns, not uses of them


def triggers(diff: str) -> dict[str, list[str]]:
    """{trigger: [file, ...]} over the added lines of non-test code files."""
    hits: dict[str, set[str]] = {}
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip()
            path = p[2:] if p.startswith("b/") else p
            continue
        if not line.startswith("+") or path is None or path == _SELF or _TEST.search(path):
            continue
        if _CONFIG.search(path):
            if _CONFIG_TRIGGER.search(line[1:]):
                hits.setdefault("permission or sandbox config", set()).add(path)
            continue
        if not _CODE.search(path):
            continue
        for name, pattern in TRIGGERS.items():
            if re.search(pattern, line[1:]):
                hits.setdefault(name, set()).add(path)
    return {k: sorted(v) for k, v in hits.items()}


def main() -> int:
    hits = triggers(sys.stdin.read())
    if not hits:
        print("security pass: not required (no trigger on an added line of non-test code)")
        return 0
    print("security pass REQUIRED before push:")
    for name, files in hits.items():
        print(f"  {name}: {', '.join(files)}")
    print("run: trinity-local verify --diff <diff> --criteria scripts/security_criteria.json "
          "--exclude-lab <your lab> --cwd .   (any concern blocks the push)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
