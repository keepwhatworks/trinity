"""MCP Resources surface — Phase A of the v2 substrate arc (task #162).

Trinity exposes ~/.trinity/memories/ and the ~/.trinity/scoreboard/
files as MCP Resources. Resources are listed at session start so any
MCP-aware harness sees them without a tool round-trip — the agent
reads `trinity://memories/lens.md` before the user types a prompt
and conditions every response on the lens.

The contract these tests pin:

1. The catalog enumerates exactly the six canonical resources (4
   memories + 2 scoreboards). Adding or removing one should be a
   deliberate spec change, not a silent drift. AGENTS.md was dropped
   2026-05-26 — see _resource_catalog docstring for the rationale.
2. URIs follow `trinity://` scheme (per the v2 spec at
   docs/PREFERENCE_CORPUS_SPEC.md).
3. Cold-install reads (when the underlying file doesn't exist)
   return a stub with an actionable next-step (`trinity-local dream`),
   NOT a 404. The stub is what makes the agent useful out of the box:
   it can tell the user "your lens isn't built yet — run dream first."
4. Populated reads return raw file contents byte-for-byte.
"""
from __future__ import annotations

import asyncio
import json

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Isolate ~/.trinity/ so cold-install + populated-install paths
    are testable in parallel without leaking state."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def populated_home(isolated_home):
    """Seed the four cognitive memories + scoreboards with known
    content so the read path can assert on body bytes.

    NOTE: core.md lives at the TOP LEVEL (~/.trinity/core.md), NOT under
    memories/ like the other three. Seeding it in the wrong place is exactly
    what let the 2026-05-31 truncation bug hide: the resource read missed the
    real (top-level) file and fell through to the cold-start stub, but a
    fixture that also wrote to memories/core.md made the read 'succeed' on the
    wrong file. Mirror the real layout here so the read path is tested honestly."""
    (isolated_home / "core.md").write_text("# Core\nidentity-paragraph", encoding="utf-8")
    memories = isolated_home / "memories"
    memories.mkdir(parents=True, exist_ok=True)
    (memories / "lens.md").write_text("# Lens\npaired tensions here", encoding="utf-8")
    (memories / "topics.json").write_text(json.dumps({"basins": []}), encoding="utf-8")
    (memories / "vocabulary.md").write_text("# Vocabulary\nanchors", encoding="utf-8")
    scoreboard = isolated_home / "scoreboard"
    scoreboard.mkdir(parents=True, exist_ok=True)
    (scoreboard / "picks.json").write_text(json.dumps({"rules": {}}), encoding="utf-8")
    (scoreboard / "routing.json").write_text(json.dumps({"task_types": {}}), encoding="utf-8")
    return isolated_home


class TestResourceCatalog:
    """The catalog is the contract — adding/removing a resource is a
    deliberate spec change, not a silent drift. The six canonical
    resources MUST be exactly: core / lens / topics / vocabulary /
    picks / routing. AGENTS.md was on this list briefly but dropped
    2026-05-26 — AGENTS.md is project-scoped by convention (./AGENTS.md
    in the user's repo) and exposing a user-home one was ceremonial;
    every harness that reads AGENTS.md also reads MCP Resources, so
    the lens flows via trinity://memories/lens.md."""

    def test_catalog_has_six_canonical_resources(self, isolated_home):
        from trinity_local.mcp_server import _resource_catalog
        catalog = _resource_catalog()
        uris = {entry[0] for entry in catalog}
        assert uris == {
            "trinity://memories/core.md",
            "trinity://memories/lens.md",
            "trinity://memories/topics.json",
            "trinity://memories/vocabulary.md",
            "trinity://scoreboard/picks.json",
            "trinity://scoreboard/routing.json",
        }, (
            "Resource catalog drifted from the v2 substrate spec. "
            "Adding/removing a resource is a deliberate change — update "
            "docs/PREFERENCE_CORPUS_SPEC.md schemas table AND this test."
        )

    def test_agentsmd_not_in_catalog(self, isolated_home):
        """AGENTS.md was dropped 2026-05-26. Regression guard against
        a future PR adding it back without revisiting the rationale:
        AGENTS.md is project-scoped; harnesses that read it also read
        MCP Resources; exposing it as a user-home resource was
        ceremonial."""
        from trinity_local.mcp_server import _resource_catalog
        uris = {entry[0] for entry in _resource_catalog()}
        assert "trinity://AGENTS.md" not in uris, (
            "trinity://AGENTS.md re-appeared in the catalog. Per the "
            "2026-05-26 decision, AGENTS.md is not a Trinity-exposed "
            "surface — the lens flows via trinity://memories/lens.md. "
            "If reviving, update the docstring + spec first."
        )

    def test_each_entry_has_description_and_mime(self, isolated_home):
        from trinity_local.mcp_server import _resource_catalog
        for uri, name, description, mime, path_func in _resource_catalog():
            assert name, f"Resource {uri} missing name"
            assert description, f"Resource {uri} missing description"
            assert mime in ("text/markdown", "application/json"), (
                f"Resource {uri} has unexpected MIME type: {mime}"
            )
            # path_func must be callable + return a Path (not eval it yet)
            assert callable(path_func), f"Resource {uri} path is not a callable"

    def test_core_md_path_func_resolves_to_top_level_core_path(self, isolated_home):
        """The core.md resource MUST resolve to state_paths.core_path()
        (~/.trinity/core.md), NOT memories/core.md. lens/topics/vocabulary
        live under memories/, but core.md is top-level — the URI keeps the
        memories/ namespace for back-compat, the path must not. Guards the
        2026-05-31 truncation bug from regressing."""
        from trinity_local.mcp_server import _resource_catalog
        from trinity_local import state_paths as sp
        catalog = {entry[0]: entry for entry in _resource_catalog()}
        core_path_func = catalog["trinity://memories/core.md"][4]
        resolved = core_path_func()
        assert resolved == sp.core_path(), (
            f"core.md resource resolves to {resolved}, expected "
            f"{sp.core_path()}. core.md is top-level, not under memories/ — "
            f"reading the wrong path returns the cold-start stub forever."
        )
        assert resolved.name == "core.md"
        assert resolved.parent == sp.state_dir(), (
            "core.md must sit directly in ~/.trinity/, not a subdirectory."
        )

    def test_uri_scheme_is_trinity(self, isolated_home):
        from trinity_local.mcp_server import _resource_catalog
        for uri, *_ in _resource_catalog():
            assert uri.startswith("trinity://"), (
                f"Resource URI {uri!r} doesn't use the trinity:// scheme — "
                f"per the v2 spec all Trinity resources MUST be trinity:// "
                f"so harnesses can disambiguate from other MCP servers'."
            )


class TestListResources:
    """The MCP server's list_resources handler must advertise all 6
    canonical resources unconditionally — even when the underlying
    files don't exist yet (cold install). The READ path handles
    cold-install via stubs; the LIST path always shows the catalog."""

    def test_list_returns_all_resources_on_cold_install(self, isolated_home):
        from trinity_local.mcp_server import handle_list_resources
        resources = asyncio.run(handle_list_resources())
        assert len(resources) == 6, (
            f"Cold install should still advertise all 6 resources (so the "
            f"agent sees them + reads the stubs that explain how to populate); "
            f"got {len(resources)}"
        )
        uris = {str(r.uri) for r in resources}
        assert "trinity://memories/lens.md" in uris

    def test_list_returns_same_resources_when_populated(self, populated_home):
        from trinity_local.mcp_server import handle_list_resources
        resources = asyncio.run(handle_list_resources())
        assert len(resources) == 6

    def test_resource_objects_have_required_fields(self, isolated_home):
        """Each Resource the harness lists must have uri/name/description/
        mimeType — without all four, the harness's resource picker UI
        renders broken entries (no description = the user has no idea
        what they're enabling)."""
        from trinity_local.mcp_server import handle_list_resources
        resources = asyncio.run(handle_list_resources())
        for r in resources:
            assert r.uri is not None
            assert r.name
            assert r.description
            assert r.mimeType in ("text/markdown", "application/json")


class TestReadResourcePopulated:
    """When the file exists on disk, read_resource returns its contents.

    Markdown resources (core / lens / vocabulary) are served byte-for-byte —
    they're already agent-readable. JSON resources (topics / picks / routing)
    are PROJECTED for the agent: embedding centroids and opaque ID/membership
    lists are stripped before serving (see _project_json_for_agent), because
    an LLM reading the resource can't use a 768-float vector and topics.json
    raw is ~2.2 MB of them. The meaningful fields (labels, representatives,
    track records) round-trip unchanged."""

    def test_core_md_returns_top_level_file_not_stub(self, populated_home):
        """core.md lives at ~/.trinity/core.md (top level), NOT memories/core.md.
        Regression guard for the 2026-05-31 bug where the catalog path_func
        pointed at memories/core.md — which never exists — so the chairman's
        FIRST resource always read back the cold-start stub even on a fully
        dreamed install. The most load-bearing persona surface must deliver
        the real distilled identity, byte-for-byte, not the 'run dream' stub."""
        from pydantic import AnyUrl
        from trinity_local.mcp_server import handle_read_resource
        result = list(asyncio.run(
            handle_read_resource(AnyUrl("trinity://memories/core.md"))))
        assert result[0].content == "# Core\nidentity-paragraph", (
            "core.md resource did not return the real top-level core.md — "
            "if it came back as a 'run trinity-local dream' stub, the path_func "
            "is pointing at memories/core.md instead of state_paths.core_path()."
        )
        assert "_(empty" not in result[0].content, (
            "core.md resource returned the cold-start stub despite a populated "
            "top-level core.md — the path_func regressed to the wrong location."
        )
        assert result[0].mime_type == "text/markdown"

    def test_lens_md_returns_file_contents_as_markdown(self, populated_home):
        from pydantic import AnyUrl
        from trinity_local.mcp_server import handle_read_resource
        result = list(asyncio.run(
            handle_read_resource(AnyUrl("trinity://memories/lens.md"))))
        assert result[0].content == "# Lens\npaired tensions here"
        # The read mimeType must match what list_resources advertised — a
        # bare-str return would default to text/plain (live 2026-05-31:
        # every read came back text/plain, a list/read mismatch).
        assert result[0].mime_type == "text/markdown"

    def test_topics_json_returns_file_contents_as_json(self, populated_home):
        from pydantic import AnyUrl
        from trinity_local.mcp_server import handle_read_resource
        result = list(asyncio.run(
            handle_read_resource(AnyUrl("trinity://memories/topics.json"))))
        # Round-trip — content is whatever the test fixture wrote
        assert json.loads(result[0].content) == {"basins": []}
        assert result[0].mime_type == "application/json"

    def test_picks_json_returns_file_contents_as_json(self, populated_home):
        from pydantic import AnyUrl
        from trinity_local.mcp_server import handle_read_resource
        result = list(asyncio.run(
            handle_read_resource(AnyUrl("trinity://scoreboard/picks.json"))))
        assert json.loads(result[0].content) == {"rules": {}}
        assert result[0].mime_type == "application/json"


class TestJsonResourceProjection:
    """JSON resources are projected for the agent before serving: embedding
    vectors (768-dim centroids) and opaque ID/membership lists (prompt_ids)
    are stripped. Without this, an agent reading trinity://memories/topics.json
    gets ~2.2 MB of float vectors per read — wasteful AND it buries the signal
    (labels, representatives) the resource exists to surface. The meaningful
    fields must round-trip intact; only the engine internals drop out."""

    def test_topics_resource_strips_centroid_and_prompt_ids(self, isolated_home):
        from pydantic import AnyUrl
        from trinity_local.mcp_server import handle_read_resource
        memories = isolated_home / "memories"
        memories.mkdir(parents=True, exist_ok=True)
        (memories / "topics.json").write_text(json.dumps({"basins": [{
            "id": "b00",
            "label": "floor-plan engine",
            "top_terms": ["floor", "plan", "engine"],
            "representatives": ["how do I render the floor plan?"],
            "size": 42,
            "centroid": [0.01 * i for i in range(768)],   # must be stripped
            "prompt_ids": [f"p{i:04d}" for i in range(2626)],  # must be stripped
        }]}), encoding="utf-8")
        result = list(asyncio.run(
            handle_read_resource(AnyUrl("trinity://memories/topics.json"))))
        basin = json.loads(result[0].content)["basins"][0]
        assert "centroid" not in basin, "768-float centroid leaked into the served resource"
        assert "prompt_ids" not in basin, "opaque prompt_ids index leaked into the served resource"
        # The signal the resource EXISTS to deliver must survive untouched:
        assert basin["label"] == "floor-plan engine"
        assert basin["representatives"] == ["how do I render the floor plan?"]
        assert basin["top_terms"] == ["floor", "plan", "engine"]
        assert basin["size"] == 42
        assert result[0].mime_type == "application/json"

    def test_picks_resource_strips_basin_centroid_keeps_fingerprint(self, isolated_home):
        """picks.json rules carry basin_centroid (768 floats, drop it) AND
        centroid_embedder (a short fingerprint string, KEEP it — it tells the
        agent which embedding space the rule lives in)."""
        from pydantic import AnyUrl
        from trinity_local.mcp_server import handle_read_resource
        scoreboard = isolated_home / "scoreboard"
        scoreboard.mkdir(parents=True, exist_ok=True)
        (scoreboard / "picks.json").write_text(json.dumps({
            "architecture_decision": {
                "basin_id": "b07",
                "routing_rule": "prefer claude",
                "basin_centroid": [0.02 * i for i in range(768)],  # strip
                "centroid_embedder": "nomic-ai/modernbert-embed-base:768",  # keep
                "trust_score": {"value": 0.8},
            }
        }), encoding="utf-8")
        result = list(asyncio.run(
            handle_read_resource(AnyUrl("trinity://scoreboard/picks.json"))))
        rule = json.loads(result[0].content)["architecture_decision"]
        assert "basin_centroid" not in rule, "768-float basin_centroid leaked into served picks.json"
        assert rule["centroid_embedder"] == "nomic-ai/modernbert-embed-base:768", (
            "centroid_embedder fingerprint (a short string, not a vector) was "
            "wrongly stripped — it's informative provenance the agent should see."
        )
        assert rule["routing_rule"] == "prefer claude"

    def test_picks_resource_canonicalizes_web_era_routing_slugs(self, isolated_home):
        """The get_picks/ask TOOLS canonicalize web-era slugs on read
        (cortex.load_routing_patterns, v1.7.166), but the picks.json RESOURCE reads
        the RAW file — which keeps the historical chatgpt/claude_ai/gemini slugs
        until the next consolidate. Without folding them, the resource hands an
        agent `primary: "chatgpt"` (not a provider it can dispatch to) while the
        tool returns `"codex"` — inconsistent + unactionable (found 2026-06-01).
        The resource must fold BOTH the routing-DECISION fields AND the
        provider-KEYED scoreboard dicts (winner_distribution / successful_prompts /
        failure_modes) to match the tool — an agent reading the resource at
        handshake must never see a key it can't dispatch to. Only the free-text
        `reason` prose keeps its web-era history."""
        from pydantic import AnyUrl
        from trinity_local.mcp_server import handle_read_resource
        scoreboard = isolated_home / "scoreboard"
        scoreboard.mkdir(parents=True, exist_ok=True)
        (scoreboard / "picks.json").write_text(json.dumps({
            "answer_synthesis": {
                "basin_id": "answer_synthesis",
                # chatgpt AND codex both present → must merge into one codex bucket.
                # Dyadic fractions so the merged sum is exact (no float wobble).
                "winner_distribution": {"chatgpt": 0.5, "codex": 0.25, "claude": 0.25},
                "successful_prompts": {"chatgpt": ["p1"], "codex": ["p2"]},
                "failure_modes": {"chatgpt": "verbose"},
                "routing_rule": {
                    "primary": "chatgpt", "challenger": "claude_ai",
                    "reason": "chatgpt wins by …",  # prose keeps history
                    "subroutes": [{"if_keywords": ["x"], "prefer": "gemini"}],
                },
            }
        }), encoding="utf-8")
        served = json.loads(list(asyncio.run(
            handle_read_resource(AnyUrl("trinity://scoreboard/picks.json"))))[0].content)
        node = served["answer_synthesis"]
        rr = node["routing_rule"]
        assert rr["primary"] == "codex"          # chatgpt -> codex (dispatchable)
        assert rr["challenger"] == "claude"      # claude_ai -> claude
        assert rr["subroutes"][0]["prefer"] == "antigravity"  # gemini -> antigravity
        assert "chatgpt" in rr["reason"]         # free-text prose keeps its history
        # Provider-keyed dicts fold + MERGE colliding slugs (chatgpt+codex -> codex).
        assert node["winner_distribution"] == {"codex": 0.75, "claude": 0.25}
        assert set(node["successful_prompts"]["codex"]) == {"p1", "p2"}
        assert "chatgpt" not in node["successful_prompts"]
        assert node["failure_modes"] == {"codex": "verbose"}

    def test_projection_helper_only_strips_real_vectors(self):
        """_is_numeric_vector must catch 768-float centroids but NOT short
        numeric lists (top_terms weights, [x, y] pairs) or bool flag lists —
        over-stripping would eat legitimate small numeric fields."""
        from trinity_local.mcp_server import _is_numeric_vector, _project_json_for_agent
        assert _is_numeric_vector([0.1] * 16) is True       # at the floor
        assert _is_numeric_vector([0.1] * 768) is True      # the real case
        assert _is_numeric_vector([1, 2, 3]) is False       # short list survives
        assert _is_numeric_vector([True] * 20) is False     # bool list is not a vector
        assert _is_numeric_vector(["a", "b"]) is False      # strings survive
        # nested projection keeps short numeric fields, drops the long vector:
        out = _project_json_for_agent(
            {"weights": [1, 2, 3], "vec": [0.0] * 32, "label": "x"})
        assert out == {"weights": [1, 2, 3], "label": "x"}


class TestReadResourceColdInstall:
    """When the file doesn't exist yet, read_resource MUST return a
    stub with an actionable next-step (run `trinity-local dream`)
    rather than 404. This is what makes Trinity useful out of the
    box: the agent reads the stub, sees the suggested action, and
    can surface it to the user."""

    def test_lens_md_returns_actionable_stub_when_missing(self, isolated_home):
        from pydantic import AnyUrl
        from trinity_local.mcp_server import handle_read_resource
        result = list(asyncio.run(
            handle_read_resource(AnyUrl("trinity://memories/lens.md"))))
        content = result[0].content
        # Stub must name the resource (so the agent knows WHICH was empty),
        # the action to populate it, AND the on-disk path (for debugging).
        assert "Trinity Lens" in content, "stub must include the resource name"
        assert "trinity-local dream" in content, (
            "stub must include the actionable command to populate this resource"
        )
        assert "trinity://memories/lens.md" in content, "stub must include the resource URI"
        # The cold-install stub is a markdown how-to message regardless of the
        # resource's declared type — it must read as markdown, not e.g. the
        # JSON type a populated picks.json would carry.
        assert result[0].mime_type == "text/markdown"

    def test_unknown_uri_raises(self, isolated_home):
        from pydantic import AnyUrl
        from trinity_local.mcp_server import handle_read_resource
        with pytest.raises(ValueError, match="Unknown Trinity resource"):
            asyncio.run(handle_read_resource(AnyUrl("trinity://not-a-thing")))


class TestResourceCatalogReflectsTrinityHome:
    """When TRINITY_HOME changes (test isolation), the path_func
    closures must resolve to the NEW home. Without this, tests
    leak into the real ~/.trinity/ and pollute the user's state."""

    def test_paths_resolve_under_test_home(self, isolated_home):
        from trinity_local.mcp_server import _resource_catalog
        catalog = _resource_catalog()
        for uri, _name, _desc, _mime, path_func in catalog:
            path = path_func()
            # Every resource path must be under the isolated test home,
            # never under the real $HOME/.trinity/.
            assert str(path).startswith(str(isolated_home)), (
                f"Resource {uri} resolved to {path} which is OUTSIDE the "
                f"isolated home {isolated_home}. The path_func closure is "
                f"capturing the wrong trinity_home() — likely evaluated at "
                f"import time instead of call time."
            )


class TestCorruptedResourceReadDegradesGracefully:
    """A non-UTF8 memory/scoreboard file (disk corruption / bad encoding) must
    NOT make read_resource raise — over the real protocol that surfaces as an
    McpError that breaks the agent's resource read. Serve the readable bytes
    with U+FFFD replacements instead — the SAME "serve it, don't 500" philosophy
    the handler's malformed-JSON branch already uses. Found 2026-06-01 via a real
    stdio read_resource on a non-UTF8 core.md/topics.json (raised McpError before
    the fix). Sibling of the v1.7.202 _read_memory_contents decode guard, one
    file over (mcp_server.py vs memory_viewer.py)."""

    def test_non_utf8_markdown_resource_served_not_raised(self, populated_home):
        from pydantic import AnyUrl
        from trinity_local.mcp_server import handle_read_resource
        (populated_home / "core.md").write_bytes(b"# Core \xff\xfe still here")
        result = list(asyncio.run(
            handle_read_resource(AnyUrl("trinity://memories/core.md"))))
        # readable parts preserved, garbled bytes → U+FFFD, no raise
        assert "Core" in result[0].content
        assert "�" in result[0].content
        assert result[0].mime_type == "text/markdown"

    def test_non_utf8_json_resource_served_not_raised(self, populated_home):
        from pydantic import AnyUrl
        from trinity_local.mcp_server import handle_read_resource
        (populated_home / "memories" / "topics.json").write_bytes(b"\xff\xfe\x00 nope")
        # Must not raise; the projection no-ops on the non-JSON garble and the
        # readable bytes are served.
        result = list(asyncio.run(
            handle_read_resource(AnyUrl("trinity://memories/topics.json"))))
        assert "�" in result[0].content
