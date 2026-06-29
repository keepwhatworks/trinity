"""Model-launch detection (#218) — the local-first signal that a new model
shipped, so Trinity can nudge the user to score it against their taste.

The whole celebration loop is **detect → notify → eval → eval-card**. This
module is the *detect* half. There is no server: the canonical current model
per provider ships in ``data/models.json`` and rides Trinity releases (the
user receives a bump via ``trinity-local update``). ``detect_new_models()``
diffs that manifest against the model the user *last evaluated* — recorded as
``target_model`` on each eval run (authoritative since v1.7.40 made the
recorded model equal the dispatched one). A provider whose current model
hasn't been scored yet is a "score it against YOUR taste" opportunity — the
viral, lab-impossible artifact.

The *notify* half surfaces these in ``trinity-local status`` + the launchpad
(no new CLI verb — the surface stays collapsed to lens/council). The *eval*
half is the existing ``eval-run --target <name>`` (now alias-friendly via
``resolve_provider_alias``). The *card* half is the existing eval-card.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


def models_manifest_path() -> Path:
    """The bundled canonical-model manifest (``data/models.json``)."""
    return Path(__file__).resolve().parent / "data" / "models.json"


def load_models_manifest() -> dict:
    """Read the manifest. Returns ``{}`` on any read/parse failure — detection
    degrades to 'no new models' rather than crashing a status call."""
    try:
        return json.loads(models_manifest_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def current_models() -> dict[str, dict]:
    """``{slug: {model, display, released, whats_new}}`` from the manifest."""
    manifest = load_models_manifest()
    models = manifest.get("models")
    return models if isinstance(models, dict) else {}


def _norm_model_id(s: str | None) -> str:
    """Normalize a model-identity string for equality only: casefold + collapse
    internal whitespace. The manifest (``current_models``) and the recorded
    eval ``target_model`` can disagree on case for the same model+effort — e.g.
    ``"Gemini 3.1 Pro (high)"`` vs ``"Gemini 3.1 Pro (High)"``. An exact-match
    diff would false-fire the 'score this new model' nudge for a model the user
    already scored. Display strings are never altered — this is comparison-only."""
    return " ".join((s or "").split()).casefold()


def _latest_evaluated_models() -> dict[str, str]:
    """Map ``{slug: target_model}`` for the most-recent eval run per provider.

    Reads ``~/.trinity/evals/results/*.json`` directly (skinny — only the two
    fields needed). A provider with no run is simply absent from the map."""
    from .evals.builder import results_dir

    out: dict[str, tuple[float, str]] = {}  # slug -> (mtime, model)
    rdir = results_dir()
    if not rdir.exists():
        return {}
    for f in rdir.glob("*.json"):
        try:
            mtime = f.stat().st_mtime
            raw = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                continue
        except (OSError, ValueError):
            continue
        slug = raw.get("target_provider")
        model = raw.get("target_model")
        if not slug or not model:
            continue
        prev = out.get(slug)
        if prev is None or mtime > prev[0]:
            out[slug] = (mtime, model)
    return {slug: model for slug, (_, model) in out.items()}


@dataclass
class NewModelEvent:
    """A provider whose current canonical model hasn't been scored against the
    user's taste yet — the trigger for the celebration nudge."""

    slug: str
    model: str
    display: str
    released: str
    whats_new: str
    last_evaluated: str | None  # the model the user last scored, or None

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "model": self.model,
            "display": self.display,
            "released": self.released,
            "whats_new": self.whats_new,
            "last_evaluated": self.last_evaluated,
        }

    def nudge(self) -> str:
        """The one-line celebration + call to action."""
        verb = "is out" if self.last_evaluated is None else "shipped"
        return (
            f"🎉 {self.display} {verb} — you haven't scored it against your "
            f"taste yet. Run: trinity-local eval-run --target {self.slug}"
        )


def detect_new_models() -> list[NewModelEvent]:
    """Providers whose current manifest model differs from the model the user
    last evaluated (or was never evaluated). Sorted by release date, newest
    first. Empty when every provider's current model has already been scored —
    so it's safe to call unconditionally from status/launchpad."""
    evaluated = _latest_evaluated_models()
    events: list[NewModelEvent] = []
    for slug, info in current_models().items():
        model = (info.get("model") or "").strip()
        if not model:
            continue
        last = evaluated.get(slug)
        if last is not None and _norm_model_id(last) == _norm_model_id(model):
            continue  # current model already scored — no nudge (case/space-insensitive)
        events.append(
            NewModelEvent(
                slug=slug,
                model=model,
                display=(info.get("display") or model),
                released=(info.get("released") or ""),
                whats_new=(info.get("whats_new") or ""),
                last_evaluated=last,
            )
        )
    # Release date DESC, slug as a stable tie-break so two models released on
    # the same date render in a deterministic order (reverse=True puts the slug
    # descending too — the direction is immaterial, only that it's stable).
    events.sort(key=lambda e: (e.released, e.slug), reverse=True)
    return events
