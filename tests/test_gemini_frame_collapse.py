"""Gemini SSE-frame collapse: iter_capture_files counts CONVERSATIONS, not frames.

Gemini writes one `<conv_id>__<ts>.stream.json` per SSE frame (Google's batchexecute is
reply-only streaming), so a single conversation explodes into dozens of files — measured on
the real corpus: 47 conversations -> 2465 frame-files. Counting raw frames inflated the
launchpad's "browser capture" total ~17× (it showed 2553 captures for ~143 real
conversations) — the recurring degenerate-green bug shape. iter_capture_files now collapses
frames to one representative per conversation, keeping the latest frame so mtime/24h stay right.
"""
from __future__ import annotations

import os

import pytest

from trinity_local.capture_host import iter_capture_files


@pytest.fixture
def gem(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    d = tmp_path / "conversations" / "gemini"
    d.mkdir(parents=True)
    return d


def test_frames_of_one_conversation_collapse_to_one(gem):
    for i in range(50):
        (gem / f"convA__2026060300{i:04d}.stream.json").write_text("{}", encoding="utf-8")
    assert len(iter_capture_files()) == 1, "50 SSE frames of ONE conversation must count as 1 capture"


def test_distinct_conversations_counted_separately(gem):
    for conv in ("convA", "convB", "convC"):
        for i in range(10):
            (gem / f"{conv}__{i:05d}.stream.json").write_text("{}", encoding="utf-8")
    assert len(iter_capture_files()) == 3


def test_sidebar_and_orphans_not_counted(gem):
    (gem / "convA__001.stream.json").write_text("{}", encoding="utf-8")
    (gem / "_sidebar.json").write_text("{}", encoding="utf-8")
    (gem / "stream-deadbeef.json").write_text("{}", encoding="utf-8")
    assert len(iter_capture_files()) == 1  # only convA is a conversation


def test_plain_json_gemini_conversation_still_counts(gem):
    """The non-streaming one-file-per-conversation shape stays 1 capture each (the fixture +
    edge shape) — the collapse must not erase genuine conversations."""
    (gem / "convA.json").write_text("{}", encoding="utf-8")
    (gem / "convB.json").write_text("{}", encoding="utf-8")
    assert len(iter_capture_files()) == 2


def test_latest_frame_is_the_representative(gem):
    early = gem / "convA__001.stream.json"; early.write_text("{}", encoding="utf-8")
    late = gem / "convA__002.stream.json"; late.write_text("{}", encoding="utf-8")
    os.utime(early, (1000, 1000))
    os.utime(late, (2000, 2000))
    files = iter_capture_files()
    assert len(files) == 1 and files[0].name == "convA__002.stream.json", \
        "the latest frame is kept so last-capture / 24h counts stay correct"
