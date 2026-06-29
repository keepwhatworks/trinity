"""Lens-discovery pipeline for `trinity-local lens-build`.

Implements the 5-stage pipeline (Stages 0–4). The Option C basins-as-
post-filter shape (Stages 1+4) was ratified by council
council_70eaf228d7753074; the Stage 0 turn-pair-gap addition by
council_e7560934cb1f1d72:

- turn_pairs.py: REFRAME/COMPRESSION/REDIRECT/SHARPENING extraction
  via ONE batch chairman call gated by deterministic post-validators
  (Stage 0; added per council_e7560934)
- basins.py: numpy k-means topology over PromptNode embeddings (Stage 1)
- decisions.py: chairman-driven decision extraction (Stage 2)
- pair_mining.py: 3-member council + JSON-verifier contract + basin
  post-filter that makes topology evidence load-bearing (Stages 3+4)

Drift instrument was explicitly rejected as topic-shift-not-value-shift
metaphor; centroids stay around only as basin tags for the post-filter.
"""

from __future__ import annotations
