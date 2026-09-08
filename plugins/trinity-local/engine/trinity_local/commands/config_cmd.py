"""Handler for the `config` command — read and change provider settings.

WHY THIS EXISTS
---------------
Trinity's engine has always been provider-portable: `ProviderConfig` is just
command + args + model, so pointing every council at a different subscription
is a JSON edit. The PRODUCT was not portable, because the JSON lived at
`project_root()/config.json`, which for a wheel install resolves into the
Python lib directory. A pip user had no writable config and therefore no way
to switch providers at all. `config_path()` now prefers the state dir; this
verb is the surface that makes it usable without a text editor.

THE TRAP THIS VERB CLOSES
-------------------------
`_reconcile_model_arg` makes a `--model X` baked into args authoritative over
the `model` field, because that inline flag is what the CLI actually
dispatches. So a user who edits `model` by hand and leaves the old inline
flag in place dispatches one model and records another — the exact poisoning
`_reconcile_model_arg` was written to detect. Setting a model through this
verb strips the conflicting inline flag in the same write.
"""
from __future__ import annotations

import json
import shutil

from ..config import config_path, load_config, project_root, trinity_home

# Top-level scalar keys a user may set. Anything else is refused rather than
# written: a typo'd key in a JSON file is silently ignored by load_config, so
# an unvalidated `--set` would report success for a setting that never applies.
_SCALAR_KEYS = {
    "default_primary_provider": str,
    "max_turns": int,
}
# Per-provider keys. `command` and `args` are lists and stay hand-edited —
# a flag-splitting CLI would guess wrong on quoting.
_PROVIDER_KEYS = {
    "model": str,
    "effort": str,
    "enabled": bool,
    "label": str,
}


def register(subparsers):
    parser = subparsers.add_parser(
        "config", help="Show or change provider settings (which subscription Trinity dispatches to)"
    )
    parser.add_argument("--init", action="store_true",
                        help="Create a writable config.json in the state dir from the bundled defaults")
    parser.add_argument("--path", action="store_true", help="Print the resolved config path and exit")
    parser.add_argument("--set", dest="assignments", action="append", metavar="KEY=VALUE", default=[],
                        help="Set a key, e.g. providers.codex.model=gpt-6-astra or default_primary_provider=codex")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Output as JSON")
    parser.set_defaults(handler=handle_config)


def _bundled_default() -> str | None:
    try:
        from importlib import resources
        return resources.files("trinity_local").joinpath(
            "data/config.example.json").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):
        return None


def _coerce(value: str, kind):
    if kind is bool:
        low = value.strip().lower()
        if low in {"true", "1", "yes", "on"}:
            return True
        if low in {"false", "0", "no", "off"}:
            return False
        raise ValueError(f"expected true/false, got {value!r}")
    if kind is int:
        return int(value)
    return value


def _strip_inline_model(args: list) -> list:
    """Drop `--model X` / `--model=X` from an args list.

    Mirrors `_reconcile_model_arg`'s parsing so the stripped shape is exactly
    what that function would have lifted. Without this, writing `model` leaves
    the old inline flag winning at dispatch.
    """
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a == "--model" and i + 1 < len(args):
            i += 2
            continue
        if isinstance(a, str) and a.startswith("--model="):
            i += 1
            continue
        out.append(a)
        i += 1
    return out


def _apply(raw: dict, assignment: str) -> str:
    if "=" not in assignment:
        raise ValueError(f"expected KEY=VALUE, got {assignment!r}")
    key, value = assignment.split("=", 1)
    key = key.strip()
    parts = key.split(".")

    if len(parts) == 1:
        if key not in _SCALAR_KEYS:
            raise ValueError(
                f"unknown setting {key!r} (known: {', '.join(sorted(_SCALAR_KEYS))}, "
                "or providers.<name>.<field>)")
        raw[key] = _coerce(value, _SCALAR_KEYS[key])
        return f"{key} = {raw[key]!r}"

    if len(parts) == 3 and parts[0] == "providers":
        _, name, field = parts
        providers = raw.get("providers") or {}
        if name not in providers:
            raise ValueError(
                f"no provider {name!r} in config (have: {', '.join(sorted(providers)) or 'none'})")
        if field not in _PROVIDER_KEYS:
            raise ValueError(
                f"cannot set {field!r} here (settable: {', '.join(sorted(_PROVIDER_KEYS))}; "
                "command and args stay hand-edited)")
        providers[name][field] = _coerce(value, _PROVIDER_KEYS[field])
        note = ""
        if field == "model":
            before = list(providers[name].get("args") or [])
            after = _strip_inline_model(before)
            if after != before:
                providers[name]["args"] = after
                note = "  (stripped the conflicting inline --model so dispatch matches what is recorded)"
        return f"providers.{name}.{field} = {providers[name][field]!r}{note}"

    raise ValueError(f"unrecognized key {key!r}")


def handle_config(args) -> int:
    explicit = getattr(args, "config", None)
    path = config_path(explicit)

    if getattr(args, "path", False):
        print(path)
        return 0

    if getattr(args, "init", False):
        if path.exists():
            print(f"config already exists at {path} — nothing written")
            return 0
        default = _bundled_default()
        if default is None:
            print("no bundled default config to copy from")
            return 1
        target = trinity_home() / "config.json" if explicit is None else path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(default, encoding="utf-8")
        print(f"wrote {target}")
        path = target

    if args.assignments:
        if not path.exists():
            print(f"no config at {path} — run `trinity-local config --init` first")
            return 1
        raw = json.loads(path.read_text(encoding="utf-8"))
        applied = []
        for assignment in args.assignments:
            try:
                applied.append(_apply(raw, assignment))
            except ValueError as exc:
                print(f"refused: {exc}")
                return 1
        backup = path.with_suffix(".json.bak")
        shutil.copyfile(path, backup)
        path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        for line in applied:
            print(f"set {line}")
        print(f"wrote {path}  (previous version at {backup.name})")

    cfg = load_config(explicit, required=False)
    rows = []
    for name, provider in sorted(cfg.providers.items()):
        rows.append({
            "provider": name,
            "enabled": provider.enabled,
            "model": provider.model,
            "effort": provider.effort,
            "command": " ".join(provider.command),
        })
    chair = getattr(cfg, "default_primary_provider", None)

    if getattr(args, "as_json", False):
        print(json.dumps({
            "config_path": str(path),
            "exists": path.exists(),
            "state_dir": str(trinity_home()),
            "repo_root": str(project_root()),
            "default_primary_provider": chair,
            "providers": rows,
        }, indent=2))
        return 0

    print(f"config: {path}{'' if path.exists() else '  (not created yet — using bundled defaults)'}")
    print(f"default chair: {chair or '(none — first available provider)'}")
    print()
    for row in rows:
        mark = "on " if row["enabled"] else "off"
        effort = f" · {row['effort']}" if row["effort"] else ""
        chair_mark = "  <- chair" if row["provider"] == chair else ""
        print(f"  {mark}  {row['provider']:<12} "
              f"{row['model'] or '(cli default)'}{effort}{chair_mark}")
    if not path.exists():
        print("\nrun `trinity-local config --init` to create a config you can edit")
    return 0
