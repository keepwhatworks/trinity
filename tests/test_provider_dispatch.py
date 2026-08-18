

def test_codex_model_guard_honours_the_short_flag_alias(monkeypatch, tmp_path):
    """`-m` is the Codex CLI's own alias for `--model`. The duplicate-model
    guard used to check only the long spelling, so a caller passing `-m` got
    BOTH flags on argv -> the CLI exits 2 with "cannot be used multiple times"
    and empty stdout. That surfaces as an unusable RESULT (a model failure)
    rather than a bad argv, which is a maddening thing to debug. Pin both
    spellings; drop either arm of the guard and this reds."""
    import dataclasses

    from trinity_local.providers import make_provider

    captured: dict[str, list[str]] = {}

    class _Fake:
        returncode = 0
        stdout = "{}"
        stderr = ""

    def _fake_run(cmd, *a, **kw):
        captured["argv"] = list(cmd)
        return _Fake()

    monkeypatch.setattr("subprocess.run", _fake_run)
    from trinity_local.config import load_config

    cfg = load_config(None).providers["codex"]
    for spelling in ("-m", "--model"):
        cfg2 = dataclasses.replace(
            cfg, model="gpt-5.6-luna",
            args=["--sandbox", "read-only", spelling, "gpt-5.6-terra"],
        )
        make_provider(cfg2).run("hi", tmp_path)
        argv = captured["argv"]
        n_model = sum(1 for a in argv if a in ("-m", "--model"))
        assert n_model == 1, (
            f"caller passed {spelling!r}; argv carries {n_model} model flags "
            f"({argv}). Two model flags make the CLI exit 2 with empty stdout, "
            "which reads as a model failure instead of an argv bug."
        )


def test_codex_model_guard_covers_joined_and_clustered_spellings():
    """A caller-supplied model must suppress the default in EVERY spelling.

    getopt accepts four ways to say the same thing and the guard originally
    matched two of them. Appending a second --model makes the Codex CLI exit 2
    with empty stdout, which the dispatch layer reports as a model failure — so a
    missed spelling shows up as "the model broke", not "we built bad argv"."""
    from trinity_local.providers import _has_model_flag

    for spelling in (["--model", "gpt-5.6"], ["-m", "gpt-5.6"],
                     ["--model=gpt-5.6"], ["-mgpt-5.6"]):
        assert _has_model_flag(spelling), (
            f"{spelling} reads as no-model-set, so dispatch appends a SECOND "
            f"--model and the Codex CLI exits 2 with empty stdout"
        )
    # ...and it must not fire on argv that merely mentions something m-ish,
    # or dispatch would silently drop the configured model.
    for benign in (["exec"], ["--sandbox", "read-only"], ["--skip-git-repo-check"]):
        assert not _has_model_flag(benign), f"{benign} falsely reads as model-set"


def test_config_lifts_every_model_spelling_into_the_identity_stamp():
    """Whatever spelling dispatches the model must also be RECORDED as the model.

    providers._has_model_flag recognises four spellings so dispatch never appends
    a duplicate flag. config._reconcile_model_arg must lift the same four into
    config.model, which is what the recording path stamps. If dispatch knows a
    spelling the extractor does not, the run succeeds while the council record and
    the disagreement ledger — which keys on model x version — name a model that
    never ran. That is strictly worse than the duplicate-flag crash it replaced,
    because nothing surfaces it.
    """
    from trinity_local.config import _reconcile_model_arg

    for spelling in (["--model", "gpt-5.6"], ["-m", "gpt-5.6"],
                     ["--model=gpt-5.6"], ["-mgpt-5.6"]):
        model, _cmd, args = _reconcile_model_arg(["codex"], list(spelling), "gpt-5.5")
        assert model == "gpt-5.6", (
            f"{spelling} dispatched gpt-5.6 but the identity stamp recorded "
            f"{model!r} — the ledger would key the result to the wrong model")
        assert not any(a.startswith("-m") for a in args), (
            f"{spelling} was lifted but not stripped from args: {args}")
