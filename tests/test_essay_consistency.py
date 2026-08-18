"""Essay-corpus consistency ratchet (2026-07-07).

The founder's standing bar: the only direction essay quality may move is UP.
Prose quality can't be tested, but every mechanical way an edit degrades the
corpus CAN be — and each check below pins something the full manual audit of
2026-07-07 verified true across all essays. An edit that breaks one of these
reds the build before it reaches a reader.

Companion skill (how to edit without tripping these):
.claude/skills/essay-discipline/SKILL.md
"""
from __future__ import annotations

import pathlib
import re

DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"
ARTICLES = DOCS / "articles"


def _essays() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8")
            for p in sorted(ARTICLES.glob("*.html"))}


class TestStructuralAnatomy:
    """Every essay carries the full template anatomy — a standalone-styled or
    half-converted upload (the raw-draft shape both founder essays arrived in)
    must not reach the site."""

    def test_every_essay_has_the_template_anatomy(self):
        for name, t in _essays().items():
            assert f'articles/{name}' in t, f"{name}: canonical URL missing/wrong"
            assert '../style.css' in t, f"{name}: not on the shared stylesheet (standalone-styled upload?)"
            assert '<style>' not in t, f"{name}: inline <style> block — converted uploads must drop their own CSS"
            assert 'class="topbar"' in t, f"{name}: site topbar missing"
            assert 'trinity-callout' in t, f"{name}: Trinity callout missing"
            assert 'class="references"' in t, f"{name}: lineage/references block missing"
            assert re.search(r'class="meta">Essay · [A-Z][a-z]{2} \d{1,2}, \d{4} · [^<]+</div>', t), \
                f"{name}: essay meta must carry date + its principle line (the ten-principle spine, 2026-07-07)"

    def test_every_referenced_image_exists(self):
        for name, t in _essays().items():
            for m in re.finditer(r'(?:og:image" content="https://keepwhatworks\.com/articles/|<img src=")(img/[^"]+)"', t):
                assert (ARTICLES / m.group(1)).exists(), \
                    f"{name}: references {m.group(1)} which does not exist on disk"

    def test_hero_and_og_image_agree(self):
        """An essay with a hero must advertise it to social cards and vice
        versa — the half-wired state (hero without og:image, or og:image
        pointing at art the page doesn't show) ships broken previews."""
        for name, t in _essays().items():
            has_hero = '<figure class="hero">' in t
            has_og = 'property="og:image"' in t
            assert has_hero == has_og, (
                f"{name}: hero figure and og:image disagree "
                f"(hero={has_hero}, og:image={has_og})"
            )


class TestSiteIntegration:
    def test_every_essay_has_an_index_card(self):
        idx = (DOCS / "index.html").read_text(encoding="utf-8")
        for name in _essays():
            assert f'href="articles/{name}"' in idx, f"{name}: no index card"

    def test_every_essay_is_in_the_sitemap(self):
        sm = (DOCS / "sitemap.xml").read_text(encoding="utf-8")
        for name in _essays():
            assert f"articles/{name}" in sm, f"{name}: missing from sitemap.xml (the canary class)"

    def test_index_card_dates_and_titles_match_the_essays(self):
        idx = (DOCS / "index.html").read_text(encoding="utf-8")
        cards = re.findall(
            r'article-card" href="articles/([^"]+)">\s*<div class="meta">([^<]+)</div>\s*<h3>([^<]+)</h3>',
            idx)
        essays = _essays()
        for href, meta, title in cards:
            t = essays.get(href)
            assert t is not None, f"index card points at missing essay {href}"
            edate = re.search(r'class="meta">Essay · ([^<]+)</div>', t).group(1).split("·")[0].strip()
            etitle = re.search(r'<h1>([^<]+)</h1>', t).group(1)
            assert meta.split("·")[0].strip() == edate, \
                f"{href}: card date {meta.split('·')[0].strip()!r} != essay date {edate!r}"
            assert title == etitle, f"{href}: card title {title!r} != essay h1 {etitle!r}"

    def test_internal_cross_links_resolve(self):
        essays = _essays()
        for name, t in essays.items():
            for m in re.finditer(r'href="([a-z0-9-]+\.html)"', t):
                assert m.group(1) in essays, \
                    f"{name}: links to {m.group(1)} which does not exist"


class TestStandaloneRule:
    """The 2026-07-07 sweep: essays stand alone. No corpus-counting, no
    'this site' framing that dates the piece and assumes the reader arrived
    through the front door. you-are-the-specimen is the ALLOWLISTED
    exception — its thesis IS turning the site's walls on their builder
    (founder-authored framing, adjudicated 2026-07-07)."""

    ALLOWLIST = {"you-are-the-specimen.html"}
    BANNED = [
        r"this (?:whole )?site has",
        r"oldest claim on this site",
        r"circling for \w+ essays",
        r"\b(?:eight|nine|ten|eleven|twelve) essays\b",
        r"as I (?:put it|argued) (?:elsewhere|in)",
        r"in a previous essay",
        r"an earlier essay",
    ]

    def test_no_corpus_dependent_framing(self):
        for name, t in _essays().items():
            if name in self.ALLOWLIST:
                continue
            prose = re.sub(r"<[^>]+>", "", t)
            for pat in self.BANNED:
                m = re.search(pat, prose, re.I)
                assert not m, (
                    f"{name}: corpus-dependent framing {m.group(0)!r} — essays must "
                    "stand alone (keep the idea, cut the pointer; see the "
                    "essay-discipline skill)"
                )


class TestBodyPurity:
    """Founder rules (2026-07-08): essays are generic principles for future
    apps — the product lives ONLY in the trinity-callout; formal research
    lives ONLY in the references blocks (lineage/appendix), woven clauses in
    prose stay but never as links."""

    def _body(self, t):
        body = t[t.index("</header>"):]
        if '<section class="references"' in body:
            body = body[:body.index('<section class="references"')]
        body = re.sub(r'<div class="trinity-callout".*', "", body, flags=re.S)
        return body

    def test_bodies_are_product_free(self):
        for name, t in _essays().items():
            prose = re.sub(r"<[^>]+>", " ", self._body(t))
            # "the Builder's Trinity" (grit/curiosity/simplicity) is the
            # Becoming essay's own concept, not the product — exempt.
            prose = prose.replace("Builder's\n      Trinity", "").replace("Builder's Trinity", "")
            assert "Trinity" not in prose, (
                f"{name}: 'Trinity' in body prose — the product lives only in "
                "the callout; essays stand as generic principles"
            )

    def test_research_links_live_only_in_references(self):
        for name, t in _essays().items():
            ext = re.findall(r'href="(https?://[^"]+)"', self._body(t))
            assert not ext, (
                f"{name}: external link in body prose ({ext[0][:50]}…) — formal "
                "citations belong in the lineage/appendix blocks"
            )


class TestClaimHygiene:
    """Copy never outruns measurement — the essay-side edge of the
    measured-claims ledger (trinity-discipline skill)."""

    def test_dead_claims_stay_dead(self):
        """'In your voice' died with the generation null (16/30, p=0.43,
        2026-07-05). It must not creep back into any essay's prose."""
        for name, t in _essays().items():
            prose = re.sub(r"<[^>]+>", "", t)
            assert "in your voice" not in prose.lower(), (
                f"{name}: resurrects the dead generation claim — the palate "
                "chooses (measured); it does not speak (measured null)"
            )

    def test_external_links_are_https_and_annotated(self):
        """Every external link opens safely (target+noopener) — the pattern
        the whole corpus follows; a bare externallink is an edit smell."""
        for name, t in _essays().items():
            for m in re.finditer(r'<a href="(https?://[^"]+)"([^>]*)>', t):
                url, attrs = m.groups()
                if "keepwhatworks.com" in url:
                    continue
                assert 'rel="noopener"' in attrs, \
                    f"{name}: external link missing rel=noopener → {url[:60]}"


class TestPrinciples:
    """The masthead (founder-authored 2026-07-07): the axiom bare at top,
    nine because-derivations, shape 1-2-2-2-2-1, last word 'free'. The card
    order IS the principles order — deliberate, not chronological."""

    HEADLINES = [
        "Design, don&rsquo;t predict.",
        "Find errors, not goals.",
        "Loop, don&rsquo;t ask.",
        "Build the affordance, not the policy.",
        "Pull, don&rsquo;t push.",
        "Anchor fast proxies to slow truths.",
        "Build for endurance, not speed.",
        "Oscillate locally, stabilize globally.",
        "Ask what survived, not what changed.",
        "Measure the shape, not the assumptions.",
        "Judge with veracity, not ferocity.",
        "Free your attention to learn fast, not to slow down.",
    ]
    # 2026-07-08: with Discovery admitted the corpus is 11 essays, so 11 lines
    # restores 1:1 — "Pull, don't push" returns as Gravity's own line, beside
    # "Build the affordance" as the structure couplet (the same wall from
    # opposite directions: Affordance removes resistance in the steady state,
    # Gravity engineers attraction at the threshold). Anchor is now the solo
    # pivot at center. Shape 1-2-2-1-2-2-1.
    # Amendment 2026-07-08: "Pull, don't push" DEMOTED from line to statute —
    # its clause lives in line 4's because; the full statute in Design the
    # Affordance; the application in Gravity. "Oscillate locally, stabilize
    # globally" admitted (passed the admission test: the chain had no
    # exploration organ; changes what you build; arrives with #184's numbers).

    # Amendment 2026-08-18: "Ask what survived, not what changed" admitted
    # (passed the admission test: the chain had chambers but no selection
    # criterion for what the chambers yield; changes what you keep; slots
    # after Oscillate as its payoff clause). Corpus is 12 essays, 1:1 holds.
    def test_masthead_carries_all_ten_in_order(self):
        idx = (DOCS / "index.html").read_text(encoding="utf-8")
        assert 'class="principles"' in idx, "the masthead section is gone"
        pos = -1
        for h in self.HEADLINES:
            i = idx.find(h)
            assert i > pos, f"principles headline out of order or missing: {h!r}"
            pos = i

    def test_becauses_have_no_terminal_periods(self):
        """Founder typography spec: no terminal periods on the because-lines."""
        idx = (DOCS / "index.html").read_text(encoding="utf-8")
        for m in re.finditer(r'class="principles-because[^"]*">([^<]+)</p>', idx):
            line = m.group(1).strip()
            assert not line.endswith("."), f"because-line grew a period: …{line[-60:]!r}"
            assert line.startswith("because"), f"because-line lost its because: {line[:40]!r}"

    def test_eleven_lines_one_per_essay(self):
        """1:1 restored 2026-07-08 (amended 2026-08-18): 12 lines, 12 essays, each line its own
        owner. No line shares an essay; no essay lacks a line."""
        idx = (DOCS / "index.html").read_text(encoding="utf-8")
        i = idx.index('class="principles"'); j = idx.index("</section>", i)
        links = re.findall(r'href="articles/([a-z0-9-]+\.html)"', idx[i:j])
        assert len(links) == len(set(links)) == 12, \
            f"principles must be 12 unique essay links (1:1), got {len(links)}"
        assert set(links) == set(_essays().keys()), \
            "every essay owns exactly one line and vice versa"

    def test_every_principles_line_links_to_its_owner_essay(self):
        """Invisible provenance links (2026-07-07): each line is an <a> to the
        essay where it was earned — the principles is the derived layer, the
        essays are its transcripts. Links must resolve."""
        idx = (DOCS / "index.html").read_text(encoding="utf-8")
        i = idx.index('class="principles"'); j = idx.index("</section>", i)
        block = idx[i:j]
        links = re.findall(r'href="articles/([a-z0-9-]+\.html)"', block)
        assert len(links) == 12, f"principles must carry exactly 12 essay links, got {len(links)}"
        for name in links:
            assert (ARTICLES / name).exists(), f"principles links to missing essay {name}"

    def test_card_order_matches_the_principles(self):
        idx = (DOCS / "index.html").read_text(encoding="utf-8")
        cards = re.findall(r'article-card" href="articles/([a-z0-9-]+)\.html"', idx)
        expected = ["architecture-of-becoming", "utopia-is-a-mechanism", "ai-native-way",
                    "design-the-affordance", "gravity-of-becoming", "coupling-problem",
                    "architecture-of-endurance", "architecture-of-discovery",
                    "causality-is-an-invariance", "you-are-the-specimen", "everyone-a-critic", "free-you-more"]
        assert cards == expected, f"card order diverged from the principles: {cards}"


class TestVoice:
    """The em-dash budget (audited 2026-08-18): across the corpus the count
    per essay runs 6-20, with body prose near zero — the dashes live almost
    entirely in the lineage-list separators. The first machine-drafted essay
    arrived carrying 41, twice the corpus max, and sailed through a green
    suite because this rule lived only in the essay-discipline skill (prose).
    A checkable rule left as prose gets ignored; this compiles it. Ratchet
    direction is down: the ceiling pins the audited max and never rises."""

    EM_DASH_CEILING = 20

    def test_em_dash_budget(self):
        for name, t in _essays().items():
            count = t.count("&mdash;") + t.count("\u2014")
            assert count <= self.EM_DASH_CEILING, (
                f"{name}: {count} em-dashes exceeds the corpus ceiling of "
                f"{self.EM_DASH_CEILING} — rewrite into the house moves "
                "(periods and fragments, colons, parentheses); dashes belong "
                "in the lineage separators, not the prose"
            )


class TestTheStoryTracksThePrinciples:
    """The boat page retells the principles, and nothing coupled it to them.

    It said "ten" for a month after the count moved, because the number lived in
    seven places on that page plus the index link and in none of them was it
    derived. This is the coupling: the spelled count follows the real one.
    """

    NUM = {10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen"}

    def _count(self) -> int:
        """Count distinct essay links, not class names.

        Counting `class="principles-line"` reads 11, because the closing
        principle carries a COMPOUND class (`principles-line principles-close`)
        that exact-string matching skips. Links are the same measure the 1:1
        guard above uses, and they cannot be fooled by markup shape.
        """
        idx = (DOCS / "index.html").read_text()
        i = idx.index('class="principles"')
        j = idx.index("</section>", i)
        return len(set(re.findall(r'href="articles/([^"]+)"', idx[i:j])))

    def test_the_story_spells_the_real_number(self):
        n = self._count()
        story = (DOCS / "the-little-boat-that-learned.html").read_text()
        word = self.NUM[n]
        for phrase in (f"{word} principles", f"{word} laws"):
            assert phrase in story, (
                f"There are {n} principles but the story does not say "
                f"{phrase!r}. The count appears in the meta description, both "
                "social cards, the JSON-LD, the meta line and the closing note "
                "— update every one."
            )
        wrong = [w for k, w in self.NUM.items() if k != n and
                 (f"{w} principles" in story or f"{w} laws" in story)]
        assert not wrong, f"the story still says {wrong} somewhere; the real count is {n}"

    def test_the_index_link_spells_the_real_number(self):
        n = self._count()
        idx = (DOCS / "index.html").read_text()
        assert f"the same {self.NUM[n]} as a story" in idx, (
            f"the link to the story must say {self.NUM[n]}, matching the {n} principles"
        )

    def test_every_principle_actually_appears_in_the_story(self):
        """The page claims every line is one of the principles.

        It was two short when this guard was written — 'Pull, don't push' and
        'Measure the shape' had no beat — so the claim held in one direction
        only. A count alone would never have caught that.
        """
        story = (DOCS / "the-little-boat-that-learned.html").read_text()
        body = story[story.index("<h1>"):story.index("<hr>")]
        beats = [b for b in re.findall(
            r"<(?:p|blockquote)[^>]*>(.*?)</(?:p|blockquote)>", body, re.S) if b.strip()]
        # One beat per principle at minimum; the story opens with a setup line
        # and spends several beats on the axiom, so it can only ever exceed it.
        assert len(beats) >= self._count(), (
            f"{len(beats)} story beats for {self._count()} principles — the story "
            "cannot be carrying them all."
        )
