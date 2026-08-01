"""A skipped file must say WHICH kind of skip it was.

THE DEFECT (measured 2026-07-31 on the live corpus). `IngestResult.skipped_parse`
counted two unrelated things under one name: "I could not read this file" and "I
read this file fine and there is nothing in it to extract". browser_gemini
reported 2,740 "parse failures" over 4,322 capture files. The real breakdown:

    parsed into a session                        1,582
    well-formed, no completed assistant turn     2,739
    unreadable                                       1   (`_sidebar.json`)

Gemini has no canonical full-conversation fetch, so the extension writes one
capture file per batchexecute network frame and only some frames close out an
assistant turn. Files with nothing in them yet ARE the normal shape of that
source. A counter that reports the ordinary case as breakage trains the reader
to ignore the counter — which is how a REAL parser regression would then ship
unnoticed.

The single "unreadable" file was `_sidebar.json`, provider metadata rather than
a conversation — one per provider, so all three browser sources carried a
permanent skipped_parse of 1 that no user action could clear. It is no longer
walked (see TestSentinelsAreNotConversations below), which is what makes the
post-split `skipped_parse == 0` on that corpus a real zero rather than a
rounding of "one thing we always fail on".

The split has to hold at BOTH ends or it is just a rename:
  - a well-formed-but-empty file must land in skipped_empty (never skipped_parse)
  - a genuinely unreadable file must still land in skipped_parse
  - a source whose parser cannot tell the two apart must NOT get the benefit of
    the doubt — silence is not evidence of emptiness.
"""
from __future__ import annotations

import json

import pytest

from trinity_local.incremental_ingest import ingest_recent
from trinity_local.ingest import is_user_facing_text


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def _capture_dir(home):
    d = home / "conversations" / "gemini"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _frame(conv_id, **over):
    """The adapter_stream payload browser-extension/adapters/gemini.js writes."""
    payload = {
        "provider": "gemini",
        "conv_id": conv_id,
        "captured_at": "2026-07-31T00:00:00Z",
        "message_id": f"msg-{conv_id}",
        "user_text": "why did the council split on this",
        "assistant_text": "Because the members disagreed on the premise.",
        "frames_count": 3,
        "events_count": 7,
    }
    payload.update(over)
    return payload


class TestEmptyIsNotBreakage:
    def test_well_formed_frame_with_no_completed_turn_is_empty_not_a_parse_failure(
        self, isolated_home
    ):
        """The measured case: the majority shape of the source. Reported as
        skipped_parse before this split."""
        d = _capture_dir(isolated_home)
        for i in range(5):
            # An in-flight frame: the RPC was captured, the reply had not landed.
            (d / f"c{i}.stream.json").write_text(
                json.dumps(_frame(f"c{i}", assistant_text="")), encoding="utf-8"
            )

        result = ingest_recent(sources=["browser_gemini"], deadline_s=10.0)

        assert result.scanned == 5
        assert result.skipped_empty == 5, result.to_dict()
        assert result.skipped_parse == 0, (
            "a well-formed capture that simply has no finished turn is not a "
            f"parse failure; got {result.to_dict()}"
        )

    def test_missing_assistant_key_entirely_is_also_empty(self, isolated_home):
        """Absent key, not just an empty string — the older adapter shape."""
        d = _capture_dir(isolated_home)
        payload = _frame("c-nokey")
        del payload["assistant_text"]
        (d / "c-nokey.stream.json").write_text(json.dumps(payload), encoding="utf-8")

        result = ingest_recent(sources=["browser_gemini"], deadline_s=10.0)
        assert (result.skipped_empty, result.skipped_parse) == (1, 0), result.to_dict()

    def test_parsed_session_with_zero_prompt_turns_is_empty(self, isolated_home):
        """A frame that carries the REPLY but not the prompt parses into a real
        session that yields no user-facing turn. Before the split this was
        counted NOWHERE — invisible in every bucket."""
        d = _capture_dir(isolated_home)
        (d / "c-reply-only.stream.json").write_text(
            json.dumps(_frame("c-reply-only", user_text="")), encoding="utf-8"
        )

        result = ingest_recent(sources=["browser_gemini"], deadline_s=10.0)
        assert result.added == 0
        assert (result.skipped_empty, result.skipped_parse) == (1, 0), result.to_dict()


class TestBreakageStillReadsAsBreakage:
    """The other half of the split. Without these, 'skipped_parse == 0' would be
    green because everything got relabelled, not because nothing broke."""

    def test_unreadable_and_unrecognized_files_stay_in_skipped_parse(self, isolated_home):
        d = _capture_dir(isolated_home)
        (d / "a.stream.json").write_text("{not json at all", encoding="utf-8")
        (d / "b.stream.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        (d / "c.stream.json").write_text(
            json.dumps(_frame("c", provider="chatgpt")), encoding="utf-8"
        )
        malformed = _frame("d")
        malformed["conv_id"] = ""  # recognizable gemini payload, no conversation
        (d / "d.stream.json").write_text(json.dumps(malformed), encoding="utf-8")

        result = ingest_recent(sources=["browser_gemini"], deadline_s=10.0)

        assert result.scanned == 4
        assert result.skipped_parse == 4, result.to_dict()
        assert result.skipped_empty == 0, (
            f"broken files were quietly reclassified as empty; got {result.to_dict()}"
        )

    def test_a_source_without_a_classifying_parser_never_claims_empty(
        self, isolated_home, monkeypatch, tmp_path
    ):
        """Silence is not evidence. `claude` has no classified parser, so a
        None from it must keep meaning 'unreadable' — the pre-split meaning —
        rather than defaulting into the reassuring bucket."""
        from trinity_local import watch_runtime

        f = tmp_path / "s.jsonl"
        f.write_text("{}\n", encoding="utf-8")
        monkeypatch.setattr(
            watch_runtime, "_iter_recent_paths",
            lambda source, since: iter([f]) if source == "claude" else iter([]),
        )
        monkeypatch.setattr(watch_runtime, "_parse_source_path", lambda source, p: None)

        result = ingest_recent(sources=["claude"], deadline_s=10.0)
        assert (result.skipped_parse, result.skipped_empty) == (1, 0), result.to_dict()

    def test_a_raising_parser_is_a_parse_failure(self, isolated_home, monkeypatch, tmp_path):
        from trinity_local import watch_runtime

        f = tmp_path / "s.jsonl"
        f.write_text("{}\n", encoding="utf-8")

        def _boom(source, path):
            raise ValueError("parser blew up")

        monkeypatch.setattr(
            watch_runtime, "_iter_recent_paths",
            lambda source, since: iter([f]) if source == "claude" else iter([]),
        )
        monkeypatch.setattr(watch_runtime, "_parse_source_path", _boom)

        result = ingest_recent(sources=["claude"], deadline_s=10.0)
        assert (result.skipped_parse, result.skipped_empty) == (1, 0), result.to_dict()


class TestSentinelsAreNotConversations:
    """The residual permanent 1.

    After the empty/parse split the live corpus still reported exactly one
    unreadable file per browser source, every pass, forever: `_sidebar.json`,
    the recent-conversations snapshot the extension writes alongside real
    captures. `capture_host.iter_capture_files` — the documented single source
    of truth for "which files count as a captured conversation" — has always
    skipped `_`-prefixed sentinels and `stream-<urlhash>.json` raw-fallback
    orphans; the ingest walker did not, so it fed provider metadata to a
    conversation parser and counted the refusal as breakage.

    A permanent 1 is the same defect as a permanent 2,740, just quieter: it is
    unclearable, and it means "skipped_parse is nonzero" can never be the alarm
    it is supposed to be.
    """

    def test_the_sidebar_sentinel_is_not_walked_at_all(self, isolated_home):
        """Not merely reclassified — not scanned. It is not a conversation."""
        d = _capture_dir(isolated_home)
        (d / "real.stream.json").write_text(json.dumps(_frame("real")), encoding="utf-8")
        (d / "_sidebar.json").write_text(
            json.dumps({"provider": "gemini", "kind": "sidebar_list",
                        "captured_at": "2026-07-31T00:00:00Z",
                        "sidebar": [{"title": "a thread"}]}),
            encoding="utf-8",
        )

        result = ingest_recent(sources=["browser_gemini"], deadline_s=10.0)

        assert result.scanned == 1, (
            f"the sentinel was walked as if it were a transcript: {result.to_dict()}"
        )
        assert (result.skipped_parse, result.skipped_empty) == (0, 0), result.to_dict()
        assert result.added >= 1, "the real capture beside it must still ingest"

    def test_the_same_sentinel_is_skipped_for_claude_captures(self, isolated_home):
        """One `_sidebar.json` exists per provider directory, so the fix has to
        hold on the canonical-JSON sources too, not just gemini."""
        d = isolated_home / "conversations" / "claude"
        d.mkdir(parents=True)
        (d / "_sidebar.json").write_text(
            json.dumps({"provider": "claude", "kind": "sidebar_list", "sidebar": []}),
            encoding="utf-8",
        )
        result = ingest_recent(sources=["browser_claude"], deadline_s=10.0)
        assert result.scanned == 0, result.to_dict()
        assert result.skipped_parse == 0, result.to_dict()

    def test_raw_fallback_orphans_are_skipped(self, isolated_home):
        """`stream-<urlhash>.json` — written when no adapter matches the domain,
        never carries a conv_id. NOTE: zero of these exist on the corpus this
        was measured against; the shape is filtered because capture_host writes
        it, not because one was observed."""
        d = _capture_dir(isolated_home)
        (d / "stream-9f2a11.json").write_text(
            json.dumps({"provider": "gemini", "url": "https://gemini.google.com/"}),
            encoding="utf-8",
        )
        result = ingest_recent(sources=["browser_gemini"], deadline_s=10.0)
        assert result.scanned == 0, result.to_dict()

    def test_the_filter_does_not_swallow_real_captures(self, isolated_home):
        """The over-broad direction. conv_ids are opaque hashes and a filter on
        'contains an underscore' or 'contains the word stream' would eat the
        gemini frame files, whose canonical name is `<conv_id>__<ts>.stream`."""
        d = _capture_dir(isolated_home)
        (d / "ff78f4ff80b72902__20260801021433845.stream.json").write_text(
            json.dumps(_frame("ff78f4ff80b72902")), encoding="utf-8")
        (d / "a_b_c.stream.json").write_text(
            json.dumps(_frame("a_b_c")), encoding="utf-8")

        result = ingest_recent(sources=["browser_gemini"], deadline_s=10.0)
        assert result.scanned == 2, result.to_dict()
        assert result.added >= 2, result.to_dict()


class TestMixedCorpusAndTheShippingSurface:
    def test_counters_partition_the_scanned_files(self, isolated_home):
        """Every scanned file lands in exactly one FILE bucket — no double
        count, no file that falls through all of them. (`added` and
        `skipped_existing` count TURNS, not files; each capture here carries
        exactly one turn, which is what makes them comparable in this fixture.)
        """
        d = _capture_dir(isolated_home)
        (d / "good.stream.json").write_text(json.dumps(_frame("good")), encoding="utf-8")
        (d / "empty1.stream.json").write_text(
            json.dumps(_frame("e1", assistant_text="")), encoding="utf-8")
        (d / "empty2.stream.json").write_text(
            json.dumps(_frame("e2", assistant_text="   ")), encoding="utf-8")
        (d / "broken.stream.json").write_text("<html>429</html>", encoding="utf-8")

        result = ingest_recent(sources=["browser_gemini"], deadline_s=10.0)

        assert result.scanned == 4
        assert result.added >= 1
        assert result.skipped_empty == 2, result.to_dict()
        assert result.skipped_parse == 1, result.to_dict()
        accounted = (result.added + result.skipped_existing + result.skipped_parse
                     + result.skipped_empty + result.skipped_unchanged)
        assert accounted == result.scanned, (
            f"{result.scanned - accounted} scanned file(s) landed in no bucket: "
            f"{result.to_dict()}"
        )

    def test_a_quiet_source_does_not_lose_its_own_boundary_file(self, isolated_home):
        """The steady state of every drained source, and the shape the live
        `gemini` source reports on EVERY pass.

        The inclusive `>=` boundary re-lists the last file forever, and the
        drained_path/drained_size record skips it without opening it — correct,
        and it used to leave `scanned=1` with every other counter at 0, i.e. a
        file that disappeared out of its own accounting. Anyone reading that
        line would conclude ingest had silently dropped something."""
        d = _capture_dir(isolated_home)
        (d / "c1.stream.json").write_text(json.dumps(_frame("c1")), encoding="utf-8")

        first = ingest_recent(sources=["browser_gemini"], deadline_s=10.0)
        assert (first.scanned, first.added, first.skipped_unchanged) == (1, 1, 0), \
            first.to_dict()

        for _ in range(2):
            steady = ingest_recent(sources=["browser_gemini"], deadline_s=10.0)
            assert steady.scanned == 1, steady.to_dict()
            assert steady.skipped_unchanged == 1, (
                "the re-listed boundary file is still unaccounted for: "
                f"{steady.to_dict()}"
            )
            assert (steady.added, steady.skipped_parse, steady.skipped_empty) == (0, 0, 0), \
                steady.to_dict()

    def test_cli_json_output_carries_both_counters(self, isolated_home, capsys):
        """`trinity-local ingest-recent` prints IngestResult.to_dict(). A field
        added to the dataclass but not threaded into to_dict() vanishes at the
        only surface a user reads (this repo has lost a field that way before —
        the chairman's `resolution`)."""
        from types import SimpleNamespace
        from trinity_local.commands.watch import handle_ingest_recent

        d = _capture_dir(isolated_home)
        (d / "e.stream.json").write_text(
            json.dumps(_frame("e", assistant_text="")), encoding="utf-8")

        handle_ingest_recent(SimpleNamespace(sources=["browser_gemini"], deadline=10.0))
        out = json.loads(capsys.readouterr().out)

        assert "skipped_empty" in out, f"skipped_empty never reaches the CLI: {out}"
        assert "skipped_parse" in out
        assert "skipped_unchanged" in out, f"skipped_unchanged never reaches the CLI: {out}"
        assert (out["skipped_empty"], out["skipped_parse"]) == (1, 0), out


class TestHarnessBlocksFound20260801:
    """The six block tags surfaced by scripts/find_generator_families.py.

    These were live in the corpus (409 accepted nodes) while the filter listed
    ~20 sibling tags from the SAME family. That is the failure the audit script
    was written for: the pattern list only grows when a human happens to look,
    and the previous two additions were months apart.

    Each case below is a real shape sampled from the store, not an invention.
    """

    def test_recommended_plugins_block_is_not_user_authored(self):
        # 338 nodes. A harness block instructing the MODEL about plugin installs.
        assert not is_user_facing_text(
            "<recommended_plugins>\nHere is a list of plugins that are available but "
            "not installed. If the user's query would benefit from one of these "
            "plugins, use the `request_plugin_install` tool"
        )

    def test_bash_mode_io_is_not_user_authored(self):
        # Same class as <local-command-stdout>, which was already filtered.
        assert not is_user_facing_text(
            "<bash-input> sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder</bash-input>"
        )
        assert not is_user_facing_text(
            "<bash-stdout></bash-stdout><bash-stderr>sudo: a terminal is required to "
            "read the password</bash-stderr>"
        )

    def test_autonomous_loop_sentinel_is_not_user_authored(self):
        # A scheduler token. A human never types it.
        assert not is_user_facing_text(
            "<<autonomous-loop-dynamic>>\n\nAUTONOMOUS TASK (user is asleep):\n\n"
            "1. Check pull status: `ollama list`"
        )

    def test_harness_command_and_action_envelopes_are_not_user_authored(self):
        assert not is_user_facing_text(
            "<user_shell_command>\n<command>\nnpm run image:paired:chatgpt\n</command>\n"
            "<result>\nExit code: 254\n</result>"
        )
        assert not is_user_facing_text(
            "<user_action>\n  <context>User initiated a review task. Here's the full "
            "review output from reviewer model.</context>\n  <action>review</action>"
        )

    def test_role_template_is_DELIBERATELY_still_accepted(self):
        """`<role>` was found in the same sweep and deliberately NOT added.

        It opens a role-play prompt template ("You are a pragmatic business
        strategist...") that a user could plausibly have pasted, and it had a
        single node. Dropping an ambiguous shape to gain one node is how a
        purity filter starts eating real prompts. If this ever flips, it should
        be because someone decided that on evidence — so the decision is pinned
        here rather than left to drift.
        """
        assert is_user_facing_text(
            "<role>You are a pragmatic business strategist with expertise in "
            "dissecting business ideas for real-world applicability.</role> "
            "<task>Analyze the given business idea objectively</task>"
        )

    def test_genuine_prompts_that_merely_mention_these_words_survive(self):
        """The filter is prefix-anchored on purpose. A user writing ABOUT these
        things must not be dropped."""
        assert is_user_facing_text(
            "can we suppress the recommended_plugins block from reaching the lens?"
        )
        assert is_user_facing_text(
            "the bash-stdout handling is wrong, it swallows stderr — fix it"
        )
