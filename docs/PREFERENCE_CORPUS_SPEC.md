---
class: historical
---

# Trinity Substrate Spec (v2)

> **Current status (2026-05-30): historical design record.** The moves /
> AGENTS.md substrate described below was retired on 2026-05-27 in #184 after
> real-data validation showed the gate was structurally dormant. The live repo
> keeps the preference-corpus pieces that survived: `council_outcomes/*.json`,
> the unified `~/.trinity/me/preference_acts.jsonl` ledger, `eval-build` /
> `eval-run`, and lens delivery through MCP Resources. For current runtime
> architecture, read [`how-trinity-works.md`](how-trinity-works.md).
>
> **Status:** v2 draft — locked May 26, 2026. Supersedes v1 (May 14, 2026). v2 reframes the spec around **two existing standards (SKILL.md, MCP) plus the Trinity-specific extension fields that let cross-provider preference signal flow through them**. v1's JSON Schemas survive as the council-outcome / rejection-signal / eval-set surface; the spec now names what they extend rather than claiming to be its own standard.
>
> **The v2 idea:** skill marketplaces are flat directories of curated procedures. Trinity is a hierarchical substrate with eval-gated promotion between layers, exposed via standards that already ship in 30+ agent tools. The interop story is "we don't invent — we stack."

## Why v2 exists

v1 (May 2026) was an honest first cut: three Trinity-shaped JSON schemas (council outcome, rejection signal, eval set), CC0-licensed, with a "please adopt this" appeal. The problem v1 didn't solve: it left Trinity standing alongside skill marketplaces (anthropics/skills, claude-skills, agentskills.io) and MCP resources — as if those were unrelated surfaces.

**They're not unrelated.** They're the same substrate at different abstraction levels:

| CoALA layer ([Sumers et al. 2023](https://arxiv.org/abs/2309.02427)) | Trinity name | Existing standard | What it stores |
|---|---|---|---|
| Episodic | **transcripts** | (no shared standard yet — MessageList shapes diverge) | Raw conversations across CLIs + web chats |
| Semantic | **memories** | MCP Resources (read-only context) | Extracted facts/observations from dream |
| Procedural | **moves** | [SKILL.md](https://agentskills.io/specification) | Crystallized procedures — repeated successful patterns |
| Preferential | **lens** | MCP Resources (`trinity://memories/lens.md`) | The geometry across all of the above — tensions, basins, vocabulary |

Each layer is a compression of the one below. Promotion between layers is what `dream` already does for memories. v2 names the missing piece: **eval-gated promotion** of memories→moves.

**On composition:** [MACLA](https://github.com/S-Forouzandeh/MACLA-LLM-Agents-AAMAS-2026-Conference)'s `MetaProcedure` shape (one level of move-composition into "playbooks") is architecturally clean, but it requires ≥20–30 moves to operate over. Trinity ships moves first; the playbooks layer is **deferred to post-launch** until the move surface is dense enough for compositions to mean anything.

**On AGENTS.md:** considered as a Trinity-exposed surface and dropped 2026-05-26. AGENTS.md is project-scoped by convention (./AGENTS.md in the user's repo); every harness that reads it also reads MCP Resources; exposing a user-home AGENTS.md was ceremonial. The lens flows via `trinity://memories/lens.md`.

## The four-layer substrate

### Layer 1: Transcripts (episodic)

Raw conversations indexed at `~/.trinity/prompts/prompt_nodes.jsonl`. Sourced from CLI sessions (`~/.claude/`, `~/.codex/`, `~/.gemini/`) and browser-captured chats (claude.ai, chatgpt.com, gemini.google.com). No shared standard exists for this; Trinity reads each provider's native format.

### Layer 2: Memories (semantic)

Distilled observations + facts written at `~/.trinity/memories/`. Files: `core.md` (one-paragraph identity), `lens.md` (paired tensions), `topics.json` (subject basins), `vocabulary.md` (anchors + homonyms).

**Standard adopted: MCP Resources.** Trinity exposes each memory file as a readable MCP Resource (URI: `trinity://memories/<file>`). Any MCP-compatible harness lists these at session start and the agent reads on demand. This is the v2 leverage point: a Claude Code session with Trinity loaded sees the lens BEFORE the user types anything.

### Layer 3a: Moves (atomic procedural)

> **Note on naming:** Trinity uses **moves** as the atomic internal name — the user's repeatable plays that survived eval. Externally Trinity exports them as **SKILL.md** files for interop. "Move" emphasizes the action-shape; "skill" emphasizes the marketplace-shape. Both refer to the same artifact.

Located at `~/.trinity/moves/<slug>/SKILL.md`. Directory contract follows agentskills.io exactly — YAML frontmatter (required: `name`, `description`) + markdown body, optional `tools` whitelist, no internal routing prescribed.

**Trinity-specific frontmatter extensions** (custom fields are explicitly allowed by the SKILL.md spec and ignored by tools that don't recognize them):

```yaml
---
name: tighten-after-bullet-list
description: |
  When the model returns a 5+ item bulleted list, compress to a 2-3
  sentence paragraph capturing only the load-bearing claims.

# Provenance — which rejection-pairs justified this move
trinity_promoted_from: ["r_001", "r_042", "r_117"]
trinity_basin_id: b03
trinity_promoted_at: "2026-05-23T14:22:00Z"
trinity_demoted_at: null

# Bayesian success tracking (lifted from MACLA Procedure dataclass)
# alpha = successful applications (chairman picked the response using
# this move). beta = unsuccessful applications. posterior =
# alpha / (alpha + beta). Trinity uses Beta-Binomial conjugate prior
# for incremental updates — no rolling-window bookkeeping needed.
trinity_alpha: 8                 # successes
trinity_beta: 2                  # failures
trinity_posterior: 0.80          # alpha / (alpha + beta)
trinity_execution_count: 10      # alpha + beta

# Scores from the four-tier gate at promotion time + last re-eval
trinity_t1_lexical_score: 0.42   # Jaccard at most-recent re-eval
trinity_t2_embedding_score: 0.81 # cosine vs basin centroid
trinity_t3_chairman_score: 0.84  # most recent T3 chairman eval
trinity_eval_baseline: 0.79      # personal best on T3; promotion floor

# Contrastive learning (lifted from MACLA ContrastiveContext) — which
# basins/contexts the move fired correctly vs incorrectly in. Lets
# users debug why a move drifted (basin shifted? corpus shifted?
# real-world drifted?).
trinity_success_contexts: ["b03", "b07"]   # basin ids
trinity_failure_contexts: []
trinity_generalizability_score: 0.6        # how many distinct basins the move applies in

# How many distinct lens-tensions this move addresses. Highly-
# specific moves are 1-2; broadly-useful moves are 5+.
trinity_lens_tensions_addressed: 2
---

(markdown body — the actual move's procedure)
```

### Layer 3b: Playbooks (deferred to post-launch)

Composed playbooks (sequential combinations of moves) are deferred until the move surface is dense enough (≥20–30 active moves) for compositions to mean anything. MACLA's `MetaProcedure` shape (`sub_procedures` list + `composition_policy: {type: sequential}` + cascading demotion when constituent moves demote) is the target architecture when this lands; the spec section reserved here will be filled in then. Until that point, Trinity ships a flat moves layer only.

### The promotion gate (the spec's load-bearing claim)

The four-tier Bayesian gate (defined below in "Eval-gated promotion") runs against **moves** at promotion time. Demotion uses the same tiers — a move demotes when T1/T2 drift below thresholds OR T3 re-eval drops below baseline for ≥3 consecutive cycles OR T4 posterior (`alpha/(alpha+beta)`) drifts below baseline.

### Layer 4: Lens (preferential)

The geometry across all of the above. Lives at `~/.trinity/memories/` already; v2 also exposes it via **AGENTS.md** at `~/.trinity/AGENTS.md`.

**Standard adopted: AGENTS.md.** Codex CLI, Cursor, and Cline all read AGENTS.md natively. Trinity generates AGENTS.md from the lens on every `dream` cycle so non-MCP harnesses see the lens too. AGENTS.md is the human-readable + agent-readable union; the MCP Resources path is the structured one.

**AGENTS.md structure** (generated by `dream`):

```markdown
---
trinity_generated_from: lens.md
trinity_lens_version: <hash>
trinity_generated_at: <iso>
---

# Own your taste

(One-paragraph identity from core.md.)

## Tensions you've consistently picked

- **Pole A vs Pole B** — when X happens, you privilege A. Evidence:
  [r_001], [r_042].
- ...

## Vocabulary

- `term` — you mean X, not Y. See [memory_xyz].

## Active moves

- [tighten-after-bullet-list] — see `moves/tighten-after-bullet-list/`.
- ...
```

## The three adopted standards

| Standard | Trinity layer | URI / location | Trinity extension fields |
|---|---|---|---|
| **[SKILL.md](https://agentskills.io/specification)** | Moves (procedural) | `~/.trinity/moves/<slug>/SKILL.md` | `trinity_lens_score`, `trinity_promoted_from`, `trinity_eval_baseline`, `trinity_eval_runs`, `trinity_basin_id`, `trinity_promoted_at`, `trinity_demoted_at` |
| **[AGENTS.md](https://github.com/agentsmd/agents.md)** | Lens (preferential) | `~/.trinity/AGENTS.md` | `trinity_generated_from`, `trinity_lens_version`, `trinity_generated_at` |
| **[MCP](https://modelcontextprotocol.io)** Resources + Prompts + Tools | All layers | `trinity://memories/<file>`, `trinity://moves/<slug>`, `trinity://prompts/<name>` | Native — uses MCP primitives without extension |

All three are markdown + YAML frontmatter throughout. Custom frontmatter fields are explicitly allowed by SKILL.md and ignored by tools that don't recognize them. Reading a Trinity-shaped SKILL.md in Cursor (which doesn't know about `trinity_lens_score`) still works — the score is just hidden.

## Eval-gated promotion — the Bayesian gate (what sets it apart)

This is the load-bearing architectural claim. Skill marketplaces ship flat directories of curated procedures. Trinity ships a hierarchy with **Bayesian-gated promotion** between layers: cheap priors filter most candidates, expensive likelihood evaluates the survivors, and live posterior continuously re-validates against actual usage.

The framing matters because the alternative ("always run the chairman eval on every candidate") is either ceremonial (skipped because it would make dream weekly) or computationally prohibitive (10+ minutes per cycle, ~200 corpus items needed for statistical power). Tiering scales sub-linearly with candidate volume because each tier filters the next.

### The promotion loop

1. **`dream` proposes candidates** at the end of each cycle:
   - New memories (extracted facts)
   - Memories→moves promotions (repeated successful patterns)
   - Lens updates (new tensions, vocab terms, basin reshapings)
2. **Each candidate passes through the four-tier gate** below.
3. **Trinity commits surviving candidates** to their respective layers.
4. **Trinity logs rejected candidates** at `~/.trinity/dream_rejections.jsonl` with `why_rejected` + which tier rejected them. Rejection is not failure — it's the gate working.
5. **Active moves continuously update their posterior** via T4 below; drift triggers demotion.

### The four-tier Bayesian gate

Read top-to-bottom as **prior → likelihood → posterior**. Each tier runs only on candidates surviving the previous one.

| Tier | Role | Cost | What it checks | Pass criterion |
|---|---|---|---|---|
| **T1** prior (lexical) | Cheap structural similarity | ~1ms | n-gram overlap with accepted patterns in the candidate's claimed basin | Jaccard ≥ 0.3 vs ≥ 3 accepted patterns |
| **T2** prior (embedding) | Geometric basin membership | ~10ms | Cosine similarity of candidate's embedding vs basin centroid | cos ≥ 0.7 to ≥ 1 accepted basin |
| **T3** likelihood (chairman) | Score against personalized rejection corpus | ~30s | Chairman runs the candidate against rejection items; scores via lens | score ≥ `trinity_eval_baseline` (set at first promotion; tracks personal best) |
| **T4** posterior (live A/B) | Real-world utility | free (side-effect of usage) | When this move was active in a chairman call, did the chairman pick the response that used it? Beta-Binomial conjugate prior tracking via `alpha` / `beta` frontmatter fields. | `alpha / (alpha + beta)` ≥ baseline |

**T1+T2 are the prior** — cheap, structural, runs on 100% of candidates. Filters ~70-80%.
**T3 is the high-fidelity likelihood** — expensive, definitive, runs on the surviving ~20-30%.
**T4 is the empirical posterior** — free as a side-effect of councils, continuously re-validates active moves against actual usage.

T4 uses a **Beta-Binomial conjugate prior** (lifted from MACLA's `Procedure` dataclass): every council where a move was active increments `alpha` (chairman picked the response using the move) or `beta` (chairman picked a different member). The posterior is `alpha / (alpha + beta)` — incremental, mathematically clean, no rolling-window bookkeeping. This is materially better than the rolling-N-call shape an earlier version of this spec proposed: rolling windows lose history at the edges and introduce window-size as a hyperparameter; the conjugate prior accumulates evidence forever and converges naturally.

A move that survives all four is: structurally similar to your taste **and** scores well on your rejection corpus **and** demonstrably improves your actual council outcomes. A move that fails any tier loses its "active" status with the failure reason recorded.

### Why T4 is something no one else has

Live A/B requires a verdict on each call. Most systems need explicit user thumbs-up/thumbs-down to learn — which is exactly the feedback loop Trinity retired (chairman pick replaced ratings).

**Trinity's T4 doesn't need an explicit user verdict** because the chairman's pick — conditioned on the lens — IS the verdict. When a move was active during a council, T4 reads whether the chairman picked the response that used the move. The lens makes this a personalized signal; the cross-provider corpus makes it non-replicable for any tool that can't read across labs. **No flat skill marketplace can build T4 without a cross-provider preference signal.**

### Demotion (the regression guard)

Every dream cycle re-runs T1 + T2 against all currently-active moves and updates the T4 rolling win-rate. A move gets demoted when:

- **T1/T2 drift**: candidate's basin shifted under it (the user's accepted patterns moved); OR
- **T3 re-eval failure**: re-run T3 on a sampled corpus item shows score < baseline for ≥ 3 consecutive cycles; OR
- **T4 posterior drift**: rolling win-rate drops below baseline over the last N calls.

Demotion writes:

- `trinity_demoted_at` in the SKILL.md frontmatter
- `trinity_demoted_by_tier` naming which tier triggered the demotion
- `~/.trinity/dream_demotions.jsonl` entry with `why_demoted` + the failing scores
- The move file moves to `~/.trinity/moves/archive/<slug>/`

The archive is preserved so demotion is debuggable. A user inspecting "why did this move stop firing?" can read the archive entry and see whether it was structural drift (T1/T2), corpus-evidence drift (T3), or real-world drift (T4) — each tier's signal tells a different story.

## Layer-by-layer JSON schemas

The v1 schemas survive — they describe the council-outcome / rejection-signal / eval-set surface. v2 adds two new schemas for the move + dream-rejection surface.

| File | Schema | Purpose |
|------|--------|---------|
| `~/.trinity/council_outcomes/council_<hash>.json` | [`council_outcome.schema.json`](../schemas/council_outcome.schema.json) | One multi-model run + chairman synthesis. Unchanged from v1. |
| `~/.trinity/me/preference_acts.jsonl` | [`rejection_signal.schema.json`](../schemas/rejection_signal.schema.json) | Preference acts: rejections (trigger=model_miss) + decisions (trigger=self_expressed), unified ledger (#209). |
| `~/.trinity/evals/eval_<hash>.json` | [`eval_set.schema.json`](../schemas/eval_set.schema.json) | Personalized eval suite. Unchanged from v1. |
| `~/.trinity/moves/<slug>/SKILL.md` | SKILL.md spec + Trinity move-extension frontmatter ([`move.schema.json`](../schemas/move.schema.json)) | Promoted moves. New in v2. |
| `~/.trinity/dream_rejections.jsonl` | [`dream_rejection.schema.json`](../schemas/dream_rejection.schema.json) | Candidates the eval gate rejected, with `why_rejected`. New in v2. |
| `~/.trinity/dream_demotions.jsonl` | [`dream_demotion.schema.json`](../schemas/dream_demotion.schema.json) | Moves that drifted below baseline and got archived. New in v2. |

## The four implicit-rejection signal types (unchanged from v1)

Trinity mines four rejection labels from turn-pair gaps:

| Type | What the user did | Validator (in `me/turn_pairs.py`) |
|------|-------------------|-----------------------------------|
| `REFRAME` | Substituted a different frame entirely | Substituted frame must persist into next user turn |
| `COMPRESSION` | Wanted the answer shorter | User text word count ≤ model text / 10 |
| `REDIRECT` | Wanted a structurally different output | Model text was multi-part (numbered/bulleted/multi-sentence) |
| `SHARPENING` | Wanted more precision on same topic | User text shares ≥2 keywords with model text |

These labels carry through into move provenance — a move's `trinity_promoted_from` list points at the rejection-signal IDs that justified the promotion, so a user inspecting a move can see "this exists because of these 3 specific rejections."

## Obsidian compatibility (a consequence, not a feature)

Once Trinity is markdown + YAML frontmatter + wikilinks throughout:

1. **Frontmatter** — already native to Obsidian; Trinity already writes it.
2. **Wikilinks** — Trinity uses `[[r_001]]` to point moves back at the rejection-pairs that justified them; Obsidian renders them as a graph. Same syntax, two readers.
3. **Tags** — `#preference/reframe`, `#move/active`, `#move/archived` for typed categorization. Obsidian filters by them; Trinity's lens-discovery groups by them.
4. **Point Obsidian at `~/.trinity/`** — or point Trinity at an existing Obsidian vault via the `--vault` config flag. The substrate IS the vault.

Obsidian compatibility isn't a feature line — it's a consequence of the standards work. Users who want the graph view get it for free.

## Adopting this in your tool

1. **Read Trinity's lens.** Either through MCP Resources (`trinity://memories/lens.md`) or by reading `~/.trinity/AGENTS.md` directly. Both formats stay in sync via `dream`.
2. **Contribute rejection signal.** Append a record (trigger=model_miss) to `~/.trinity/me/preference_acts.jsonl` following [`rejection_signal.schema.json`](../schemas/rejection_signal.schema.json). Trinity's lens-build picks it up on the next cycle.
3. **Contribute candidate moves.** Drop a SKILL.md file into `~/.trinity/moves/<your-slug>/`. Trinity's next dream cycle runs it through the eval gate; if it passes, it goes live. If not, it lands in `dream_rejections.jsonl` with the `why_rejected`.
4. **Read promoted moves.** Either via MCP Resources or by listing `~/.trinity/moves/`. The SKILL.md format works in Claude Code, Cursor, Codex CLI, Cline, Continue, and 25+ other agent tools natively.
5. **Don't invent a parallel format.** If your tool needs a field Trinity doesn't have, add a `<your_tool>_*` frontmatter field. SKILL.md's spec explicitly supports custom fields.

## Reference implementation

Trinity Local itself is the reference. The canonical writers:

- `src/trinity_local/council_runtime.py::save_council_outcome` — produces `council_outcomes/*.json`
- `src/trinity_local/me/preference_acts.py::save_preference_acts` — produces `preference_acts.jsonl`
- `src/trinity_local/evals/builder.py::save_eval_set` — produces `evals/*.json`
- `src/trinity_local/moves/*` — produces `moves/<slug>/SKILL.md` (NEW in v2)
- `src/trinity_local/dream.py::propose_and_gate` — runs the promotion loop (extended in v2)

Schemas are validated against real on-disk data in `tests/test_preference_corpus_schemas.py`. If a Trinity write doesn't match its schema, that test fails — so the schema can't drift from the reference implementation silently.

## Versioning

v1 (May 14, 2026) — council outcome / rejection signal / eval set schemas; structural lock.
v2 (May 26, 2026) — two-standard adoption (SKILL.md + MCP); adds move + dream-rejection + dream-demotion schemas; Bayesian four-tier gate; Beta-Binomial conjugate prior for T4. AGENTS.md considered + dropped (project-scoped by convention; MCP Resources covers the use case). Playbooks layer deferred post-launch (needs ≥20-30 moves to operate over). v1 schemas survive unchanged; v2 is purely additive.

v2 is structural; backward-compatible additions don't bump the version, removals would.

## Prior art lifted into v2

- **[CoALA / Sumers et al. 2023](https://arxiv.org/abs/2309.02427)** — four-layer cognitive memory taxonomy (working/episodic/semantic/procedural). Trinity drops "working" (per-MCP-session, ephemeral) and adds "preferential" as the lens layer.
- **[MACLA / S. Forouzandeh, AAMAS 2026](https://github.com/S-Forouzandeh/MACLA-LLM-Agents-AAMAS-2026-Conference)** — the `Procedure` dataclass shape (alpha/beta Beta-Binomial tracker, success/failure contrastive contexts, generalizability score). Trinity adopts the math + structure; not the agent loop. MACLA's `MetaProcedure` composition shape is the reserved target for the post-launch playbooks layer.
- **[SKILL.md / agentskills.io](https://agentskills.io/specification)** — exact file format for moves. Trinity adds extension frontmatter fields; the core spec is unchanged.
- **[MCP Resources + Tools](https://modelcontextprotocol.io)** — primitive substrate. Trinity uses both (v1 used only Tools; v2 lit up Resources for the lens-flow path). Prompts primitive considered + dropped — Tools already covers the run_council use case; Prompts would be a brag-line rather than a new capability.

## Distribution

> **Status (current):** Trinity is **not on PyPI** yet — there is no `pip install
> trinity-local` or `uvx trinity-local` package (both 404). The canonical install
> today is the curl bootstrap (`git clone` + shell wrappers in `~/.local/bin/`);
> see [`INSTALL-pip.md`](INSTALL-pip.md) and the README. The `uvx`/PyPI flow below
> is the **planned** distribution for the PyPI-launch milestone, kept here as the
> design target — not a live path.

The canonical install is the one-line bootstrap:

```bash
curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh | bash
```

It clones the repo, installs the runtime deps into `~/.trinity/venv`, and registers
Trinity's MCP server in every harness it detects. `trinity-local install-mcp`
re-writes the per-harness configs by hand if the bootstrap missed one.

**Planned (PyPI-launch target):** ship via **[`uvx`](https://github.com/astral-sh/uv)**
(Astral's `uv`-based PyPI runner) as the canonical install path — isolated venv on
first run, auto-version-check on subsequent invocations, no global Python pollution.
The substrate at `~/.trinity/` is the engine's only durable state, so `uvx` upgrades
would reach it without migration steps. At that point one-line registration per
harness becomes `claude mcp add trinity-local --command "uvx trinity-local --mcp"`
(and the equivalent `[mcp_servers.trinity-local]` TOML for Codex), with a `pip
install trinity-local` secondary path. Until the package is published, none of these
PyPI paths resolve — use the curl bootstrap above.

The Chrome extension (capture + dispatch) ships independently via `chrome://extensions` Load Unpacked from `browser-extension/`. The extension generates harness-specific paste-in snippets (Phase A), so the user doesn't have to know which harness they're in.

## License

The schemas + this spec are CC0 — adopt them, fork them, embed them in commercial products. The point is interop, not protection.
