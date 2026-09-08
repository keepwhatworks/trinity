"""One setting to send every council to one subscription.

Asked for 2026-09-04 by a user weighing which vendor gets the expensive plan.
Trinity already dispatches wherever you point it, but `primary_provider` had to
be passed on every call, so "all my work lands on X" was a per-call discipline
rather than a setting.

Unset, nothing changes. Set, it must still refuse to name a chair that cannot
dispatch: a missing or disabled chair fails the whole council, while falling
through to the built-in default succeeds.
"""
from __future__ import annotations

import dataclasses

from trinity_local.config import AppConfig, default_primary_provider, load_config


def _cfg(**kw) -> AppConfig:
    base = load_config()
    return dataclasses.replace(base, **kw)


class TestTheSetting:
    def test_unset_changes_nothing(self):
        assert default_primary_provider(_cfg(default_primary_provider=None)) is None

    def test_an_enabled_provider_is_returned(self):
        assert default_primary_provider(_cfg(default_primary_provider="codex")) == "codex"

    def test_a_disabled_provider_is_refused(self):
        cfg = load_config()
        providers = dict(cfg.providers)
        providers["codex"] = dataclasses.replace(providers["codex"], enabled=False)
        cfg = dataclasses.replace(cfg, providers=providers, default_primary_provider="codex")
        assert default_primary_provider(cfg) is None, (
            "naming a disabled chair fails the whole council; falling through to "
            "the built-in default succeeds"
        )

    def test_a_provider_that_does_not_exist_is_refused(self):
        assert default_primary_provider(_cfg(default_primary_provider="nope")) is None

    def test_blank_is_treated_as_unset(self):
        assert default_primary_provider(_cfg(default_primary_provider="  ")) is None


class TestItLoadsFromConfigJson:
    def test_the_key_is_read(self, tmp_path, monkeypatch):
        import json
        from trinity_local.config import load_config as lc
        base = json.loads(open("config.json").read())
        base["default_primary_provider"] = "codex"
        p = tmp_path / "config.json"
        p.write_text(json.dumps(base))
        assert lc(p).default_primary_provider == "codex"

    def test_absence_is_none_not_a_crash(self, tmp_path):
        import json
        from trinity_local.config import load_config as lc
        base = json.loads(open("config.json").read())
        base.pop("default_primary_provider", None)
        p = tmp_path / "config.json"
        p.write_text(json.dumps(base))
        assert lc(p).default_primary_provider is None
